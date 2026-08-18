"""Temporal football candidate rules and evidence-preserving fusion.

The kick detector is a small state machine rather than a bare speed threshold:

    SEARCHING -> CONTACT (ball near a player)
              -> RELEASE (ball accelerates away + separates)
              -> KICK_CANDIDATE -> COOLDOWN

A candidate requires ball-track continuity across multiple frames, a minimum
number of near-player contact frames, separation (distance increasing), and an
inter-frame speed above threshold. Confidence is earned from those factors and
is capped for heuristic-only evidence.
"""

from __future__ import annotations

from math import dist, prod

from football_intelligence.domain import EventEvidence, FootballEvent, TrackObservation

# Semantic events shown in the default timeline. Low-level heuristic/debug
# evidence (kick spam, track updates, raw OCR) is excluded from the default view
# but preserved as evidence/debug data.
SEMANTIC_EVENT_TYPES = frozenset(
    {
        "goal_candidate",
        "score_change_candidate",
        "penalty_candidate",
        "foul_candidate",
        "corner_candidate",
        "yellow_card_candidate",
        "red_card_candidate",
        "offside_candidate",
        "substitution_candidate",
        "shot_on_target_candidate",
        "shot_off_target_candidate",
        "direct_free_kick_candidate",
        "indirect_free_kick_candidate",
        "throw_in_candidate",
        "kick_off_candidate",
        "clearance_candidate",
        "ball_out_candidate",
    }
)


def semantic_events(
    events: list[FootballEvent],
) -> list[FootballEvent]:
    """Return only the events suitable for the default semantic timeline."""
    return [event for event in events if event.event_type in SEMANTIC_EVENT_TYPES]


def debug_events(events: list[FootballEvent]) -> list[FootballEvent]:
    """Return low-level evidence events (kick spam, etc.) for debug views."""
    return [event for event in events if event.event_type not in SEMANTIC_EVENT_TYPES]


def fuse_semantic_events(
    events: list[FootballEvent], window_ms: int = 10_000
) -> list[FootballEvent]:
    """Fuse independent evidence into one semantic event where possible.

    Rules:
    * A CALF ``goal_candidate`` plus a nearby stable OCR ``score_change_candidate``
      fuses into a strong ``goal_candidate`` carrying both evidence sources.
    * A ``goal_candidate`` with no score-change support stays a reviewable
      ``goal_candidate`` (needs_review stays true).
    * A ``score_change_candidate`` with no action-model support stays a reviewable
      ``score_change_candidate``.
    * All other event types are passed through unchanged (already deduplicated).

    Duplicates/replays of the same type are not deleted; each independent
    evidence source is preserved and their confidences are combined via noisy-or.
    """
    goal_actions = [
        event for event in events if event.event_type == "goal_candidate"
    ]
    score_changes = [
        event for event in events
        if event.event_type == "score_change_candidate"
    ]
    fused_goals: list[FootballEvent] = []
    for goal in sorted(goal_actions, key=lambda item: item.start_ms):
        support = next(
            (
                change
                for change in sorted(
                    score_changes, key=lambda item: item.start_ms
                )
                if abs(change.start_ms - goal.start_ms) <= window_ms
            ),
            None,
        )
        if support is None:
            # No OCR support: remains a reviewable goal candidate.
            fused_goals.append(goal)
            continue
        combined_confidence = 1 - (1 - goal.confidence) * (1 - support.confidence)
        fused_goals.append(
            FootballEvent(
                id=goal.id,
                job_id=goal.job_id,
                event_type="goal_candidate",
                start_ms=min(goal.start_ms, support.start_ms),
                end_ms=max(goal.end_ms, support.end_ms),
                game_time=goal.game_time,
                team=goal.team,
                player=goal.player,
                description="Goal action spotted and scoreboard confirmed a score change",
                confidence=round(min(1.0, combined_confidence), 4),
                evidence=[*goal.evidence, *support.evidence],
                source=sorted(
                    {source for event in (goal, support) for source in event.source}
                ),
                track_ids=goal.track_ids,
                frame_refs=sorted(
                    {frame for event in (goal, support) for frame in event.frame_refs}
                ),
                needs_review=False,  # two independent sources agree
                status=goal.status,
                period=goal.period,
                match_clock_ms=goal.match_clock_ms,
                score_transition=support.score_transition,
                producer_version=goal.producer_version,
                review=goal.review,
                original_model_output={
                    "fused_sources": [
                        source for event in (goal, support) for source in event.source
                    ],
                    "score_transition": support.score_transition,
                },
            )
        )
    fused_ids = {event.id for event in fused_goals}
    others = [
        event for event in events
        if event.id not in fused_ids and event.event_type != "goal_candidate"
    ]
    return sorted([*fused_goals, *others], key=lambda item: item.start_ms)



