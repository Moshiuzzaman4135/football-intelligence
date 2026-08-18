"""Generate a tiny deterministic clip for an offline event-timeline demo."""

import argparse
from pathlib import Path

import cv2
import numpy as np


def generate_demo_clip(path: str | Path, duration_seconds: int = 30) -> Path:
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(destination), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (640, 360)
    )
    if not writer.isOpened():
        raise RuntimeError(f"could not create demo clip: {destination}")
    try:
        for frame_index in range(duration_seconds * 10):
            frame = np.full((360, 640, 3), (35, 125, 35), dtype=np.uint8)
            cv2.line(frame, (320, 0), (320, 360), (230, 230, 230), 3)
            cv2.circle(frame, (320, 180), 58, (230, 230, 230), 3)
            cv2.rectangle(frame, (180, 130), (215, 245), (255, 0, 0), -1)
            cv2.rectangle(frame, (470, 120), (505, 235), (0, 0, 255), -1)
            if frame_index < 10:
                ball_x = 222 + frame_index
            else:
                ball_x = min(600, 232 + (frame_index - 9) * 30)
            cv2.circle(frame, (ball_x, 215), 7, (255, 255, 255), -1)
            writer.write(frame)
    finally:
        writer.release()
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the offline football demo clip")
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    print(generate_demo_clip(arguments.output))


if __name__ == "__main__":
    main()
