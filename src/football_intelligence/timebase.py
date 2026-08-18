"""Conversions for the canonical integer-millisecond media timebase."""

from math import isfinite


def frame_to_ms(frame_index: int, fps: float) -> int:
    """Return the presentation time for a zero-based frame index."""
    if frame_index < 0:
        raise ValueError("frame_index must be non-negative")
    if not isfinite(fps) or fps <= 0:
        raise ValueError("fps must be finite and positive")
    return int(round(frame_index * 1000 / fps))


def ms_to_timestamp(timestamp_ms: int) -> str:
    """Format milliseconds as an unambiguous HH:MM:SS.mmm timestamp."""
    hours, remainder = divmod(timestamp_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"

