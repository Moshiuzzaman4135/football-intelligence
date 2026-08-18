from pathlib import Path

import cv2

from football_intelligence.domain import BoundingBox, TrackObservation
from football_intelligence.fullmatch.heatmap import ScreenSpaceHeatMap


def _track(track_id: int, object_class: str, x: float, y: float) -> TrackObservation:
    return TrackObservation(
        track_id=track_id,
        object_class=object_class,
        bbox=BoundingBox(x1=x, y1=y, x2=x + 2, y2=y + 4),
        confidence=0.8,
        timestamp_ms=1_000,
        frame_index=25,
    )


def test_heat_map_is_fixed_32x18_and_ignores_ball_or_out_of_frame_tracks():
    heatmap = ScreenSpaceHeatMap()

    heatmap.observe(
        [
            _track(1, "player", 49, 48),
            _track(2, "goalkeeper", 0, 0),
            _track(3, "ball", 49, 48),
            _track(4, "player", 200, 200),
        ],
        frame_width=100,
        frame_height=100,
    )

    counts = heatmap.to_counts()
    assert len(counts) == 18
    assert all(len(row) == 32 for row in counts)
    assert sum(sum(row) for row in counts) == 2
    assert counts[9][16] == 1
    assert counts[0][0] == 1
    assert heatmap.observation_count == 2


def test_heat_map_round_trip_and_png_is_readable_and_labeled(tmp_path: Path):
    heatmap = ScreenSpaceHeatMap()
    heatmap.observe(
        [_track(1, "player", 25, 25), _track(2, "player", 75, 75)],
        frame_width=100,
        frame_height=100,
    )
    restored = ScreenSpaceHeatMap.from_counts(heatmap.to_counts())

    output = restored.write_png(tmp_path / "heat-map.png")
    image = cv2.imread(str(output))

    assert image is not None
    assert image.shape[0] >= 360 and image.shape[1] >= 640
    assert image.max() > 0
    assert restored.label == "Screen-space player density (not pitch calibrated)"
