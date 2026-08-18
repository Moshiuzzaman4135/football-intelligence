"""OpenCV rendering with clean/tactical/debug presentation modes."""

from collections.abc import Mapping
from typing import Literal

import cv2
import numpy as np

from football_intelligence.domain import Detection, FootballEvent, TrackObservation
from football_intelligence.pitch import Polygon
from football_intelligence.timebase import ms_to_timestamp

COLORS = {
    "player": (255, 180, 0),
    "goalkeeper": (0, 215, 255),
    "referee": (180, 80, 255),
    "ball": (255, 255, 255),
}
PERSON_CLASSES = {"player", "goalkeeper", "referee"}
_PREFIX = {"player": "P", "goalkeeper": "GK", "referee": "R", "ball": "B"}


def compact_label(track: TrackObservation) -> str:
    return f"{_PREFIX.get(track.object_class, 'T')}{track.track_id}"


def draw_overlay(
    frame: np.ndarray,
    tracks: list[TrackObservation],
    events: list[FootballEvent],
    *,
    timestamp_ms: int,
    trails: Mapping[int, list[tuple[int, int]]],
    mode: Literal["clean", "tactical", "debug"] = "clean",
    active_ceiling: int = 30,
    playing_area: Polygon | None = None,
    rejected: list[tuple[Detection, str]] | None = None,
    banner_duration_ms: int = 1500,
) -> np.ndarray:
    rendered = frame.copy()
    height, width = rendered.shape[:2]

    visible = _visible_tracks(tracks, mode, active_ceiling)

    if mode == "debug":
        for detection, reason in rejected or []:
            box = detection.bbox
            cv2.rectangle(
                rendered,
                (round(box.x1), round(box.y1)),
                (round(box.x2), round(box.y2)),
                (90, 90, 120),
                1,
            )
            cv2.putText(
                rendered,
                reason,
                (round(box.x1), max(12, round(box.y1) - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                (90, 90, 120),
                1,
                cv2.LINE_AA,
            )
        if playing_area:
            _draw_polygon(rendered, playing_area, width, height)

    for track in visible:
        color = COLORS.get(track.object_class, (180, 180, 180))
        box = track.bbox
        start = (round(box.x1), round(box.y1))
        end = (round(box.x2), round(box.y2))
        if track.object_class == "ball":
            center = (round(box.center[0]), round(box.center[1]))
            cv2.circle(rendered, center, 5, color, -1)
            cv2.circle(rendered, center, 7, color, 2)
        else:
            cv2.rectangle(rendered, start, end, color, 2)
        label = _label_for(track, mode)
        if label:
            cv2.putText(
                rendered,
                label,
                (start[0], max(16, start[1] - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                color,
                1,
                cv2.LINE_AA,
            )
        if mode in {"tactical", "debug"}:
            points = trails.get(track.track_id, [])
            for previous, current in zip(points, points[1:], strict=False):
                cv2.line(rendered, previous, current, color, 2)

    cv2.rectangle(rendered, (8, 8), (180, 34), (0, 0, 0), -1)
    cv2.putText(
        rendered,
        ms_to_timestamp(timestamp_ms),
        (14, 27),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )

    active_event = _banner_event(events, timestamp_ms, banner_duration_ms)
    if active_event is not None:
        cv2.rectangle(rendered, (8, 42), (300, 95), (15, 15, 15), -1)
        cv2.putText(
            rendered,
            active_event.event_type.replace("_", " ").upper(),
            (14, 64),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 220, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            rendered,
            f"confidence {active_event.confidence:.0%} - review candidate",
            (14, 86),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.43,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    return rendered


def _visible_tracks(
    tracks: list[TrackObservation], mode: str, active_ceiling: int
) -> list[TrackObservation]:
    if mode == "debug":
        return list(tracks)
    confirmed = [track for track in tracks if track.state == "confirmed"]
    persons = [track for track in confirmed if track.object_class in PERSON_CLASSES]
    others = [track for track in confirmed if track.object_class not in PERSON_CLASSES]
    if len(persons) > active_ceiling:
        persons = sorted(
            persons, key=lambda item: (item.hits, item.confidence), reverse=True
        )[:active_ceiling]
    return others + persons


def _label_for(track: TrackObservation, mode: str) -> str:
    if mode == "debug":
        return (
            f"{track.object_class}#{track.track_id} {track.state} "
            f"{track.confidence:.0%}"
        )
    if track.team_id != "unknown":
        return f"{compact_label(track)} {track.team_id.replace('team_', 'T')}"
    return compact_label(track)


def _banner_event(
    events: list[FootballEvent], timestamp_ms: int, banner_duration_ms: int
) -> FootballEvent | None:
    if not events:
        return None
    candidates = [
        event
        for event in events
        if event.start_ms <= timestamp_ms <= event.start_ms + banner_duration_ms
    ]
    return max(candidates, key=lambda item: item.confidence, default=None)


def _draw_polygon(rendered: np.ndarray, polygon: Polygon, width: int, height: int) -> None:
    points = np.array(
        [[round(x * width), round(y * height)] for x, y in polygon],
        dtype=np.int32,
    )
    cv2.polylines(rendered, [points], isClosed=True, color=(0, 255, 0), thickness=2)
