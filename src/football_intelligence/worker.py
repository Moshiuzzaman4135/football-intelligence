"""Explicit worker entry point for a previously created job."""

import argparse
from pathlib import Path

from football_intelligence.bus import EventBus
from football_intelligence.detection.factory import build_detector
from football_intelligence.pipeline import Pipeline
from football_intelligence.settings import Settings
from football_intelligence.storage import JobRepository
from football_intelligence.tracking.iou import IoUTracker


def main() -> None:
    settings = Settings()
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=settings.data_root,
    )
    arguments = parser.parse_args()
    repository = JobRepository(arguments.data_root / "db" / "football_intelligence.db")
    pipeline = Pipeline(
        repository=repository,
        detector=build_detector(
            settings.detector,
            model_name=settings.model_name,
            device=settings.device,
        ),
        tracker=IoUTracker(),
        output_dir=arguments.data_root / "outputs",
        bus=EventBus(),
        max_frame_errors=settings.max_frame_errors,
    )
    pipeline.run(arguments.job_id)


if __name__ == "__main__":
    main()
