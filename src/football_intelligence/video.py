"""Validated video probing and frame iteration."""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from football_intelligence.domain import VideoMetadata
from football_intelligence.timebase import frame_to_ms


class VideoOpenError(ValueError):
    pass


class VideoDecodeError(VideoOpenError):
    pass


@dataclass(frozen=True, slots=True)
class FramePacket:
    frame_index: int
    timestamp_ms: int
    frame: np.ndarray


def probe_video(path: str | Path) -> VideoMetadata:
    source = Path(path)
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise VideoOpenError(f"could not open video: {source}")
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if width <= 0 or height <= 0 or fps <= 0 or frame_count <= 0:
            raise VideoOpenError(f"video metadata is invalid: {source}")
        fourcc = int(capture.get(cv2.CAP_PROP_FOURCC))
        codec = "".join(chr((fourcc >> 8 * index) & 0xFF) for index in range(4)).strip()
        return VideoMetadata(
            source_path=str(source),
            width=width,
            height=height,
            fps=fps,
            frame_count=frame_count,
            duration_ms=int(round(frame_count * 1000 / fps)),
            codec=codec,
        )
    finally:
        capture.release()


def iter_frames(path: str | Path, stride: int = 1) -> Iterator[FramePacket]:
    metadata = probe_video(path)
    capture = cv2.VideoCapture(str(path))
    try:
        frame_index = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                if frame_index < metadata.frame_count:
                    raise VideoDecodeError(
                        f"video decode ended at frame {frame_index} of "
                        f"{metadata.frame_count}: {path}"
                    )
                break
            if frame_index % stride == 0:
                yield FramePacket(
                    frame_index=frame_index,
                    timestamp_ms=frame_to_ms(frame_index, metadata.fps),
                    frame=frame,
                )
            frame_index += 1
    finally:
        capture.release()
