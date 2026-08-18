"""Safe localization and validated FFmpeg media preparation for full matches."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from pydantic import BaseModel, Field

MAX_MATCH_DURATION_MS = 150 * 60 * 1000
_ALLOWED_CONTAINERS = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska"}


class MediaCancelled(RuntimeError):
    pass


class StreamingObjectStore(Protocol):
    def iter_object(self, object_key: str, chunk_size: int = 1024 * 1024): ...


class MediaProbe(BaseModel):
    path: str
    container: str
    video_codec: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    frame_count: int = Field(gt=0)
    frame_count_estimated: bool = False
    duration_ms: int = Field(gt=0)
    has_audio: bool
    pixel_format: str = ""
    video_start_ms: int = 0
    audio_codec: str | None = None
    audio_start_ms: int | None = None
    audio_duration_ms: int | None = None


def parse_same_bucket_uri(uri: str, bucket: str) -> str:
    parsed = urlsplit(uri)
    if parsed.scheme != "s3" or parsed.netloc != bucket:
        raise ValueError("source must use the configured S3 bucket")
    if parsed.query or parsed.fragment:
        raise ValueError("versioned or parameterized S3 source URIs are not accepted")
    key = unquote(parsed.path.lstrip("/"))
    path = PurePosixPath(key)
    if not key or path.is_absolute() or ".." in path.parts:
        raise ValueError("S3 source key must be an opaque relative key")
    return key


def localize_s3_source(
    object_store: StreamingObjectStore,
    uri: str,
    *,
    bucket: str,
    destination_dir: str | Path,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    key = parse_same_bucket_uri(uri, bucket)
    root = Path(destination_dir)
    root.mkdir(parents=True, exist_ok=True)
    suffix = PurePosixPath(key).suffix.lower()
    final_path = root / f"source{suffix}"
    partial_path = root / "source.partial"
    partial_path.unlink(missing_ok=True)
    try:
        with partial_path.open("xb") as target:
            for chunk in object_store.iter_object(key):
                if cancelled is not None and cancelled():
                    raise MediaCancelled("source localization cancelled")
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        partial_path.replace(final_path)
        _fsync_directory(root)
    except Exception:
        partial_path.unlink(missing_ok=True)
        raise
    return final_path


def probe_media(
    path: str | Path, *, cancelled: Callable[[], bool] | None = None
) -> MediaProbe:
    source = Path(path)
    result = run_media_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(source),
        ],
        cancelled=cancelled,
    )
    payload = json.loads(result.stdout)
    return _media_probe_from_payload(source, payload)


def _media_probe_from_payload(source: Path, payload: dict[str, object]) -> MediaProbe:
    streams = payload.get("streams", [])
    if not isinstance(streams, list):
        streams = []
    video = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    if video is None:
        raise ValueError("media does not contain a video stream")
    format_payload = payload.get("format", {})
    if not isinstance(format_payload, dict):
        format_payload = {}
    fps = _parse_rate(video.get("avg_frame_rate")) or _parse_rate(
        video.get("r_frame_rate")
    )
    reported_frame_count = _parse_positive_int(
        video.get("nb_read_frames")
    ) or _parse_positive_int(video.get("nb_frames"))
    frame_count = reported_frame_count
    duration = _parse_positive_float(video.get("duration")) or _parse_positive_float(
        format_payload.get("duration")
    )
    if duration <= 0 and frame_count > 0 and fps > 0:
        duration = frame_count / fps
    if frame_count <= 0 and duration > 0 and fps > 0:
        frame_count = max(1, round(duration * fps))
    if fps <= 0 and duration > 0 and frame_count > 0:
        fps = frame_count / duration
    if duration <= 0 or frame_count <= 0 or fps <= 0:
        raise ValueError("media timing metadata is invalid")
    audio = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"), None
    )
    return MediaProbe(
        path=str(source),
        container=str(format_payload.get("format_name", "")),
        video_codec=str(video.get("codec_name", "")),
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        fps=fps,
        frame_count=frame_count,
        frame_count_estimated=reported_frame_count == 0,
        duration_ms=max(1, round(duration * 1000)),
        has_audio=audio is not None,
        pixel_format=str(video.get("pix_fmt", "")),
        video_start_ms=round(_parse_nonnegative_float(video.get("start_time")) * 1000),
        audio_codec=str(audio.get("codec_name", "")) if audio else None,
        audio_start_ms=(
            round(_parse_nonnegative_float(audio.get("start_time")) * 1000)
            if audio
            else None
        ),
        audio_duration_ms=(
            round(
                (
                    _parse_positive_float(audio.get("duration"))
                    or _parse_positive_float(format_payload.get("duration"))
                )
                * 1000
            )
            if audio
            else None
        ),
    )


def validate_source_media(probe: MediaProbe) -> None:
    containers = set(probe.container.split(","))
    if not containers & _ALLOWED_CONTAINERS:
        raise ValueError(f"unsupported media container: {probe.container}")
    if probe.duration_ms > MAX_MATCH_DURATION_MS:
        raise ValueError("full-match source exceeds 150 minutes")


def build_proxy(
    source: str | Path,
    destination: str | Path,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    source_path = Path(source)
    destination_path = Path(destination)
    original = probe_media(source_path, cancelled=cancelled)
    validate_source_media(original)
    width, height = _bounded_geometry(original.width, original.height)
    target_fps = min(25.0, original.fps)
    temporary = destination_path.with_name(f"{destination_path.stem}.partial.mp4")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary.unlink(missing_ok=True)
    try:
        run_media_command(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(source_path),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-vf",
                f"scale={width}:{height},fps={target_fps:g}",
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
            cancelled=cancelled,
        )
        actual = probe_media(temporary, cancelled=cancelled)
        _validate_proxy(actual, original, width, height, target_fps)
        temporary.replace(destination_path)
        _fsync_directory(destination_path.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination_path


def _validate_proxy(
    actual: MediaProbe,
    original: MediaProbe,
    width: int,
    height: int,
    fps: float,
) -> None:
    if actual.video_codec != "h264":
        raise RuntimeError(f"proxy codec is not H.264: {actual.video_codec}")
    if actual.pixel_format != "yuv420p":
        raise RuntimeError(f"proxy pixel format is not yuv420p: {actual.pixel_format}")
    if (actual.width, actual.height) != (width, height):
        raise RuntimeError("proxy geometry does not match the bounded geometry")
    if abs(actual.fps - fps) > 0.01:
        raise RuntimeError("proxy FPS does not match the bounded frame rate")
    tolerance_ms = max(100, round(2000 / fps))
    if abs(actual.duration_ms - original.duration_ms) > tolerance_ms:
        raise RuntimeError("proxy duration changed outside tolerance")
    if original.has_audio and not actual.has_audio:
        raise RuntimeError("proxy lost the source audio stream")
    if original.has_audio and actual.audio_codec != "aac":
        raise RuntimeError("proxy audio is not AAC")


def _bounded_geometry(width: int, height: int) -> tuple[int, int]:
    scale = min(1.0, 1280 / width, 720 / height)
    bounded_width = max(2, int(width * scale) // 2 * 2)
    bounded_height = max(2, int(height * scale) // 2 * 2)
    return bounded_width, bounded_height


def _parse_rate(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        numerator, separator, denominator = value.partition("/")
        if separator:
            divisor = float(denominator)
            return float(numerator) / divisor if divisor else 0.0
        return float(value)
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _parse_positive_float(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


def _parse_nonnegative_float(value: object) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _parse_positive_int(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json_write(path: str | Path, payload: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(payload, output, separators=(",", ":"), sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def delete_file_durable(path: str | Path) -> None:
    target = Path(path)
    target.unlink(missing_ok=True)
    _fsync_directory(target.parent)


def run_media_command(
    command: list[str], *, cancelled: Callable[[], bool] | None = None
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.1)
            break
        except subprocess.TimeoutExpired as error:
            if cancelled is not None and cancelled():
                process.terminate()
                process.communicate()
                raise MediaCancelled("media command cancelled") from error
    completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if process.returncode:
        raise subprocess.CalledProcessError(
            process.returncode, command, output=stdout, stderr=stderr
        )
    return completed


def validate_full_decode(
    path: str | Path, *, cancelled: Callable[[], bool] | None = None
) -> None:
    completed = run_media_command(
        [
            "ffmpeg",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(path),
            "-f",
            "null",
            "-",
        ],
        cancelled=cancelled,
    )
    if completed.stderr.strip():
        raise RuntimeError(f"full decode diagnostics: {completed.stderr.strip()}")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
