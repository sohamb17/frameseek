"""Speech recognition with faster-whisper (CTranslate2 Whisper)."""
from __future__ import annotations

import functools
import pathlib
import wave
from typing import Callable

import numpy as np

ProgressFn = Callable[[float], None]


@functools.lru_cache(maxsize=2)
def _model(name: str, revision: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel
    from huggingface_hub import snapshot_download

    # Download the pinned revision explicitly so the manifest's revision is what runs.
    path = snapshot_download(repo_id=name, revision=revision)
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    if device == "cpu" and compute_type == "float16":
        compute_type = "int8"
    return WhisperModel(path, device=device, compute_type=compute_type)


def load_wav(path: pathlib.Path) -> np.ndarray:
    """Read the 16 kHz mono PCM file written by media.extract_audio as float32 in [-1, 1].

    Passing samples directly avoids faster-whisper's PyAV decode path (and its version coupling).
    """
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def transcribe(audio: pathlib.Path, cfg: dict, duration_s: float, progress: ProgressFn | None = None) -> list[dict]:
    """Return raw ASR segments with word timestamps (seconds, relative to audio start)."""
    model = _model(cfg["model"], cfg["revision"], cfg.get("device", "auto"), cfg.get("compute_type", "int8"))
    segments, _info = model.transcribe(
        load_wav(audio),
        language=cfg.get("language", "en"),
        beam_size=cfg.get("beam_size", 5),
        vad_filter=cfg.get("vad_filter", True),
        word_timestamps=True,
        condition_on_previous_text=False,  # reduces repetition loops on long talks
    )
    out = []
    for seg in segments:  # generator: decoding happens while iterating
        out.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
            "avg_logprob": seg.avg_logprob,
            "no_speech_prob": seg.no_speech_prob,
            "words": [{"w": w.word.strip(), "s": w.start, "e": w.end, "p": round(w.probability, 3)}
                      for w in (seg.words or [])],
        })
        if progress and duration_s > 0:
            progress(min(1.0, seg.end / duration_s))
    return out


def release() -> None:
    """Drop the loaded model so the next stage (CLIP) does not stack on top of it in RAM."""
    import gc
    _model.cache_clear()
    gc.collect()
