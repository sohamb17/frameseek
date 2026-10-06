"""FFmpeg/ffprobe wrappers.

All timestamps produced here are on the *playback asset's* presentation timeline,
in milliseconds, with the container start time subtracted. Audio and frames are
extracted from the playback asset itself (not the original), so a result's
start_ms is exactly where the browser <video> element should seek.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from typing import Callable

ProgressFn = Callable[[float], None]

BROWSER_SAFE_VIDEO = {"h264"}
BROWSER_SAFE_AUDIO = {"aac", "mp3"}


class MediaError(Exception):
    """A user-facing problem with the input file (not a bug)."""


@dataclass
class Probe:
    raw: dict
    format_name: str
    duration_s: float
    start_time_s: float
    video_codec: str | None
    pix_fmt: str | None
    width: int | None
    height: int | None
    video_start_s: float
    audio_codec: str | None
    audio_start_s: float

    @property
    def has_audio(self) -> bool:
        return self.audio_codec is not None

    def browser_safe(self) -> bool:
        container_ok = any(n in self.format_name for n in ("mp4", "mov"))
        return (
            container_ok
            and self.video_codec in BROWSER_SAFE_VIDEO
            and self.pix_fmt in ("yuv420p", "yuvj420p")
            and (self.audio_codec is None or self.audio_codec in BROWSER_SAFE_AUDIO)
            and (self.width or 0) <= 1920
        )

    def summary(self) -> dict:
        return {
            "format": self.format_name,
            "duration_s": round(self.duration_s, 3),
            "video_codec": self.video_codec,
            "pix_fmt": self.pix_fmt,
            "width": self.width,
            "height": self.height,
            "audio_codec": self.audio_codec,
            "start_time_s": self.start_time_s,
            "browser_safe": self.browser_safe(),
        }


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def probe(path: pathlib.Path) -> Probe:
    """Probe the actual streams; never trust the file extension."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise MediaError(f"ffprobe could not read the file: {proc.stderr.strip()[:300]}")
    raw = json.loads(proc.stdout)
    fmt = raw.get("format", {})
    v = next((s for s in raw.get("streams", []) if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in raw.get("streams", []) if s.get("codec_type") == "audio"), None)
    if v is None:
        raise MediaError("no video stream found")
    duration = _f(fmt.get("duration")) or _f(v.get("duration"))
    if duration <= 0:
        raise MediaError("could not determine duration")
    start = _f(fmt.get("start_time"))
    return Probe(
        raw=raw,
        format_name=fmt.get("format_name", ""),
        duration_s=duration,
        start_time_s=start,
        video_codec=v.get("codec_name"),
        pix_fmt=v.get("pix_fmt"),
        width=v.get("width"),
        height=v.get("height"),
        video_start_s=_f(v.get("start_time"), start),
        audio_codec=a.get("codec_name") if a else None,
        audio_start_s=_f(a.get("start_time"), start) if a else start,
    )


def decode_check(path: pathlib.Path, seconds: float = 15) -> list[str]:
    """Decode the first seconds of every stream and return decoder error lines.

    ffprobe only reads headers, so a truncated or damaged file can pass it; this catches
    obviously broken inputs before minutes of processing are spent on them.
    """
    proc = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-t", str(seconds), "-i", str(path), "-f", "null", "-"],
                          capture_output=True, text=True, timeout=300)
    lines = [l for l in proc.stderr.splitlines() if l.strip()]
    if proc.returncode != 0 and not lines:
        lines = [f"ffmpeg exited with {proc.returncode}"]
    return lines


def _run_with_progress(cmd: list[str], duration_s: float, progress: ProgressFn | None) -> None:
    """Run ffmpeg with -progress on stdout and report the fraction completed.

    stderr goes to a temp file, not a pipe: a damaged input can make FFmpeg print thousands of
    decoder errors, and an unread stderr pipe would fill up and deadlock both processes.
    A watchdog kills FFmpeg if it runs far longer than the media duration.
    """
    full = cmd[:1] + ["-nostdin", "-progress", "pipe:1", "-nostats"] + cmd[1:]
    with tempfile.TemporaryFile(mode="w+") as err:
        proc = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=err, text=True)
        watchdog = threading.Timer(max(300.0, duration_s * 10), proc.kill)
        watchdog.start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if line.startswith("out_time_us=") and progress and duration_s > 0:
                    try:
                        progress(min(1.0, int(line.split("=")[1]) / 1e6 / duration_s))
                    except ValueError:
                        pass
            proc.wait()
        finally:
            watchdog.cancel()
        if proc.returncode != 0:
            err.seek(0)
            raise MediaError(f"ffmpeg failed: {err.read().strip()[-400:]}")


def transcode_playback(src: pathlib.Path, dst: pathlib.Path, max_width: int, duration_s: float,
                       progress: ProgressFn | None = None) -> None:
    """Create a browser-safe H.264/AAC MP4 with the moov atom up front."""
    tmp = dst.with_name(dst.stem + ".tmp.mp4")
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", f"scale='min({max_width},iw)':-2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-movflags", "+faststart", str(tmp),
    ]
    _run_with_progress(cmd, duration_s, progress)
    tmp.replace(dst)


def extract_audio(src: pathlib.Path, dst: pathlib.Path) -> None:
    """16 kHz mono PCM, the input format Whisper expects."""
    tmp = dst.with_name(dst.stem + ".tmp.wav")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(tmp)],
        check=True, capture_output=True, timeout=3600,
    )
    tmp.replace(dst)


_PTS_RE = re.compile(r"pts_time:\s*([0-9.]+)")


def sample_frames(src: pathlib.Path, out_dir: pathlib.Path, interval_s: float, scene_threshold: float,
                  max_width: int, video_start_s: float, duration_s: float,
                  progress: ProgressFn | None = None) -> list[int]:
    """Sample one frame every `interval_s` seconds plus frames at scene changes.

    Returns each written frame's presentation timestamp in ms. Timestamps come from
    FFmpeg's showinfo filter (the real PTS), so variable-frame-rate inputs are handled
    correctly instead of assuming frame_number / fps.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.jpg"):
        old.unlink()
    select = (f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{interval_s})"
              f"+gt(scene\\,{scene_threshold})'")
    vf = f"{select},showinfo,scale='min({max_width},iw)':-2"
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "info", "-i", str(src), "-an", "-vf", vf,
           "-fps_mode", "vfr", "-q:v", "3", str(out_dir / "%05d.jpg")]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    watchdog = threading.Timer(max(300.0, duration_s * 10), proc.kill)
    watchdog.start()
    assert proc.stderr is not None
    times: list[int] = []
    tail: list[str] = []
    for line in proc.stderr:
        if "Parsed_showinfo" in line and "pts_time" in line:
            m = _PTS_RE.search(line)
            if m:
                t = float(m.group(1)) - video_start_s
                times.append(max(0, int(round(t * 1000))))
                if progress and duration_s > 0:
                    progress(min(1.0, t / duration_s))
        else:
            tail = (tail + [line])[-20:]
    proc.wait()
    watchdog.cancel()
    if proc.returncode != 0:
        raise MediaError("frame extraction failed: " + "".join(tail)[-400:])
    files = sorted(out_dir.glob("*.jpg"))
    if len(files) != len(times):
        raise MediaError(f"frame/timestamp mismatch: {len(files)} files vs {len(times)} timestamps")
    return times
