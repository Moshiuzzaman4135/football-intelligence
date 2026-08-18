import cv2
import numpy as np
import pytest

from football_intelligence.detection.color import ColorDetector
from football_intelligence.detection.factory import build_detector


def test_color_detector_finds_synthetic_player_and_ball():
    frame = np.full((180, 320, 3), (30, 130, 30), dtype=np.uint8)
    cv2.rectangle(frame, (80, 50), (110, 140), (255, 0, 0), -1)
    cv2.circle(frame, (180, 110), 5, (255, 255, 255), -1)

    detections = ColorDetector().detect(frame, frame_index=4, timestamp_ms=160)

    assert {detection.object_class for detection in detections} == {"player", "ball"}
    player = next(item for item in detections if item.object_class == "player")
    ball = next(item for item in detections if item.object_class == "ball")
    assert player.bbox.x1 <= 80 < player.bbox.x2
    assert ball.bbox.x1 <= 180 < ball.bbox.x2
    assert all(item.frame_index == 4 and item.timestamp_ms == 160 for item in detections)


def test_color_detector_does_not_label_green_pitch_as_player():
    frame = np.full((180, 320, 3), (30, 130, 30), dtype=np.uint8)

    assert ColorDetector().detect(frame, frame_index=0, timestamp_ms=0) == []


def test_detector_factory_has_explicit_degraded_mode_and_rejects_typos():
    assert isinstance(build_detector("color"), ColorDetector)
    with pytest.raises(ValueError, match="unknown detector"):
        build_detector("mystery")
