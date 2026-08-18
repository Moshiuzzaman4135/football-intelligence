"""Command-line entry point for reproducible single-video processing."""

import argparse
import json
from pathlib import Path

from football_intelligence.bus import EventBus
from football_intelligence.detection.factory import build_detector
from football_intelligence.pipeline import Pipeline
from football_intelligence.settings import Settings
from football_intelligence.storage import JobRepository
from football_intelligence.tracking.iou import IoUTracker


def process_video(video: Path, data_root: Path, settings: Settings | None = None) -> dict:
    runtime_settings = settings or Settings()
    data_root.mkdir(parents=True, exist_ok=True)
    repository = JobRepository(data_root / "db" / "football_intelligence.db")
    job = repository.create(str(video.resolve()), video.name)
    pipeline = Pipeline(
        repository=repository,
        detector=build_detector(
            runtime_settings.detector,
            model_name=runtime_settings.model_name,
            device=runtime_settings.device,
        ),
        tracker=IoUTracker(),
        output_dir=data_root / "outputs",
        bus=EventBus(),
        max_frame_errors=runtime_settings.max_frame_errors,
    )
    return pipeline.run(job.id).model_dump(mode="json")


def main() -> None:
    settings = Settings()
    parser = argparse.ArgumentParser(description="Football video intelligence")
    subparsers = parser.add_subparsers(dest="command", required=True)
    process = subparsers.add_parser("process", help="process one local video")
    process.add_argument("video", type=Path)
    process.add_argument(
        "--data-root",
        type=Path,
        default=settings.data_root,
    )
    arguments = parser.parse_args()
    if arguments.command == "process":
        print(
            json.dumps(
                process_video(arguments.video, arguments.data_root, settings=settings), indent=2
            )
        )


if __name__ == "__main__":
    main()
