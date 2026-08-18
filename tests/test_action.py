"""Focused tests for the ActionSpotter interface and CALF normalization."""

import numpy as np
import pytest

from football_intelligence.action import (
    CALF_EVENTS,
    ActionSpot,
    action_spots_from_features,
    normalize_action,
    normalize_label,
)
from football_intelligence.domain import EventStatus


def test_known_label_maps_to_football_event():
    assert normalize_label("Goal") == "goal_candidate"
    assert normalize_label("Foul") == "foul_candidate"
    assert normalize_label("Yellow card") == "yellow_card_candidate"
    assert normalize_label("Red card") == "red_card_candidate"
    assert normalize_label("Corner") == "corner_candidate"
    assert normalize_label("Offside") == "offside_candidate"


def test_unknown_label_safely_ignored():
    assert normalize_label("SOMETHING_UNKNOWN") is None
    assert normalize_label("") is None


def test_full_calf_taxonomy_present():
    assert len(CALF_EVENTS) == 17
    for label in (
        "Penalty",
        "Kick-off",
        "Goal",
        "Substitution",
        "Offside",
        "Shots on target",
        "Shots off target",
        "Clearance",
        "Ball out of play",
        "Throw-in",
        "Foul",
        "Indirect free-kick",
        "Direct free-kick",
        "Corner",
        "Yellow card",
        "Red card",
        "Yellow->red card",
    ):
        assert label in CALF_EVENTS


def test_normalize_action_produces_football_event_with_evidence():
    spot = ActionSpot(
        event_type="goal_candidate",
        start_ms=65_000,
        end_ms=65_500,
        confidence=0.8,
        raw_label="Goal",
        raw_score=0.82,
        producer="action.calf",
        producer_version="calf",
        frame_index=130,
    )
    event = normalize_action(spot, job_id="job-1", match_clock_ms=780_000)

    assert event.event_type == "goal_candidate"
    assert event.start_ms == 65_000 and event.end_ms == 65_500
    assert event.status is EventStatus.CANDIDATE
    assert event.needs_review is True
    assert event.source == ["action.calf"]
    assert event.original_model_output["raw_label"] == "Goal"
    assert event.original_model_output["raw_score"] == 0.82
    assert event.evidence[0].kind == "action_spot"
    assert event.evidence[0].confidence == 0.8


def test_unknown_label_spot_raises():
    spot = ActionSpot(
        event_type="something_bogus",
        start_ms=0,
        end_ms=100,
        confidence=0.5,
        raw_label="NotAClass",
        raw_score=0.5,
        producer="action.test",
        producer_version="1",
    )
    with pytest.raises(ValueError):
        normalize_action(spot, job_id="job-1")


def test_decode_timestamp_mapping_is_correct():
    # One detection at frame_pos=0.5 over feature_length=240 => frame 119 => 59.5s.
    spotting = np.zeros((1, 2 + 17))
    spotting[0, 0] = 0.9  # object confidence
    spotting[0, 1] = 0.5  # normalized frame position
    spotting[0, 2 + 2] = 1.0  # Goal class one-hot
    spots = action_spots_from_features(
        spotting, feature_length=240, frame_ms=500
    )
    assert len(spots) == 1
    spot = spots[0]
    assert spot.event_type == "goal_candidate"
    assert spot.start_ms == 119 * 500
    assert spot.raw_label == "Goal"


def test_decode_nms_keeps_strongest_in_window():
    # Two Goal detections close together => NMS keeps the stronger one.
    spotting = np.zeros((2, 2 + 17))
    for i, (conf, pos) in enumerate(((0.4, 0.1), (0.9, 0.12))):
        spotting[i, 0] = conf
        spotting[i, 1] = pos
        spotting[i, 2 + 2] = 1.0
    spots = action_spots_from_features(
        spotting, feature_length=240, frame_ms=500, nms_delta_ms=20_000
    )
    assert len(spots) == 1
    assert spots[0].confidence == pytest.approx(0.9)


def test_decode_unknown_class_dropped():
    # Force a class index beyond the 17-action taxonomy => no spot.
    spotting = np.zeros((1, 2 + 19))
    spotting[0, 0] = 0.9
    spotting[0, 1] = 0.5
    spotting[0, 2 + 18] = 1.0  # an 18th bogus class
    spots = action_spots_from_features(
        spotting, feature_length=240, frame_ms=500
    )
    assert spots == []
