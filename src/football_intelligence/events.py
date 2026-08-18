"""Temporal football candidate rules and evidence-preserving fusion."""

from math import dist, prod

from football_intelligence.domain import EventEvidence, FootballEvent, TrackObservation


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
        best = max(group, key=lambda item: item.confidence)
        fused.append(
            FootballEvent(
                job_id=best.job_id,
                event_type=best.event_type,
                start_ms=min(item.start_ms for item in group),
                end_ms=max(item.end_ms for item in group),
                game_time=best.game_time,
                team=best.team,
                player=best.player,
                description=best.description,
                confidence=1 - prod(1 - item.confidence for item in group),
                evidence=[evidence for item in group for evidence in item.evidence],
                source=sorted({source for item in group for source in item.source}),
                track_ids=sorted({track_id for item in group for track_id in item.track_ids}),
                frame_refs=sorted({frame for item in group for frame in item.frame_refs}),
                needs_review=any(item.needs_review for item in group),
            )
        )
    return fused


class TemporalEventEngine:
    def __init__(self, job_id: str, kick_speed_px_s: float = 250, proximity_px: float = 60):
        self.job_id = job_id
        self.kick_speed_px_s = kick_speed_px_s
        self.proximity_px = proximity_px
        self._previous_ball: TrackObservation | None = None
        self._previous_near_player: TrackObservation | None = None
        self._events: list[FootballEvent] = []
        self._last_kick_ms = -10_000

    def observe(self, tracks: list[TrackObservation]) -> None:
        balls = [track for track in tracks if track.object_class == "ball"]
        players = [track for track in tracks if track.object_class in {"player", "goalkeeper"}]
        if not balls:
            return
        ball = max(balls, key=lambda item: item.confidence)

        if self._previous_ball is not None:
            elapsed_ms = ball.timestamp_ms - self._previous_ball.timestamp_ms
            if elapsed_ms > 0:
                displacement = dist(ball.bbox.center, self._previous_ball.bbox.center)
                speed = displacement * 1000 / elapsed_ms
                if (
                    speed >= self.kick_speed_px_s
                    and self._previous_near_player is not None
                    and ball.timestamp_ms - self._last_kick_ms > 750
                ):
                    confidence = min(0.95, 0.55 + speed / (4 * self.kick_speed_px_s))
                    self._events.append(
                        FootballEvent(
                            job_id=self.job_id,
                            event_type="kick_candidate",
                            start_ms=self._previous_ball.timestamp_ms,
                            end_ms=ball.timestamp_ms,
                            description="Ball accelerated near a tracked player",
                            confidence=confidence,
                            evidence=[
                                EventEvidence(
                                    kind="ball_speed_px_s",
                                    value=round(speed, 2),
                                    confidence=confidence,
                                    frame_refs=[self._previous_ball.frame_index, ball.frame_index],
                                )
                            ],
                            source=["heuristic.temporal"],
                            track_ids=[self._previous_near_player.track_id, ball.track_id],
                            frame_refs=[self._previous_ball.frame_index, ball.frame_index],
                        )
                    )
                    self._last_kick_ms = ball.timestamp_ms

        self._previous_ball = ball
        self._previous_near_player = min(
            players,
            key=lambda player: dist(player.bbox.center, ball.bbox.center),
            default=None,
        )
        if (
            self._previous_near_player is not None
            and dist(self._previous_near_player.bbox.center, ball.bbox.center) > self.proximity_px
        ):
            self._previous_near_player = None

    def finalize(self) -> list[FootballEvent]:
        return deduplicate_events(self._events)
