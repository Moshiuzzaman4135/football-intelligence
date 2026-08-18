"""Normalized playing-area geometry and detection filtering.

The deterministic color detector (and COCO ``person``) treats every saturated
non-pitch blob as a player, so real broadcast frames are flooded with spectator,
bench, and camera-operator boxes. This module filters football-relevant people
by their bottom-center "footpoint" against a manually configured, normalized
pitch polygon before tracking. Ball geometry uses an expanded margin because the
ball may be airborne, cross the touchline, or reach the goal area.
"""

from __future__ import annotations

from dataclasses import dataclass

from football_intelligence.domain import BoundingBox, Detection

Polygon = tuple[tuple[float, float], ...]
PERSON_CLASSES = {"player", "goalkeeper", "referee"}


@dataclass(frozen=True)
class PlayingAreaFilter:
    """Accept/reject detections by normalized playing-area geometry.

    ``polygon`` is a sequence of normalized ``(x, y)`` corner points in the
    ``[0, 1]`` range (see the example JSON in the milestone brief). An empty
    polygon disables filtering, preserving the legacy/synthetic behavior.
    """

    polygon: Polygon = ()
    person_tolerance: float = 0.0
    ball_margin: float = 0.10

    @property
    def enabled(self) -> bool:
        return len(self.polygon) >= 3

    @staticmethod
    def footpoint(bbox: BoundingBox) -> tuple[float, float]:
        center_x = bbox.x1 + bbox.width / 2
        return (center_x, bbox.y2)

    def filter(
        self, detections: list[Detection], *, frame_width: int, frame_height: int
    ) -> DetectionFilterResult:
        raw_count = len(detections)
        if not self.enabled or frame_width <= 0 or frame_height <= 0:
            return DetectionFilterResult(
                kept=list(detections), rejected=[], raw_count=raw_count
            )
        kept: list[Detection] = []
        rejected: list[tuple[Detection, str]] = []
        for detection in detections:
            if detection.object_class == "ball":
                cx, cy = detection.bbox.center
                point = (cx / frame_width, cy / frame_height)
                if distance_to_polygon(point, self.polygon) <= self.ball_margin:
                    kept.append(detection)
                else:
                    rejected.append((detection, "outside_pitch"))
            elif detection.object_class in PERSON_CLASSES:
                fx, fy = self.footpoint(detection.bbox)
                point = (fx / frame_width, fy / frame_height)
                if distance_to_polygon(point, self.polygon) <= self.person_tolerance:
                    kept.append(detection)
                else:
                    rejected.append((detection, "outside_pitch"))
            else:
                kept.append(detection)
        return DetectionFilterResult(
            kept=kept, rejected=rejected, raw_count=raw_count
        )


@dataclass
class DetectionFilterResult:
    kept: list[Detection]
    rejected: list[tuple[Detection, str]]
    raw_count: int

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)


def point_in_polygon(point: tuple[float, float], polygon: Polygon) -> bool:
    """Ray-casting inclusion test for a point against a simple polygon."""
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
        ):
            inside = not inside
        j = i
    return inside


def distance_to_polygon(point: tuple[float, float], polygon: Polygon) -> float:
    """Minimum Euclidean distance from a point to any polygon edge."""
    if point_in_polygon(point, polygon):
        return 0.0
    best = float("inf")
    j = len(polygon) - 1
    for i in range(len(polygon)):
        best = min(best, _distance_to_segment(point, polygon[i], polygon[j]))
        j = i
    return best


def _distance_to_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    px, py = point
    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return ((px - sx) ** 2 + (py - sy) ** 2) ** 0.5
    projection = ((px - sx) * dx + (py - sy) * dy) / length_squared
    projection = max(0.0, min(1.0, projection))
    closest_x = sx + projection * dx
    closest_y = sy + projection * dy
    return ((px - closest_x) ** 2 + (py - closest_y) ** 2) ** 0.5


def normalize_polygon(points: list[list[float]] | list[tuple[float, float]]) -> Polygon:
    normalized = tuple((float(x), float(y)) for x, y in points)
    if any(not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0 for x, y in normalized):
        raise ValueError("playing-area polygon points must be normalized to [0, 1]")
    if len(normalized) < 3:
        raise ValueError("playing-area polygon requires at least three points")
    return normalized
