# Architecture

## Chosen shape

The first demonstrable release is a modular monolith: FastAPI owns lifecycle and persistence, a background executor runs one pipeline per job, Streamlit calls the API, SQLite stores metadata, and the filesystem stores source/output media. An in-process typed event bus emits semantic lifecycle and processing events. These boundaries permit later Redis/Celery deployment without inventing services before workload evidence requires them.

```text
Upload/local/RTSP -> source probe/frame iterator
                  -> Detector protocol -> Tracker protocol
                  -> temporal observations -> candidate rules -> fusion
                  -> SQLite metadata + annotated-frame renderer
                  -> FFmpeg H.264 MP4 -> FastAPI -> Streamlit timeline
```

## Components

- `domain.py`: Pydantic schemas for detections, tracks, evidence, events, video metadata, and jobs.
- `timebase.py`: frame/time conversion with explicit FPS validation.
- `bus.py`: synchronous semantic event envelope/publisher with subscriber isolation.
- `storage.py`: SQLite repositories and atomic job transitions; media paths remain outside SQL blobs.
- `video.py`: source validation, OpenCV iteration, metadata probe, and output finalization.
- `detection/base.py` and `detection/ultralytics.py`: normalized detector contract and YOLO adapter.
- `tracking/base.py` and `tracking/iou.py`: normalized contract and lightweight deterministic fallback; Ultralytics ByteTrack is the preferred ML path when installed.
- `events.py`: temporal possession/kick/pass/shot/out/stoppage candidate rules plus deduplication and evidence fusion.
- `overlay.py`: labels, confidence, team, event banners, timestamps, and trajectory trails.
- `pipeline.py`: orchestration, progress, stop checks, per-frame fault budget, metrics, and artifact completion.
- `api.py`: REST lifecycle/media endpoints and background execution.
- `ui.py`: upload/start/progress/video/timeline/evidence showcase.

## Normalized contracts

`Detector.detect(frame, frame_index, timestamp_ms) -> list[Detection]` and `Tracker.update(detections, frame_index, timestamp_ms) -> list[TrackObservation]` isolate vendor details. `TrackObservation.track_id` means visual continuity only. The event schema preserves raw evidence objects and producer names after fusion.

## Internal events

The bus supports `job.created`, `job.started`, `video.opened`, `frame.ready`, `detection.completed`, `tracking.updated`, `event.candidate`, `event.fused`, `overlay.frame.ready`, `overlay.video.completed`, `job.completed`, `job.failed`, and `job.stopped`. Handlers must not make frame processing fail; subscriber errors are logged with the envelope ID.

## Error and fallback behavior

Invalid/unreadable sources fail before inference. Isolated frame/decode/inference failures increment an error counter and processing continues within a configured budget. Stop requests are checked between frames. Device selection follows local CUDA, optional remote experiment, smaller model, CPU, optional disablement. The mandatory path never calls SSH at runtime.

## Deployment

Docker Compose provides reproducible CPU backend/UI containers and a shared media volume. Native CUDA may be used when a compatible Python/PyTorch stack is verified. Redis/Celery, PostgreSQL, and a remote inference API are deferred until the single-node workload or optional heavy model justifies them.

