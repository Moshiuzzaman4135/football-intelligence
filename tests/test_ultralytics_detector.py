import numpy as np

from football_intelligence.detection.ultralytics import UltralyticsDetector


class FakeBoxes:
    xyxy = np.array([[10, 20, 40, 90], [100, 110, 108, 118], [0, 0, 5, 5]])
    conf = np.array([0.91, 0.76, 0.2])
    cls = np.array([0, 32, 0])


class FakeResult:
    boxes = FakeBoxes()
    names = {0: "person", 32: "sports ball"}


class FakeModel:
    def __init__(self):
        self.calls = []

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        return [FakeResult()]


def test_ultralytics_adapter_normalizes_coco_classes_and_filters_confidence():
    model = FakeModel()
    detector = UltralyticsDetector(model=model, device="cpu", confidence=0.25)
    frame = np.zeros((180, 320, 3), dtype=np.uint8)

    detections = detector.detect(frame, frame_index=5, timestamp_ms=200)

    assert [item.object_class for item in detections] == ["player", "ball"]
    assert [item.confidence for item in detections] == [0.91, 0.76]
    assert all(item.frame_index == 5 and item.timestamp_ms == 200 for item in detections)
    assert model.calls[0]["device"] == "cpu"
    assert model.calls[0]["conf"] == 0.25
