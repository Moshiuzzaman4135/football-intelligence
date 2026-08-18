"""Bundled presentation and detection-quality options.

Keeps the many quality knobs out of ``Pipeline``/``FullMatchRunner`` signatures
and derives them once from ``Settings`` for the API/CLI entry points.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from football_intelligence.domain import Detection
from football_intelligence.pitch import PlayingAreaFilter, Polygon, normalize_polygon


@dataclass(frozen=True)
class QualityOptions:
    overlay_mode: str = "clean"
    playing_area: PlayingAreaFilter = field(default_factory=PlayingAreaFilter)
    person_min_confidence: float = 0.25
    ball_min_confidence: float = 0.25
    active_track_ceiling: int = 30
    track_confirm_min_hits: int = 2
    trail_max_age_ms: int = 1500
    trail_max_points: int = 20
    trail_max_jump_ratio: float = 0.35
    banner_duration_ms: int = 1500
    kick_speed_px_s: float = 250
    kick_proximity_px: float = 60
    kick_min_contact_frames: int = 1
    kick_min_ball_continuity: int = 2
    kick_cooldown_ms: int = 1000
    kick_max_confidence: float = 0.70
    kick_max_jump_ratio: float = 0.30


def apply_confidence_thresholds(
    detections: list[Detection],
    *,
    person_min_confidence: float,
    ball_min_confidence: float,
) -> list[Detection]:
    thresholds = {
        "player": person_min_confidence,
        "goalkeeper": person_min_confidence,
        "referee": person_min_confidence,
        "ball": ball_min_confidence,
    }
    return [
        detection
        for detection in detections
        if detection.confidence >= thresholds.get(detection.object_class, 0.0)
    ]


def playing_area_from_polygon(
    points: list[list[float]] | list[tuple[float, float]],
    *,
    person_tolerance: float,
    ball_margin: float,
) -> PlayingAreaFilter:
    if not points:
        return PlayingAreaFilter(polygon=())
    polygon: Polygon = normalize_polygon(points)
    return PlayingAreaFilter(
        polygon=polygon,
        person_tolerance=person_tolerance,
        ball_margin=ball_margin,
    )
