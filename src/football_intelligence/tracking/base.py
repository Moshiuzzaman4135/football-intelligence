"""Vendor-independent tracker protocol."""

from typing import Protocol

from football_intelligence.domain import Detection, TrackObservation


class Tracker(Protocol):
    def update(
        self, detections: list[Detection], frame_index: int, timestamp_ms: int
    ) -> list[TrackObservation]: ...