def deduplicate_events(events: list[FootballEvent], window_ms: int = 1000) -> list[FootballEvent]:
    kept: list[FootballEvent] = []
    for event in sorted(events, key=lambda item: (item.start_ms, -item.confidence)):
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(kept)
                if existing.job_id == event.job_id
                and existing.event_type == event.event_type
                and abs(existing.start_ms - event.start_ms) <= window_ms
            ),
            None,
        )
        if duplicate_index is None:
            kept.append(event)
        elif event.confidence > kept[duplicate_index].confidence:
            kept[duplicate_index] = event
    return sorted(kept, key=lambda item: item.start_ms)


def fuse_events(events: list[FootballEvent], window_ms: int = 1500) -> list[FootballEvent]:
    groups: list[list[FootballEvent]] = []
    for event in sorted(events, key=lambda item: item.start_ms):
        if (
            groups
            and groups[-1][0].job_id == event.job_id
            and groups[-1][0].event_type == event.event_type
            and event.start_ms - max(item.end_ms for item in groups[-1]) <= window_ms
        ):
            groups[-1].append(event)
        else:
            groups.append([event])

    fused: list[FootballEvent] = []
    for group in groups:
        independent: list[FootballEvent] = []
        used_sources: set[str] = set()
        for candidate in sorted(group, key=lambda item: item.confidence, reverse=True):
            candidate_sources = set(candidate.source)
            if candidate_sources & used_sources:
                continue
            independent.append(candidate)
            used_sources.update(candidate_sources)
        best = max(independent, key=lambda item: item.confidence)
        fused.append(
            FootballEvent(
                job_id=best.job_id,
                event_type=best.event_type,
                start_ms=min(item.start_ms for item in independent),
                end_ms=max(item.end_ms for item in independent),
                game_time=best.game_time,
                team=best.team,
                player=best.player,
                description=best.description,
                confidence=1 - prod(1 - item.confidence for item in independent),
                evidence=[evidence for item in independent for evidence in item.evidence],
                source=sorted({source for item in independent for source in item.source}),
                track_ids=sorted(
                    {track_id for item in independent for track_id in item.track_ids}
                ),
                frame_refs=sorted(
                    {frame for item in independent for frame in item.frame_refs}
                ),
                needs_review=any(item.needs_review for item in independent),
            )
        )
    return fused


