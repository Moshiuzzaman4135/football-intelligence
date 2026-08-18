"""Event clip and thumbnail extraction from annotated output video.

Mirrors the clip-generation idea from the reference ``vision-relay`` service,
but stays synchronous and single-host (no Celery) for the MVP: a short
H.264+AAC clip is cut around an event's start timestamp from the already
annotated video, and a lightweight PNG thumbnail is captured at the same point.
Both are cached on disk under ``data/clips`` and served through FastAPI.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

DEFAULT_CONTEXT_BEFORE_MS = 2_000
DEFAULT_CLIP_DURATION_MS = 8_000
DEFAULT_THUMBNAIL_WIDTH = 320


class ClipError(RuntimeError):
    pass


def _run_ffmpeg(arguments: list[str], *, timeout: int, description: str) -> None:
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as error:
        stderr = (error.stderr or error.stdout or "").strip()
        raise ClipError(f"{description} failed: {stderr[:300]}") from error
    except subprocess.TimeoutExpired as error:
        raise ClipError(f"{description} timed out after {timeout}s") from error


def build_event_clip(
    source: Path,
    output: Path,
    *,
    start_ms: int,
    context_before_ms: int = DEFAULT_CONTEXT_BEFORE_MS,
    duration_ms: int = DEFAULT_CLIP_DURATION_MS,
) -> Path:
    """Cut a short annotated clip starting just before ``start_ms``.

    The seek uses ``-ss`` before ``-i`` for a fast keyframe-accurate start, and
    re-encodes to a browser-playable H.264/AAC faststart MP4.
    """
    if not source.is_file():
        raise ClipError(f"source video is missing: {source}")
    if duration_ms <= 0:
        raise ClipError("clip duration must be positive")
    if context_before_ms < 0:
        raise ClipError("clip context must be non-negative")
    if start_ms < 0:
        raise ClipError("clip start must be non-negative")
    output.parent.mkdir(parents=True, exist_ok=True)
    start_seconds = max(0.0, (start_ms - context_before_ms) / 1000)
    duration_seconds = duration_ms / 1000
    temporary = output.with_suffix(".partial.mp4")
    temporary.unlink(missing_ok=True)
    try:
        _run_ffmpeg(
            [
                "-ss",
                f"{start_seconds:.3f}",
                "-i",
                str(source),
                "-t",
                f"{duration_seconds:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(temporary),
            ],
            timeout=180,
            description="clip generation",
        )
    except ClipError:
        temporary.unlink(missing_ok=True)
        raise
    if not temporary.is_file():
        temporary.unlink(missing_ok=True)
        raise ClipError(f"clip generation produced no output for {source}")
    temporary.replace(output)
    return output


def build_event_thumbnail(
    source: Path,
    output: Path,
    *,
    at_ms: int,
    width: int = DEFAULT_THUMBNAIL_WIDTH,
) -> Path:
    """Capture a single downscaled PNG frame at ``at_ms``."""
    if not source.is_file():
        raise ClipError(f"source video is missing: {source}")
    if at_ms < 0:
        raise ClipError("thumbnail timestamp must be non-negative")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".partial.png")
    temporary.unlink(missing_ok=True)
    try:
        _run_ffmpeg(
            [
                "-ss",
                f"{max(0.0, at_ms / 1000):.3f}",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                f"scale={width}:-2",
                str(temporary),
            ],
            timeout=60,
            description="thumbnail generation",
        )
    except ClipError:
        temporary.unlink(missing_ok=True)
        raise
    if not temporary.is_file():
        temporary.unlink(missing_ok=True)
        raise ClipError(f"thumbnail generation produced no output for {source}")
    temporary.replace(output)
    return output
