import pytest

from football_intelligence.domain import (
    BoundingBox,
    EventEvidence,
    FootballEvent,
    TrackObservation,
)
from football_intelligence.events import TemporalEventEngine, deduplicate_events, fuse_events


def make_event(start_ms: int, confidence: float, source: str) -> FootballEvent:
    return FootballEvent(
        job_id="job-1",
        event_type="kick_candidate",
        start_ms=start_ms,
        end_ms=start_ms + 200,
        description="Ball acceleration near player",
        confidence=confidence,
        evidence=[EventEvidence(kind="velocity", value=7.2, confidence=confidence)],
        source=[source],
        track_ids=[1, 9],
        frame_refs=[start_ms // 40],
    )


def observation(track_id, object_class, center, timestamp_ms, frame_index):
    x, y = center
    size = 8 if object_class == "ball" else 30
    return TrackObservation(
        track_id=track_id,
        object_class=object_class,
        bbox=BoundingBox(x1=x, y1=y, x2=x + size, y2=y + size),
        confidence=0.9,
        timestamp_ms=timestamp_ms,
        frame_index=frame_index,
    )


def test_deduplication_keeps_stronger_nearby_candidate():
    weak = make_event(1000, 0.55, "heuristic.a")
    strong = make_event(1400, 0.81, "heuristic.a")

    result = deduplicate_events([weak, strong], window_ms=1000)

    assert len(result) == 1
    assert result[0].confidence == 0.81


def test_fusion_combines_confidence_and_preserves_producers():
    heuristic = make_event(1000, 0.6, "heuristic.temporal")
    action_model = make_event(1300, 0.75, "action.spotter")

    fused = fuse_events([heuristic, action_model], window_ms=1000)

    assert len(fused) == 1
    assert fused[0].confidence == pytest.approx(0.9)
    assert fused[0].source == ["action.spotter", "heuristic.temporal"]
    assert len(fused[0].evidence) == 2
    assert fused[0].start_ms == 1000
    assert fused[0].end_ms == 1500


def test_temporal_engine_requires_motion_across_frames_for_kick_candidate():
    engine = TemporalEventEngine(job_id="job-1", kick_speed_px_s=150)
    engine.observe(
        [
            observation(1, "player", (100, 100), 0, 0),
            observation(9, "ball", (120, 120), 0, 0),
        ]
    )
    engine.observe(
        [
            observation(1, "player", (100, 100), 100, 1),
            observation(9, "ball", (124, 120), 100, 1),
        ]
    )
    engine.observe(
        [
            observation(1, "player", (100, 100), 200, 2),
            observation(9, "ball", (170, 120), 200, 2),
        ]
    )

    events = engine.finalize()

    assert len(events) == 1
    assert events[0].event_type == "kick_candidate"
    assert events[0].start_ms == 100
    assert events[0].end_ms == 200
    assert events[0].track_ids == [1, 9]
    assert events[0].evidence[0].kind == "ball_speed_px_s"
    assert events[0].needs_review is True


def test_static_single_frame_cannot_create_kick_candidate():
    engine = TemporalEventEngine(job_id="job-1", kick_speed_px_s=150)
    engine.observe(
        [
            observation(1, "player", (100, 100), 0, 0),
            observation(9, "ball", (120, 120), 0, 0),
        ]
    )

    assert engine.finalize() == []