class TemporalEventEngine:
    def __init__(
        self,
        job_id: str,
        kick_speed_px_s: float = 250,
        proximity_px: float = 60,
        *,
        min_contact_frames: int = 1,
        min_ball_continuity: int = 2,
        cooldown_ms: int = 1000,
        max_confidence: float = 0.70,
        max_ball_jump_px: float | None = None,
    ):
        self.job_id = job_id
        self.kick_speed_px_s = kick_speed_px_s
        self.proximity_px = proximity_px
        self.min_contact_frames = min_contact_frames
        self.min_ball_continuity = min_ball_continuity
        self.cooldown_ms = cooldown_ms
        self.max_confidence = max_confidence
        self.max_ball_jump_px = max_ball_jump_px

        self._ball_track_id: int | None = None
        self._ball_continuity = 0
        self._last_ball: TrackObservation | None = None
        self._contact_player: TrackObservation | None = None
        self._contact_frames = 0
        self._contact_distance: float | None = None
        self._contact_ts_ms: int | None = None
        self._contact_frame_index: int | None = None
        self._last_kick_ms: int = -(10**9)
        self._events: list[FootballEvent] = []
        self._drain_index = 0

    def observe(self, tracks: list[TrackObservation]) -> None:
        balls = [track for track in tracks if track.object_class == "ball"]
        players = [track for track in tracks if track.object_class in {"player", "goalkeeper"}]
        if not balls:
            self._ball_track_id = None
            self._ball_continuity = 0
            self._last_ball = None
            self._reset_contact()
            return
        ball = max(balls, key=lambda item: item.confidence)

        if self._ball_track_id != ball.track_id:
            self._ball_continuity = 0
            self._last_ball = None
            self._reset_contact()
        self._ball_track_id = ball.track_id

        speed = 0.0
        if self._last_ball is not None:
            elapsed_ms = ball.timestamp_ms - self._last_ball.timestamp_ms
            if elapsed_ms > 0:
                displacement = dist(ball.bbox.center, self._last_ball.bbox.center)
                speed = displacement * 1000 / elapsed_ms
                if (
                    self.max_ball_jump_px is not None
                    and displacement > self.max_ball_jump_px
                ):
                    # extreme scene/camera transition: never treat as a kick
                    self._ball_continuity = 0
                    self._last_ball = ball
                    self._reset_contact()
                    return

        near_player = min(
            players,
            key=lambda player: dist(player.bbox.center, ball.bbox.center),
            default=None,
        )
        proximity = (
            dist(near_player.bbox.center, ball.bbox.center)
            if near_player is not None
            else float("inf")
        )

        # Snapshot the pre-frame contact state: the release transition is
        # measured against it, so the event's start lands on the last contact
        # frame before the ball accelerated away.
        pre_contact_player = self._contact_player
        pre_contact_frames = self._contact_frames
        pre_contact_distance = self._contact_distance
        pre_contact_ts_ms = self._contact_ts_ms
        pre_contact_frame_index = self._contact_frame_index

        moving_away = (
            pre_contact_distance is not None and proximity > pre_contact_distance
        )

        # Update contact evidence for the next frame.
        if near_player is not None and proximity <= self.proximity_px:
            if (
                self._contact_player is None
                or self._contact_player.track_id != near_player.track_id
            ):
                self._contact_frames = 0
            self._contact_player = near_player
            self._contact_frames += 1
            self._contact_distance = proximity
            self._contact_ts_ms = ball.timestamp_ms
            self._contact_frame_index = ball.frame_index

        self._ball_continuity += 1

        if (
            speed >= self.kick_speed_px_s
            and pre_contact_player is not None
            and pre_contact_frames >= self.min_contact_frames
            and self._ball_continuity >= self.min_ball_continuity
            and moving_away
            and ball.timestamp_ms - self._last_kick_ms >= self.cooldown_ms
        ):
            self._emit_kick(
                ball,
                speed=speed,
                contact_player=pre_contact_player,
                contact_frames=pre_contact_frames,
                contact_distance=pre_contact_distance or 0.0,
                contact_ts_ms=pre_contact_ts_ms or ball.timestamp_ms,
                contact_frame_index=pre_contact_frame_index or ball.frame_index,
            )
            self._last_kick_ms = ball.timestamp_ms
            self._reset_contact()

        self._last_ball = ball

    def _emit_kick(
        self,
        ball: TrackObservation,
        *,
        speed: float,
        contact_player: TrackObservation,
        contact_frames: int,
        contact_distance: float,
        contact_ts_ms: int,
        contact_frame_index: int,
    ) -> None:
        confidence = self._confidence(
            speed=speed,
            contact_frames=contact_frames,
            continuity=self._ball_continuity,
        )
        self._events.append(
            FootballEvent(
                job_id=self.job_id,
                event_type="kick_candidate",
                start_ms=contact_ts_ms,
                end_ms=ball.timestamp_ms,
                description="Ball accelerated away from a tracked player",
                confidence=confidence,
                evidence=[
                    EventEvidence(
                        kind="ball_speed_px_s",
                        value=round(speed, 2),
                        confidence=confidence,
                        frame_refs=[contact_frame_index, ball.frame_index],
                    ),
                    EventEvidence(
                        kind="player_proximity_px",
                        value=round(contact_distance, 2),
                        confidence=confidence,
                        frame_refs=[contact_frame_index],
                    ),
                ],
                source=["heuristic.temporal"],
                track_ids=[contact_player.track_id, ball.track_id],
                frame_refs=[contact_frame_index, ball.frame_index],
            )
        )

    def _confidence(self, *, speed: float, contact_frames: int, continuity: int) -> float:
        score = 0.45
        score += 0.05 * min(contact_frames, 3)
        score += 0.05 * min(max(0, continuity - self.min_ball_continuity), 3)
        if speed >= 2 * self.kick_speed_px_s:
            score += 0.10
        return round(min(self.max_confidence, score), 4)

    def _reset_contact(self) -> None:
        self._contact_player = None
        self._contact_frames = 0
        self._contact_distance = None
        self._contact_ts_ms = None
        self._contact_frame_index = None

    def finalize(self) -> list[FootballEvent]:
        return deduplicate_events(self._events)

    def drain_candidates(self) -> list[FootballEvent]:
        candidates = self._events[self._drain_index :]
        self._drain_index = len(self._events)
        return candidates
