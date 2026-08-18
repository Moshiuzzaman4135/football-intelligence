"""Fixed-resolution screen-space player density heat maps."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np

from football_intelligence.domain import TrackObservation


class ScreenSpaceHeatMap:
    columns = 32
    rows = 18
    label = "Screen-space player density (not pitch calibrated)"

    def __init__(self) -> None:
        self._counts = np.zeros((self.rows, self.columns), dtype=np.int64)

    @property
    def observation_count(self) -> int:
        return int(self._counts.sum())

    def observe(
        self,
        tracks: Sequence[TrackObservation],
        *,
        frame_width: int,
        frame_height: int,
    ) -> None:
        if frame_width <= 0 or frame_height <= 0:
            raise ValueError("frame dimensions must be positive")
        for track in tracks:
            if track.object_class not in {"player", "goalkeeper"}:
                continue
            x, y = track.bbox.center
            if not (0 <= x < frame_width and 0 <= y < frame_height):
                continue
            column = min(self.columns - 1, int(x / frame_width * self.columns))
            row = min(self.rows - 1, int(y / frame_height * self.rows))
            self._counts[row, column] += 1

    def to_counts(self) -> list[list[int]]:
        return self._counts.tolist()

    @classmethod
    def from_counts(cls, counts: Sequence[Sequence[int]]) -> ScreenSpaceHeatMap:
        array = np.asarray(counts, dtype=np.int64)
        if array.shape != (cls.rows, cls.columns) or np.any(array < 0):
            raise ValueError("heat-map counts must be a non-negative 32x18 grid")
        heatmap = cls()
        heatmap._counts = array.copy()
        return heatmap

    def write_png(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        maximum = int(self._counts.max())
        if maximum:
            density = np.uint8(self._counts / maximum * 255)
        else:
            density = np.zeros_like(self._counts, dtype=np.uint8)
        colored = cv2.applyColorMap(density, cv2.COLORMAP_TURBO)
        resized = cv2.resize(colored, (640, 360), interpolation=cv2.INTER_NEAREST)
        canvas = np.zeros((400, 640, 3), dtype=np.uint8)
        canvas[40:, :] = resized
        cv2.putText(
            canvas,
            self.label,
            (10, 27),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        temporary = path.with_name(f"{path.stem}.partial{path.suffix}")
        if not cv2.imwrite(str(temporary), canvas):
            raise OSError(f"could not write heat map {temporary}")
        temporary.replace(path)
        return path
