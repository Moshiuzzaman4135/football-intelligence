import numpy as np

from football_intelligence.domain import BoundingBox, TrackObservation
from football_intelligence.overlay import draw_overlay


def test_draw_overlay_adds_box_label_and_timestamp_without_resizing():
    frame = np.zeros((120, 200, 3), dtype=np.uint8)
    track = TrackObservation(
        track_id=7,
        object_class="player",
        bbox=BoundingBox(x1=20, y1=20, x2=60, y2=100),
        confidence=0.84,
        timestamp_ms=1000,
        frame_index=25,
    )

    rendered = draw_overlay(frame, [track], [], timestamp_ms=1000, trails={7: [(40, 90)]})

    assert rendered.shape == frame.shape
    assert np.count_nonzero(rendered) > 0
    assert np.count_nonzero(frame) == 0
