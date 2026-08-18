import math

import pytest

from football_intelligence.timebase import frame_to_ms, ms_to_timestamp


@pytest.mark.parametrize(
    ("frame_index", "fps", "expected_ms"),
    [
        (0, 25.0, 0),
        (1, 25.0, 40),
        (25, 25.0, 1000),
        (30, 29.97, 1001),
    ],
)
def test_frame_to_ms_uses_source_frame_time(frame_index, fps, expected_ms):
    assert frame_to_ms(frame_index, fps) == expected_ms


@pytest.mark.parametrize("fps", [0.0, -1.0, math.inf, math.nan])
def test_frame_to_ms_rejects_invalid_fps(fps):
    with pytest.raises(ValueError, match="fps must be finite and positive"):
        frame_to_ms(1, fps)


def test_frame_to_ms_rejects_negative_frame_index():
    with pytest.raises(ValueError, match="frame_index must be non-negative"):
        frame_to_ms(-1, 25.0)


@pytest.mark.parametrize(
    ("timestamp_ms", "expected"),
    [
        (0, "00:00:00.000"),
        (62_345, "00:01:02.345"),
        (3_723_004, "01:02:03.004"),
    ],
)
def test_ms_to_timestamp_formats_hours_minutes_seconds(timestamp_ms, expected):
    assert ms_to_timestamp(timestamp_ms) == expected

