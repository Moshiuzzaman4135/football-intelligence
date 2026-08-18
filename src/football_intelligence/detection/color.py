"""Deterministic color/contour detector for tests and degraded operation."""

import cv2
import numpy as np

from football_intelligence.domain import BoundingBox, Detection


class ColorDetector:
    SATURATED_LOWER = (0, 90, 45)
    SATURATED_UPPER = (179, 255, 255)
    GREEN_LOWER = (35, 60, 35)
    GREEN_UPPER = (90, 255, 255)
    BALL_LOWER = (0, 0, 205)
    BALL_UPPER = (179, 55, 255)
    PLAYER_CONFIDENCE = 0.65
    PLAYER_MIN_AREA = 150
    PLAYER_MAX_AREA_RATIO = 0.25
    BALL_CONFIDENCE = 0.7
    BALL_MIN_AREA = 12
    BALL_MAX_AREA = 500

    @property
    def provenance_config(self) -> dict[str, str]:
        """Return the exact output-affecting parameters used by ``detect``."""
        return {
            "ball_confidence": f"{self.BALL_CONFIDENCE:g}",
            "ball_hsv": _range_text(self.BALL_LOWER, self.BALL_UPPER),
            "ball_max_area": f"{self.BALL_MAX_AREA:g}",
            "ball_min_area": f"{self.BALL_MIN_AREA:g}",
            "green_hsv": _range_text(self.GREEN_LOWER, self.GREEN_UPPER),
            "player_confidence": f"{self.PLAYER_CONFIDENCE:g}",
            "player_max_area_ratio": f"{self.PLAYER_MAX_AREA_RATIO:g}",
            "player_min_area": f"{self.PLAYER_MIN_AREA:g}",
            "saturated_hsv": _range_text(
                self.SATURATED_LOWER, self.SATURATED_UPPER
            ),
        }

    def detect(
        self, frame: np.ndarray, frame_index: int, timestamp_ms: int
    ) -> list[Detection]:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        saturated = cv2.inRange(hsv, self.SATURATED_LOWER, self.SATURATED_UPPER)
        green = cv2.inRange(hsv, self.GREEN_LOWER, self.GREEN_UPPER)
        player_mask = cv2.bitwise_and(saturated, cv2.bitwise_not(green))
        white_mask = cv2.inRange(hsv, self.BALL_LOWER, self.BALL_UPPER)

        detections = self._contours(
            player_mask,
            "player",
            self.PLAYER_CONFIDENCE,
            min_area=self.PLAYER_MIN_AREA,
            max_area=(
                frame.shape[0] * frame.shape[1] * self.PLAYER_MAX_AREA_RATIO
            ),
            frame_index=frame_index,
            timestamp_ms=timestamp_ms,
        )
        detections.extend(
            self._contours(
                white_mask,
                "ball",
                self.BALL_CONFIDENCE,
                min_area=self.BALL_MIN_AREA,
                max_area=self.BALL_MAX_AREA,
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


def _range_text(lower: tuple[int, int, int], upper: tuple[int, int, int]) -> str:
    return f"{','.join(map(str, lower))}:{','.join(map(str, upper))}"
