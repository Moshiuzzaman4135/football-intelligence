"""Trail buffer and overlay presentation tests."""

import numpy as np

from football_intelligence.domain import (
    BoundingBox,
    EventEvidence,
    FootballEvent,
    TrackObservation,
)
from football_intelligence.overlay import (
    _banner_event,
    _label_for,
    _visible_tracks,
    compact_label,
    draw_overlay,
)
from football_intelligence.trails import TrailBuffer


def track(track_id, object_class, bbox, *, frame_index=0, timestamp_ms=0, state="confirmed"):
    return TrackObservation(
        track_id=track_id,
        object_class=object_class,
        bbox=BoundingBox(x1=bbox[0], y1=bbox[1], x2=bbox[2], y2=bbox[3]),
        confidence=0.8,
        timestamp_ms=timestamp_ms,
        frame_index=frame_index,
        state=state,
    )


def test_compact_label_omits_team_and_confidence():
    player = track(7, "player", (20, 20, 60, 100))
    ball = track(3, "ball", (20, 20, 30, 30))

    assert compact_label(player) == "P7"
    assert compact_label(ball) == "B3"


def test_clean_label_omits_unknown_team():
    player = track(7, "player", (20, 20, 60, 100))

    assert _label_for(player, "clean") == "P7"


def test_visible_tracks_hides_tentative_and_applies_ceiling():
    confirmed = [track(i, "player", (10, 10, 30, 50)) for i in range(40)]
    tentative = track(99, "player", (10, 10, 30, 50), state="tentative")

    visible = _visible_tracks(confirmed + [tentative], "clean", active_ceiling=30)

    assert len(visible) == 30
    assert all(item.state == "confirmed" for item in visible)


def test_debug_mode_shows_all_tracks_including_tentative():
    confirmed = track(1, "player", (10, 10, 30, 50))
    tentative = track(2, "player", (10, 10, 30, 50), state="tentative")

    visible = _visible_tracks([confirmed, tentative], "debug", active_ceiling=30)

    assert len(visible) == 2


def test_banner_only_appears_within_window():
    event = FootballEvent(
        job_id="j",
        event_type="kick_candidate",
        start_ms=1000,
        end_ms=1100,
        description="x",
        confidence=0.6,
        evidence=[EventEvidence(kind="x", value=1.0, confidence=0.6)],
        source=["heuristic.temporal"],
    )
    assert _banner_event([event], 1000, 1500) == event
    assert _banner_event([event], 2400, 1500) == event
    assert _banner_event([event], 2600, 1500) is None


def test_overlay_clean_draws_without_resizing():
    frame = np.zeros((120, 200, 3), dtype=np.uint8)
    player = track(7, "player", (20, 20, 60, 100))

    rendered = draw_overlay(frame, [player], [], timestamp_ms=1000, trails={})

    assert rendered.shape == frame.shape
    assert np.count_nonzero(rendered) > 0


def test_trail_buffer_ignores_tentative_tracks():
    buffer = TrailBuffer()
    tentative = track(1, "player", (10, 10, 40, 80), state="tentative")

    trails = buffer.update(
        [tentative], frame_index=0, timestamp_ms=0, frame_width=100, frame_height=100
    )

    assert trails == {}


def test_trail_buffer_uses_footpoint_for_players():
    buffer = TrailBuffer()
    player = track(1, "player", (10, 10, 40, 80))

    trails = buffer.update(
        [player], frame_index=0, timestamp_ms=0, frame_width=100, frame_height=100
    )

    assert trails[1] == [(25, 80)]


def test_trail_buffer_uses_center_for_ball():
    buffer = TrailBuffer()
    ball = track(1, "ball", (10, 10, 30, 30))

    trails = buffer.update([ball], frame_index=0, timestamp_ms=0, frame_width=100, frame_height=100)

    assert trails[1] == [(20, 20)]


def test_trail_buffer_enforces_max_age():
    buffer = TrailBuffer(max_age_ms=100)
    t0 = track(1, "player", (10, 10, 40, 80), frame_index=0, timestamp_ms=0)
    t1 = track(1, "player", (12, 10, 42, 80), frame_index=1, timestamp_ms=200)

    buffer.update([t0], frame_index=0, timestamp_ms=0, frame_width=100, frame_height=100)
    trails = buffer.update([t1], frame_index=1, timestamp_ms=200, frame_width=100, frame_height=100)

    assert trails[1] == [(27, 80)]


def test_trail_buffer_resets_on_large_jump():
    buffer = TrailBuffer(max_jump_ratio=0.1)
    t0 = track(1, "player", (10, 10, 40, 80), frame_index=0, timestamp_ms=0)
    t1 = track(1, "player", (200, 10, 230, 80), frame_index=1, timestamp_ms=40)

    buffer.update([t0], frame_index=0, timestamp_ms=0, frame_width=400, frame_height=400)
    trails = buffer.update([t1], frame_index=1, timestamp_ms=40, frame_width=400, frame_height=400)

    # jump of 190 px vs diagonal 566 * 0.1 = 56.6 -> reset, so only the new point remains
    assert trails[1] == [(215, 80)]


def test_trail_buffer_resets_on_frame_gap():
    buffer = TrailBuffer()
    t0 = track(1, "player", (10, 10, 40, 80), frame_index=0, timestamp_ms=0)
    t_gap = track(1, "player", (12, 10, 42, 80), frame_index=5, timestamp_ms=200)

    buffer.update([t0], frame_index=0, timestamp_ms=0, frame_width=100, frame_height=100)
    trails = buffer.update(
        [t_gap], frame_index=5, timestamp_ms=200, frame_width=100, frame_height=100
    )

    assert trails[1] == [(27, 80)]
