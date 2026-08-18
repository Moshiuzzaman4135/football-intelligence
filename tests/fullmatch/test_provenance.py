import hashlib
from pathlib import Path

import pytest

import football_intelligence.fullmatch.provenance as provenance_module
from football_intelligence.detection.color import ColorDetector
from football_intelligence.detection.ultralytics import UltralyticsDetector
from football_intelligence.fullmatch.ocr import FakeOcrEngine, TesseractCliOcrEngine
from football_intelligence.fullmatch.provenance import measure_runtime_provenance
from football_intelligence.tracking.iou import IoUTracker


class StubModel:
    pass


def _write_tesseract(path: Path, version: str) -> None:
    path.write_text(f"#!/bin/sh\nprintf 'tesseract {version}\\n'\n", encoding="utf-8")
    path.chmod(0o755)


def test_tesseract_provenance_measures_executable_and_configured_model_bytes(
    tmp_path: Path,
):
    executable = tmp_path / "tesseract"
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    model = tessdata / "eng.traineddata"
    model.write_bytes(b"model-v1")
    _write_tesseract(executable, "9.1.2")

    first = TesseractCliOcrEngine(tessdata, executable=str(executable))
    first_hash = first.model_sha256
    model.write_bytes(b"model-v2")
    _write_tesseract(executable, "9.2.0")
    second = TesseractCliOcrEngine(tessdata, executable=str(executable))

    assert first.version == "9.1.2"
    assert second.version == "9.2.0"
    assert first_hash == hashlib.sha256(b"model-v1").hexdigest()
    assert second.model_sha256 == hashlib.sha256(b"model-v2").hexdigest()
    assert first.model_sha256 != second.model_sha256


def test_runtime_provenance_hashes_adapter_sources_and_exact_color_parameters():
    detector = ColorDetector()
    measured = measure_runtime_provenance(
        detector, IoUTracker(), FakeOcrEngine([])
    )

    assert measured.detector_model == "deterministic-color-thresholds-v1"
    assert measured.detector_device == "cpu"
    assert measured.detector_config == {
        "ball_confidence": "0.7",
        "ball_hsv": "0,0,205:179,55,255",
        "ball_max_area": "500",
        "ball_min_area": "12",
        "green_hsv": "35,60,35:90,255,255",
        "player_confidence": "0.65",
        "player_max_area_ratio": "0.25",
        "player_min_area": "150",
        "saturated_hsv": "0,90,45:179,255,255",
    }
    assert measured.detector_adapter_sha256 != "0" * 64
    assert measured.tracker_adapter_sha256 != "0" * 64
    assert measured.ocr_adapter_sha256 != "0" * 64
    assert measured.detector_config == detector.provenance_config


def test_ultralytics_provenance_changes_when_weight_bytes_change(tmp_path: Path):
    weights = tmp_path / "model.pt"
    weights.write_bytes(b"weights-v1")
    first = measure_runtime_provenance(
        UltralyticsDetector(model_name=str(weights), model=StubModel()),
        IoUTracker(),
        FakeOcrEngine([]),
    )
    weights.write_bytes(b"weights-v2")
    second = measure_runtime_provenance(
        UltralyticsDetector(model_name=str(weights), model=StubModel()),
        IoUTracker(),
        FakeOcrEngine([]),
    )

    assert first.detector_model_sha256 == hashlib.sha256(b"weights-v1").hexdigest()
    assert second.detector_model_sha256 == hashlib.sha256(b"weights-v2").hexdigest()
    assert first.detector_model_sha256 != second.detector_model_sha256


def test_provenance_refuses_missing_tessdata_and_unresolved_weight_bytes(
    tmp_path: Path,
):
    executable = tmp_path / "tesseract"
    _write_tesseract(executable, "9.1.2")
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()

    with pytest.raises(FileNotFoundError):
        TesseractCliOcrEngine(tessdata, executable=str(executable))
    with pytest.raises(ValueError, match="readable weight bytes"):
        measure_runtime_provenance(
            UltralyticsDetector(
                model_name=str(tmp_path / "missing.pt"), model=StubModel()
            ),
            IoUTracker(),
            FakeOcrEngine([]),
        )


def test_provenance_refuses_adapter_without_measurable_source(monkeypatch):
    monkeypatch.setattr(provenance_module.inspect, "getsourcefile", lambda value: None)

    with pytest.raises(ValueError, match="adapter source"):
        measure_runtime_provenance(
            ColorDetector(), IoUTracker(), FakeOcrEngine([])
        )
