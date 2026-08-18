"""Measured runtime identity for restart-safe full-match manifests."""

from __future__ import annotations

import inspect
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import cv2

from football_intelligence.detection.color import ColorDetector
from football_intelligence.detection.ultralytics import UltralyticsDetector
from football_intelligence.fullmatch.manifest import RuntimeProvenance
from football_intelligence.fullmatch.media import sha256_file


def measure_runtime_provenance(detector, tracker, ocr_engine) -> RuntimeProvenance:
    detector_name = _qualified_name(detector)
    detector_model = getattr(detector, "model_name", "runtime-injected")
    detector_device = str(getattr(detector, "device", "cpu") or "auto")
    detector_config: dict[str, str] = {}
    detector_model_sha256 = "0" * 64
    if isinstance(detector, ColorDetector):
        detector_model = "deterministic-color-thresholds-v1"
        detector_device = "cpu"
        detector_framework = "opencv-python-headless"
        detector_version = cv2.__version__
        detector_config = detector.provenance_config
    elif isinstance(detector, UltralyticsDetector):
        detector_framework = "ultralytics"
        detector_version = _package_version("ultralytics")
        detector_config = {"confidence": f"{detector.confidence:g}"}
        detector_model_sha256 = sha256_file(_ultralytics_weight_path(detector))
    else:
        detector_framework = type(detector).__module__.partition(".")[0]
        detector_version = _package_version(detector_framework)

    tracker_config = {
        name: f"{getattr(tracker, name):g}"
        for name in ("iou_threshold", "max_missed", "ball_max_distance")
        if hasattr(tracker, name)
    }
    return RuntimeProvenance(
        detector=detector_name,
        detector_model=str(detector_model),
        detector_device=detector_device,
        detector_framework=detector_framework,
        detector_version=detector_version,
        detector_config=detector_config,
        detector_model_sha256=detector_model_sha256,
        detector_adapter_sha256=_source_hash(detector),
        tracker=_qualified_name(tracker),
        tracker_config=tracker_config,
        tracker_adapter_sha256=_source_hash(tracker),
        ocr_engine=_qualified_name(ocr_engine),
        ocr_model=str(getattr(ocr_engine, "model_name", "runtime-injected")),
        ocr_version=str(getattr(ocr_engine, "version", "runtime-injected")),
        ocr_model_sha256=str(getattr(ocr_engine, "model_sha256", "0" * 64)),
        ocr_adapter_sha256=_source_hash(ocr_engine),
    )


def _ultralytics_weight_path(detector: UltralyticsDetector) -> Path:
    candidates = [
        Path(detector.model_name),
        Path(str(getattr(detector.model, "ckpt_path", ""))),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValueError("Ultralytics full-match processing requires readable weight bytes")


def _source_hash(adapter: object) -> str:
    source_path = inspect.getsourcefile(type(adapter))
    if source_path is None:
        raise ValueError(
            f"adapter source is not measurable: {_qualified_name(adapter)}"
        )
    return sha256_file(source_path)


def _qualified_name(adapter: object) -> str:
    adapter_type = type(adapter)
    return f"{adapter_type.__module__}.{adapter_type.__qualname__}"


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "not-installed"
