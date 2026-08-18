import pytest
from pydantic import ValidationError

from football_intelligence.domain import (
    BoundingBox,
    Detection,
    EventEvidence,
    FootballEvent,
    JobStatus,
    TrackObservation,
)


def test_bounding_box_requires_positive_area():
    with pytest.raises(ValidationError, match="x2 must be greater than x1"):
        BoundingBox(x1=20, y1=10, x2=20, y2=30)


def test_detection_normalizes_required_frame_evidence():
    detection = Detection(
        object_class="player",
        confidence=0.82,
        bbox=BoundingBox(x1=10, y1=20, x2=40, y2=80),
        frame_index=25,
        timestamp_ms=1000,
    )

    assert detection.object_class == "player"
    assert detection.bbox.width == 30
    assert detection.bbox.center == (25.0, 50.0)


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_detection_rejects_confidence_outside_unit_interval(confidence):
    with pytest.raises(ValidationError):
        Detection(
            object_class="ball",
            confidence=confidence,
            bbox=BoundingBox(x1=1, y1=1, x2=4, y2=4),
            frame_index=0,
            timestamp_ms=0,
        )


def test_track_observation_defaults_to_unknown_identity_fields():
    track = TrackObservation(
        track_id=14,
        object_class="player",
        bbox=BoundingBox(x1=10, y1=20, x2=40, y2=80),
        confidence=0.73,
        timestamp_ms=1200,
        frame_index=30,
    )

    assert track.team_id == "unknown"
    assert track.player == "unknown"
    assert track.pitch_x is None
    assert track.pitch_y is None


def test_event_rejects_end_before_start():
    with pytest.raises(ValidationError, match="end_ms must be greater than or equal to start_ms"):
        FootballEvent(
            job_id="job-1",
            event_type="shot_candidate",
            start_ms=2000,
            end_ms=1000,
            description="Ball accelerated toward goal",
            confidence=0.7,
            evidence=[EventEvidence(kind="ball_speed", value=12.4, confidence=0.8)],
            source=["heuristic"],
        )


def test_uncertain_event_keeps_evidence_sources_and_review_flag():
    event = FootballEvent(
        job_id="job-1",
        event_type="shot_candidate",
        start_ms=1000,
        end_ms=1800,
        description="Ball accelerated away from a player",
        confidence=0.71,
        evidence=[
            EventEvidence(
                kind="ball_acceleration",
                value=8.5,
                confidence=0.76,
                frame_refs=[25, 30, 35],
            )
        ],
        source=["heuristic.temporal"],
        track_ids=[3, 14],
        frame_refs=[25, 35],
    )

    assert event.needs_review is True
    assert event.team == "unknown"
    assert event.player == "unknown"
    assert event.evidence[0].frame_refs == [25, 30, 35]


def test_job_status_values_are_stable_api_strings():
    assert [status.value for status in JobStatus] == [
        "created",
        "running",
        "stopping",
        "completed",
        "failed",
        "stopped",
    ]
