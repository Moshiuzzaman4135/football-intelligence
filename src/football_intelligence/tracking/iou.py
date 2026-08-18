"""Lightweight class-aware IoU tracker used as the deterministic fallback."""

from dataclasses import dataclass
from math import dist

from football_intelligence.domain import BoundingBox, Detection, TrackObservation


def intersection_over_union(left: BoundingBox, right: BoundingBox) -> float:
    x1 = max(left.x1, right.x1)
    y1 = max(left.y1, right.y1)
    x2 = min(left.x2, right.x2)
    y2 = min(left.y2, right.y2)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = left.width * left.height + right.width * right.height - intersection
    return intersection / union if union else 0.0


@dataclass
class _Track:
    track_id: int
    object_class: str
    bbox: BoundingBox
    missed: int = 0


class IoUTracker:
    def __init__(
        self,
        iou_threshold: float = 0.25,
        max_missed: int = 8,
        ball_max_distance: float = 50,
    ):
        self.iou_threshold = iou_threshold
        self.max_missed = max_missed
        self.ball_max_distance = ball_max_distance
        self._tracks: dict[int, _Track] = {}
        self._next_track_id = 1

    def update(
        self, detections: list[Detection], frame_index: int, timestamp_ms: int
    ) -> list[TrackObservation]:
        for track in self._tracks.values():
            track.missed += 1

        observations = []
        claimed_tracks: set[int] = set()
        for detection in sorted(detections, key=lambda item: item.confidence, reverse=True):
            candidates = [
                (intersection_over_union(track.bbox, detection.bbox), track)
                for track in self._tracks.values()
                if track.object_class == detection.object_class
                and track.track_id not in claimed_tracks
            ]
            score, track = max(candidates, key=lambda item: item[0], default=(0.0, None))
            if detection.object_class == "ball" and (track is None or score < self.iou_threshold):
                distance_candidates = [
                    (dist(candidate.bbox.center, detection.bbox.center), candidate)
                    for candidate in self._tracks.values()
                    if candidate.object_class == "ball"
                    and candidate.track_id not in claimed_tracks
                ]
                distance_score, nearby_track = min(
                    distance_candidates, key=lambda item: item[0], default=(float("inf"), None)
                )
                if distance_score <= self.ball_max_distance:
                    track = nearby_track
                    score = self.iou_threshold
            if track is None or score < self.iou_threshold:
                track = _Track(
                    track_id=self._next_track_id,
                    object_class=detection.object_class,
                    bbox=detection.bbox,
                )
                self._tracks[track.track_id] = track
                self._next_track_id += 1
            else:
                track.bbox = detection.bbox
                track.missed = 0
            claimed_tracks.add(track.track_id)
            observations.append(
                TrackObservation(
                    track_id=track.track_id,
                    object_class=detection.object_class,
                    bbox=detection.bbox,
                    confidence=detection.confidence,
                    timestamp_ms=timestamp_ms,
                    frame_index=frame_index,
                )
            )

        self._tracks = {
            track_id: track
            for track_id, track in self._tracks.items()
            if track.missed <= self.max_missed or track_id in claimed_tracks
        }
        return observations
