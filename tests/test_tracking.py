from football_intelligence.domain import BoundingBox, Detection
from football_intelligence.tracking.iou import IoUTracker


def detection(object_class, bbox, frame_index):
    return Detection(
        object_class=object_class,
        confidence=0.8,
        bbox=BoundingBox(x1=bbox[0], y1=bbox[1], x2=bbox[2], y2=bbox[3]),
        frame_index=frame_index,
        timestamp_ms=frame_index * 40,
    )


def test_iou_tracker_keeps_visual_id_for_overlapping_detection():
    tracker = IoUTracker(iou_threshold=0.2, max_missed=2)

    first = tracker.update([detection("player", (10, 10, 40, 80), 0)], 0, 0)
    second = tracker.update([detection("player", (14, 10, 44, 80), 1)], 1, 40)

    assert first[0].track_id == second[0].track_id
    assert second[0].object_class == "player"


def test_iou_tracker_does_not_cross_match_classes():
    tracker = IoUTracker(iou_threshold=0.2, max_missed=2)
    first = tracker.update([detection("player", (10, 10, 40, 80), 0)], 0, 0)
    second = tracker.update([detection("ball", (10, 10, 40, 80), 1)], 1, 40)

    assert first[0].track_id != second[0].track_id


def test_iou_tracker_expires_unseen_tracks():
    tracker = IoUTracker(iou_threshold=0.2, max_missed=1)
    first = tracker.update([detection("player", (10, 10, 40, 80), 0)], 0, 0)
    tracker.update([], 1, 40)
    tracker.update([], 2, 80)
    returned = tracker.update([detection("player", (10, 10, 40, 80), 3)], 3, 120)

    assert first[0].track_id != returned[0].track_id


def test_iou_tracker_keeps_fast_ball_id_by_nearby_center():
    tracker = IoUTracker(iou_threshold=0.2, max_missed=1, ball_max_distance=40)

    first = tracker.update([detection("ball", (100, 100, 110, 110), 0)], 0, 0)
    second = tracker.update([detection("ball", (130, 100, 140, 110), 1)], 1, 100)

    assert first[0].track_id == second[0].track_id
