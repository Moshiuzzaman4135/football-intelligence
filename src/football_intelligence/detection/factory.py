"""Detector selection kept outside orchestration and API code."""

from football_intelligence.detection.base import Detector
from football_intelligence.detection.color import ColorDetector
from football_intelligence.detection.ultralytics import UltralyticsDetector


def build_detector(
    kind: str,
    *,
    model_name: str = "yolo11n.pt",
    device: str = "cpu",
    confidence: float = 0.25,
) -> Detector:
    normalized = kind.strip().lower()
    if normalized == "color":
        return ColorDetector()
    if normalized == "ultralytics":
        selected_device = None if device == "auto" else device
        return UltralyticsDetector(
            model_name=model_name,
            device=selected_device,
            confidence=confidence,
        )
    raise ValueError(f"unknown detector: {kind}")
