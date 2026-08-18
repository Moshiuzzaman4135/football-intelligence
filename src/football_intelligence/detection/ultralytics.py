"""Lazy Ultralytics adapter; the optional package and weights are never bundled."""

from typing import Any

import numpy as np

from football_intelligence.domain import BoundingBox, Detection

CLASS_MAP = {
    "person": "player",
    "sports ball": "ball",
    "player": "player",
    "goalkeeper": "goalkeeper",
    "referee": "referee",
    "ball": "ball",
}


class UltralyticsDetector:
    def __init__(
        self,
        model_name: str = "yolo11n.pt",
        *,
        device: str | None = "cpu",
        confidence: float = 0.25,
        model: Any | None = None,
    ):
        self.model_name = model_name
        if model is None:
            try:
                from ultralytics import YOLO
            except ImportError as error:
                raise RuntimeError(
                    "Ultralytics is optional; install football-intelligence[ml]"
                ) from error
            model = YOLO(model_name)
        self.model = model
        self.device = device
        self.confidence = confidence

    def detect(
        self, frame: np.ndarray, frame_index: int, timestamp_ms: int
    ) -> list[Detection]:
        results = self.model.predict(
            source=frame,
            device=self.device,
            conf=self.confidence,
            verbose=False,
        )
        if not results or results[0].boxes is None:
            return []
        result = results[0]
        coordinates = _to_list(result.boxes.xyxy)
        confidences = _to_list(result.boxes.conf)
        class_ids = _to_list(result.boxes.cls)
        detections = []
        for coordinate, confidence, class_id in zip(
            coordinates, confidences, class_ids, strict=True
        ):
            if float(confidence) < self.confidence:
                continue
            source_class = str(result.names[int(class_id)]).lower()
            object_class = CLASS_MAP.get(source_class)
            if object_class is None:
                continue
            x1, y1, x2, y2 = (float(value) for value in coordinate)
            detections.append(
                Detection(
                    object_class=object_class,
                    confidence=float(confidence),
                    bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
                    frame_index=frame_index,
                    timestamp_ms=timestamp_ms,
                )
            )
        return detections


def _to_list(value: Any) -> list:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    return value.tolist()
