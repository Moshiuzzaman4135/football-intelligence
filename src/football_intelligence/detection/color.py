"""Deterministic color/contour detector for tests and degraded operation."""

import cv2
import numpy as np

from football_intelligence.domain import BoundingBox, Detection


class ColorDetector:
    def detect(
        self, frame: np.ndarray, frame_index: int, timestamp_ms: int
    ) -> list[Detection]:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        saturated = cv2.inRange(hsv, (0, 90, 45), (179, 255, 255))
        green = cv2.inRange(hsv, (35, 60, 35), (90, 255, 255))
        player_mask = cv2.bitwise_and(saturated, cv2.bitwise_not(green))
        white_mask = cv2.inRange(hsv, (0, 0, 205), (179, 55, 255))

        detections = self._contours(
            player_mask,
            "player",
            0.65,
            min_area=150,
            max_area=frame.shape[0] * frame.shape[1] * 0.25,
            frame_index=frame_index,
            timestamp_ms=timestamp_ms,
        )
        detections.extend(
            self._contours(
                white_mask,
                "ball",
                0.7,
                min_area=12,
                max_area=500,
                frame_index=frame_index,
                timestamp_ms=timestamp_ms,
            )
        )
        return detections

    @staticmethod
    def _contours(
        mask: np.ndarray,
        object_class: str,
        confidence: float,
        *,
        min_area: float,
        max_area: float,
        frame_index: int,
        timestamp_ms: int,
    ) -> list[Detection]:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if not min_area <= area <= max_area:
                continue
            x, y, width, height = cv2.boundingRect(contour)
            detections.append(
                Detection(
                    object_class=object_class,
                    confidence=confidence,
                    bbox=BoundingBox(x1=x, y1=y, x2=x + width, y2=y + height),
                    frame_index=frame_index,
                    timestamp_ms=timestamp_ms,
                )
            )
        return detections
