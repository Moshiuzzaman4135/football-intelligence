"""Vendor-independent detector protocol."""

from typing import Protocol

import numpy as np

from football_intelligence.domain import Detection


class Detector(Protocol):
    def detect(
        self, frame: np.ndarray, frame_index: int, timestamp_ms: int
    ) -> list[Detection]: ...
