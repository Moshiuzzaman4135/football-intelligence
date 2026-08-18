"""OpenCV rendering for reviewable tracks and event candidates."""

from collections.abc import Mapping

import cv2
import numpy as np

from football_intelligence.domain import FootballEvent, TrackObservation
from football_intelligence.timebase import ms_to_timestamp

COLORS = {
    "player": (255, 180, 0),
    "goalkeeper": (0, 215, 255),
    "referee": (180, 80, 255),
    "ball": (255, 255, 255),
}


def draw_overlay(
    frame: np.ndarray,
    tracks: list[TrackObservation],
    events: list[FootballEvent],
    *,
    timestamp_ms: int,
    trails: Mapping[int, list[tuple[int, int]]],
) -> np.ndarray:
    rendered = frame.copy()
    for track in tracks:
        color = COLORS.get(track.object_class, (180, 180, 180))
        box = track.bbox
        start = (round(box.x1), round(box.y1))
        end = (round(box.x2), round(box.y2))
        cv2.rectangle(rendered, start, end, color, 2)
        label = (
            f"{track.object_class} #{track.track_id} {track.team_id} "
            f"{track.confidence:.0%}"
        )
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
    if events:
        event = max(events, key=lambda item: item.confidence)
        cv2.rectangle(rendered, (8, 42), (300, 95), (15, 15, 15), -1)
        cv2.putText(
            rendered,
            event.event_type.replace("_", " ").upper(),
            (14, 64),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 220, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            rendered,
            f"confidence {event.confidence:.0%} - review candidate",
            (14, 86),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.43,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    return rendered
