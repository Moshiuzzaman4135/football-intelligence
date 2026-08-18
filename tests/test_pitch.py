"""Playing-area detection filter tests."""

import pytest

from football_intelligence.domain import BoundingBox, Detection
from football_intelligence.pitch import (
    PlayingAreaFilter,
    distance_to_polygon,
    normalize_polygon,
    point_in_polygon,
)

PITCH = (
    (0.04, 0.37),
    (0.91, 0.35),
    (0.99, 0.92),
    (0.01, 0.95),
)


def detection(object_class, bbox, confidence=0.8):
    return Detection(
        object_class=object_class,
        confidence=confidence,
        bbox=BoundingBox(x1=bbox[0], y1=bbox[1], x2=bbox[2], y2=bbox[3]),
        frame_index=0,
        timestamp_ms=0,
    )


def test_point_in_polygon_ray_casting():
    assert point_in_polygon((0.5, 0.6), PITCH)
    assert not point_in_polygon((0.5, 0.1), PITCH)


def test_distance_to_polygon_is_zero_inside():
    assert distance_to_polygon((0.5, 0.6), PITCH) == 0.0


def test_spectator_outside_pitch_rejected():
    filt = PlayingAreaFilter(polygon=PITCH)
    # spectator in the top stands, well outside the polygon
    spectator = detection("player", (10, 5, 40, 60))
    result = filt.filter([spectator], frame_width=1000, frame_height=1000)

    assert result.kept == []
    assert result.rejected[0][1] == "outside_pitch"


def test_player_footpoint_inside_pitch_retained():
    filt = PlayingAreaFilter(polygon=PITCH)
    # player whose bottom-center (0.5, 0.65) lies inside the polygon
    player = detection("player", (470, 500, 530, 650))
    result = filt.filter([player], frame_width=1000, frame_height=1000)

    assert result.kept == [player]
    assert result.rejected == []


def test_person_uses_footpoint_not_center():
    filt = PlayingAreaFilter(polygon=PITCH)
    # center outside but footpoint inside: must be retained
    tall = detection("player", (480, 100, 520, 650))
    result = filt.filter([tall], frame_width=1000, frame_height=1000)

    assert result.kept == [tall]


def test_ball_uses_expanded_margin():
    filt = PlayingAreaFilter(polygon=PITCH, ball_margin=0.10)
    # ball center just above the top touchline, within the expanded margin
    ball = detection("ball", (495, 295, 505, 305))
    result = filt.filter([ball], frame_width=1000, frame_height=1000)

    assert result.kept == [ball]


def test_empty_polygon_disables_filtering():
    filt = PlayingAreaFilter(polygon=())
    player = detection("player", (10, 10, 40, 80))

    result = filt.filter([player], frame_width=1000, frame_height=1000)

    assert result.kept == [player]
    assert result.rejected == []


def test_non_person_classes_pass_through():
    filt = PlayingAreaFilter(polygon=PITCH)
    unknown = detection("unknown_class", (10, 10, 40, 80))

    result = filt.filter([unknown], frame_width=1000, frame_height=1000)

    assert result.kept == [unknown]


def test_normalize_polygon_rejects_out_of_range():
    with pytest.raises(ValueError):
        normalize_polygon([[0.0, 0.0], [2.0, 0.0], [1.0, 1.0]])


def test_normalize_polygon_requires_three_points():
    with pytest.raises(ValueError):
        normalize_polygon([[0.0, 0.0], [1.0, 1.0]])
