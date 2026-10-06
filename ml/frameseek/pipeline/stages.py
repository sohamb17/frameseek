"""Ingestion stages.

Each stage reads artifacts written by earlier stages, writes its own artifacts to
storage, and returns a StageResult. The worker records a stage manifest (input hash,
config hash, tool/model versions, output checksums, timings) after every stage, and
skips a stage on retry when a manifest with matching config/code exists and its
outputs still verify. Nothing here touches the searchable tables except `index`,
which writes the whole new version and publishes it in one transaction.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
from PIL import Image

from .. import media
from ..config import settings
from ..storage import storage
from .segments import FrameObs, Word, build_segments

STAGES = ["probe", "playback", "extract", "asr", "ocr", "segment", "embed", "index"]

# Coarse states shown in the UI (blueprint: queued, extracting, transcribing/OCR, embedding, indexing, ready).
UI_STAGE = {
    "probe": "extracting", "playback": "extracting", "extract": "extracting",
    "asr": "transcribing", "ocr": "transcribing",
    "segment": "embedding", "embed": "embedding",
    "index": "indexing",
}


class LeaseLost(Exception):
    """Another attempt owns the job now; this worker must stop without writing."""


class PermanentError(Exception):
    """Retrying will not help (bad input, over the configured limits)."""


@dataclass
class StageResult:
    outputs: dict[str, str] = field(default_factory=dict)   # storage key -> sha256
    metrics: dict[str, Any] = field(default_factory=dict)
    input_hash: str = ""


@dataclass
class JobCtx:
    job_id: str
    video_id: str
    owner_id: str
    index_version: int
    attempt: int
    config: dict
    config_hash: str
    video: dict
    lost: threading.Event
    report: Callable[[str, float], None]

    @property
    def vkey(self) -> str:
        return f"videos/{self.video_id}"

    @property
    def key(self) -> str:
        return f"videos/{self.video_id}/v{self.index_version}"

    def check(self) -> None:
        if self.lost.is_set():
            raise LeaseLost(f"lease lost for job {self.job_id} attempt {self.attempt}")


def _h(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _write_json(key: str, obj: Any) -> str:
    data = json.dumps(obj, separators=(",", ":")).encode()
    storage().write_bytes(key, data)
    return hashlib.sha256(data).hexdigest()


def _read_json(key: str) -> Any:
    return json.loads(storage().read_bytes(key))


def _inject(stage: str) -> None:
    s = settings()
    if s.fail_stage == stage:
        raise RuntimeError(f"injected failure in stage '{stage}' (FRAMESEEK_FAIL_STAGE)")


def _maybe_slow(ctx: JobCtx, stage: str) -> None:
    if settings().slow_stage == stage:
        for _ in range(60):  # 30 s of interruptible work, for the kill-the-worker demo
            ctx.check()
            time.sleep(0.5)


# --------------------------------------------------------------------------- probe
def probe(ctx: JobCtx) -> StageResult:
    st = storage()
    src = st.path(ctx.video["original_key"])
    try:
        p = media.probe(src)
    except media.MediaError as e:
        raise PermanentError(str(e)) from e
    errors = media.decode_check(src)
    if len(errors) > 20:
        raise PermanentError(f"the file looks damaged ({len(errors)} decoder errors in the first 15 s, "
                             f"e.g. '{errors[0][:120]}'); re-export or re-download it")
    cap = ctx.config["media"]["max_duration_s"]
    if p.duration_s > cap:
        raise PermanentError(f"video is {p.duration_s / 60:.1f} min; the configured limit is {cap / 60:.0f} min")
    sha = _write_json(f"{ctx.vkey}/probe.json", p.raw)
    return StageResult(
        outputs={f"{ctx.vkey}/probe.json": sha},
        metrics={"probe": p.summary(), "decode_warnings": len(errors)},
        input_hash=ctx.video["content_hash"],
    )


# --------------------------------------------------------------------------- playback
def playback(ctx: JobCtx) -> StageResult:
    """Use the original when the browser can play it; otherwise derive an H.264/AAC MP4."""
    st = storage()
    src = st.path(ctx.video["original_key"])
    p = media.probe(src)
    if p.browser_safe():
        key, derived = ctx.video["original_key"], False
    else:
        key, derived = f"{ctx.vkey}/playback.mp4", True
        if not st.exists(key):
            media.transcode_playback(src, st.ensure_parent(key), ctx.config["media"]["playback_max_width"],
                                     p.duration_s, lambda f: ctx.report("playback", f))
    pp = media.probe(st.path(key))
    meta = {
        "playback_key": key,
        "derived": derived,
        "duration_ms": int(round((pp.duration_s) * 1000)),
        "video_start_s": pp.video_start_s - pp.start_time_s,
        "audio_start_s": pp.audio_start_s - pp.start_time_s,
        "has_audio": pp.has_audio,
        "width": pp.width,
        "height": pp.height,
        "original": p.summary(),
        "playback": pp.summary(),
    }
    sha = _write_json(f"{ctx.vkey}/playback.json", meta)
    return StageResult(outputs={f"{ctx.vkey}/playback.json": sha}, metrics=meta,
                       input_hash=ctx.video["content_hash"])


def _playback_meta(ctx: JobCtx) -> dict:
    return _read_json(f"{ctx.vkey}/playback.json")


# --------------------------------------------------------------------------- extract
def _small_gray(path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("L").resize((64, 36), Image.BILINEAR), dtype=np.float32) / 255.0


def extract(ctx: JobCtx) -> StageResult:
    st = storage()
    pm = _playback_meta(ctx)
    mcfg = ctx.config["media"]
    src = st.path(pm["playback_key"])
    outputs: dict[str, str] = {}
    t0 = time.time()
    if pm["has_audio"]:
        audio_key = f"{ctx.key}/audio.wav"
        media.extract_audio(src, st.ensure_parent(audio_key))
        outputs[audio_key] = st.sha256(audio_key)
    ctx.check()
    frames_dir = st.path(f"{ctx.key}/frames")
    duration_s = pm["duration_ms"] / 1000
    times = media.sample_frames(src, frames_dir, mcfg["frame_interval_s"], mcfg["scene_threshold"],
                                mcfg["frame_max_width"], pm["video_start_s"], duration_s,
                                lambda f: ctx.report("extract", f))
    ctx.check()
    thumbs_dir = st.path(f"{ctx.key}/thumbs")
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    prev = None
    for i, (path, ts) in enumerate(zip(sorted(frames_dir.glob("*.jpg")), times)):
        small = _small_gray(path)
        diff = float(np.abs(small - prev).mean()) if prev is not None else 1.0
        prev = small
        with Image.open(path) as im:
            w = mcfg["thumb_width"]
            th = im.convert("RGB").resize((w, max(1, int(im.height * w / im.width))), Image.BILINEAR)
            th.save(thumbs_dir / path.name, quality=82)
        frames.append({
            "idx": i, "ts_ms": ts,
            "image_key": f"{ctx.key}/frames/{path.name}",
            "thumb_key": f"{ctx.key}/thumbs/{path.name}",
            "diff": round(diff, 4),
            "is_transition": i == 0 or diff >= mcfg["transition_diff"],
        })
    if not frames:
        raise PermanentError("no frames could be decoded")
    poster = min(frames, key=lambda f: abs(f["ts_ms"] - 0.15 * pm["duration_ms"]))["thumb_key"]
    outputs[f"{ctx.key}/frames.json"] = _write_json(f"{ctx.key}/frames.json", {"frames": frames, "poster": poster})
    return StageResult(
        outputs=outputs,
        metrics={"n_frames": len(frames), "n_transitions": sum(f["is_transition"] for f in frames),
                 "has_audio": pm["has_audio"], "seconds": round(time.time() - t0, 2)},
        input_hash=_h(pm["playback_key"], str(pm["duration_ms"])),
    )


# --------------------------------------------------------------------------- asr
def asr(ctx: JobCtx) -> StageResult:
    from ..models import asr as asr_model

    _inject("asr")
    pm = _playback_meta(ctx)
    t0 = time.time()
    if not pm["has_audio"]:
        data = {"state": "no_audio", "segments": []}
    else:
        audio = storage().path(f"{ctx.key}/audio.wav")
        segs = asr_model.transcribe(audio, ctx.config["asr"], pm["duration_ms"] / 1000,
                                    lambda f: ctx.report("asr", f))
        offset = pm.get("audio_start_s", 0.0)
        for s in segs:
            s["start"] += offset
            s["end"] += offset
            for w in s["words"]:
                w["s"] += offset
                w["e"] += offset
        n_words = sum(len(s["words"]) for s in segs)
        data = {"state": "ok" if n_words else "no_speech", "segments": segs}
    if settings().low_memory:
        asr_model.release()
    _maybe_slow(ctx, "asr")
    key = f"{ctx.key}/asr.json"
    elapsed = time.time() - t0
    return StageResult(
        outputs={key: _write_json(key, data)},
        metrics={"state": data["state"], "n_segments": len(data["segments"]),
                 "n_words": sum(len(s["words"]) for s in data["segments"]),
                 "seconds": round(elapsed, 2),
                 "realtime_factor": round(elapsed / max(1e-6, pm["duration_ms"] / 1000), 3)},
        input_hash=_h(pm["playback_key"], "audio"),
    )


# --------------------------------------------------------------------------- ocr
def ocr(ctx: JobCtx) -> StageResult:
    from ..models.ocr import ocr_image

    st = storage()
    cfg = ctx.config["ocr"]
    frames = _read_json(f"{ctx.key}/frames.json")["frames"]
    results = []
    last = None          # (small image, result) of the last frame actually OCR'd
    n_run = 0
    t0 = time.time()
    for i, f in enumerate(frames):
        ctx.check()
        small = _small_gray(st.path(f["image_key"]))
        if last is not None and float(np.abs(small - last[0]).mean()) < cfg["reuse_below_diff"]:
            res = dict(last[1], reused=True)
        else:
            with Image.open(st.path(f["image_key"])) as im:
                res = ocr_image(im, cfg["min_word_conf"])
            res["reused"] = False
            last = (small, res)
            n_run += 1
        results.append({"idx": f["idx"], **res})
        ctx.report("ocr", (i + 1) / len(frames))
    key = f"{ctx.key}/ocr.json"
    with_text = sum(r["n_words"] >= cfg["min_words_for_coverage"] for r in results)
    return StageResult(
        outputs={key: _write_json(key, {"frames": results})},
        metrics={"frames": len(results), "ocr_runs": n_run, "frames_with_text": with_text,
                 "state": "ok" if with_text else "no_text", "seconds": round(time.time() - t0, 2)},
        input_hash=_h(*(f["image_key"] for f in frames)),
    )


# --------------------------------------------------------------------------- segment
def segment(ctx: JobCtx) -> StageResult:
    pm = _playback_meta(ctx)
    asr_data = _read_json(f"{ctx.key}/asr.json")
    ocr_data = {r["idx"]: r for r in _read_json(f"{ctx.key}/ocr.json")["frames"]}
    frames = _read_json(f"{ctx.key}/frames.json")["frames"]
    words = [Word(int(w["s"] * 1000), int(w["e"] * 1000), w["w"])
             for s in asr_data["segments"] for w in s["words"] if w["w"]]
    obs = [FrameObs(None, f["idx"], f["ts_ms"], ocr_data[f["idx"]]["text"], ocr_data[f["idx"]]["n_words"],
                    ocr_data[f["idx"]]["reused"]) for f in frames]
    scfg = ctx.config["segments"]
    segs = build_segments(pm["duration_ms"], words, obs, int(scfg["window_s"] * 1000), int(scfg["stride_s"] * 1000),
                          ctx.config["ocr"]["min_words_for_coverage"])
    key = f"{ctx.key}/segments.json"
    data = [s.__dict__ for s in segs]
    return StageResult(
        outputs={key: _write_json(key, {"segments": data})},
        metrics={"n_segments": len(segs),
                 "with_speech": sum(bool(s.transcript) for s in segs),
                 "with_ocr": sum(bool(s.ocr_text) for s in segs)},
        input_hash=_h(str(len(words)), str(len(obs))),
    )


# --------------------------------------------------------------------------- embed
def embed(ctx: JobCtx) -> StageResult:
    from ..models import clip, text

    _inject("embed")
    st = storage()
    segs = _read_json(f"{ctx.key}/segments.json")["segments"]
    frames = _read_json(f"{ctx.key}/frames.json")["frames"]
    t0 = time.time()
    tcfg, vcfg = ctx.config["text_encoder"], ctx.config["visual_encoder"]
    t_idx = [i for i, s in enumerate(segs) if s["transcript"].strip()]
    o_idx = [i for i, s in enumerate(segs) if s["ocr_text"].strip()]
    t_emb = text.encode([segs[i]["transcript"] for i in t_idx], tcfg)
    ctx.report("embed", 0.15)
    o_emb = text.encode([segs[i]["ocr_text"].replace("\n", ". ") for i in o_idx], tcfg)
    ctx.report("embed", 0.3)
    ctx.check()
    v_emb = clip.encode_images([st.path(f["image_key"]) for f in frames], vcfg, progress=lambda f: ctx.report("embed", 0.3 + 0.7 * f))
    if settings().low_memory:
        clip.release()
        text.release()
    _maybe_slow(ctx, "embed")
    key = f"{ctx.key}/embeddings.npz"
    path = st.ensure_parent(key)
    tmp = path.with_name("embeddings.tmp.npz")
    np.savez(tmp, t_idx=np.array(t_idx, dtype=np.int32), t_emb=t_emb, o_idx=np.array(o_idx, dtype=np.int32),
             o_emb=o_emb, v_emb=v_emb)
    tmp.replace(path)
    return StageResult(
        outputs={key: st.sha256(key)},
        metrics={"transcript_vectors": len(t_idx), "ocr_vectors": len(o_idx), "frame_vectors": len(frames),
                 "text_model": tcfg["model"], "visual_model": f"{vcfg['model']}/{vcfg['pretrained']}",
                 "seconds": round(time.time() - t0, 2)},
        input_hash=_h(st.sha256(f"{ctx.key}/segments.json"), st.sha256(f"{ctx.key}/frames.json")),
    )


STAGE_FUNCS: dict[str, Callable[[JobCtx], StageResult]] = {
    "probe": probe, "playback": playback, "extract": extract, "asr": asr,
    "ocr": ocr, "segment": segment, "embed": embed,
}
