"""Bounded, jump-aware presentation trails for players and the ball."""

from __future__ import annotations

from football_intelligence.domain import TrackObservation

PERSON_CLASSES = {"player", "goalkeeper", "referee"}


class TrailBuffer:
    """Track short tactical trails with age, length, and continuity limits.

    Player trails anchor at the bottom-center footpoint; the ball anchors at its
    box center. A trail is immediately reset when its track is lost (a frame
    gap), reappears after a timeout, or jumps farther than a fraction of the
    frame diagonal, so an ID switch never draws a line across the pitch.
    """

    def __init__(
        self,
        *,
        max_age_ms: int = 1500,
        max_points: int = 20,
        max_jump_ratio: float = 0.35,
    ):
        self.max_age_ms = max_age_ms
        self.max_points = max_points
        self.max_jump_ratio = max_jump_ratio
        self._points: dict[int, list[tuple[float, float, int]]] = {}
        self._last_frame: dict[int, int] = {}

    def update(
        self,
        tracks: list[TrackObservation],
        *,
        frame_index: int,
        timestamp_ms: int,
        frame_width: int,
        frame_height: int,
    ) -> dict[int, list[tuple[int, int]]]:
        diagonal = (frame_width * frame_width + frame_height * frame_height) ** 0.5
        max_jump = self.max_jump_ratio * diagonal
        active: set[int] = set()
        result: dict[int, list[tuple[int, int]]] = {}

        for track in tracks:
            if track.state != "confirmed":
                self._points.pop(track.track_id, None)
                self._last_frame.pop(track.track_id, None)
                continue
            active.add(track.track_id)
            anchor = self._anchor(track)
            points = self._points.setdefault(track.track_id, [])
            last_frame = self._last_frame.get(track.track_id)
            if last_frame is not None and frame_index - last_frame > 1:
                points.clear()
            elif points:
                previous_x, previous_y, _ = points[-1]
                jump = ((anchor[0] - previous_x) ** 2 + (anchor[1] - previous_y) ** 2) ** 0.5
                if jump > max_jump:
                    points.clear()
            points.append((anchor[0], anchor[1], timestamp_ms))
            cutoff = timestamp_ms - self.max_age_ms
            while points and points[0][2] < cutoff:
                points.pop(0)
            if len(points) > self.max_points:
                del points[: len(points) - self.max_points]
            self._last_frame[track.track_id] = frame_index
            result[track.track_id] = [(round(x), round(y)) for x, y, _ in points]

        for track_id in list(self._points):
            if track_id not in active:
                self._points.pop(track_id, None)
                self._last_frame.pop(track_id, None)
        return result

    @staticmethod
    def _anchor(track: TrackObservation) -> tuple[float, float]:
        if track.object_class == "ball":
            return track.bbox.center
        return (track.bbox.x1 + track.bbox.width / 2, track.bbox.y2)
