# Interview Prep: Football Video Intelligence

## Simple project summary

This project takes a football video, detects visible people/ball, assigns visual tracking IDs, creates evidence-backed event candidates, draws an annotated video, and exposes results through FastAPI and Streamlit. It also has a production-oriented foundation for resumable full-match uploads to MinIO.

## What was used and why

- **FastAPI:** job, upload, status, event, track, and video APIs.
- **Pydantic:** validated domain contracts for detections, tracks, events, stages, OCR/calibration placeholders, artifacts, and upload sessions.
- **OpenCV:** frame reading, deterministic fallback detection, drawing boxes/trails/banners, and video-frame rendering.
- **FFmpeg/ffprobe:** browser-compatible H.264 output, audio preservation, and media validation.
- **YOLO11n adapter:** optional real person/ball inference; tested on the local RTX 3050. It is not bundled because licensing and role coverage need care.
- **IoU tracker:** lightweight visual IDs for the working demo. IDs are not player identities.
- **Temporal heuristics:** kick candidates use motion plus proximity across frames instead of guessing from one image.
- **SQLite:** simple short-demo job/event/track metadata.
- **PostgreSQL + SQLAlchemy + Alembic:** durable full-match job/stage/upload records, migrations, compare-and-set updates, leases, and recovery.
- **MinIO/S3 multipart:** large uploads bypass FastAPI memory, use 16 MiB slices, resume after interruption, and validate size/ETag/SHA-256 before creating a job.
- **Docker Compose:** reproducible backend/UI/PostgreSQL/MinIO startup with health checks and migrations.
- **pytest + Ruff:** behavior, concurrency, failure recovery, API, migration, media, and code-quality verification.

## End-to-end flow that works today

```text
short video -> frames -> detection -> visual tracking -> temporal candidate
            -> overlay frames -> H.264 annotated MP4 -> API -> Streamlit timeline
```

For large files, the implemented control-plane flow is:

```text
browser/API client -> presigned 16 MiB MinIO parts -> durable upload session
                   -> size/ETag/SHA validation -> deterministic job record
```

The next engineer must connect that `s3://` job to the restartable analysis runner.

## Good interview talking points

1. **Truthful AI output:** events are candidates with confidence, evidence, source, and `needs_review`; the system does not claim Opta-level accuracy.
2. **Replaceable ML boundaries:** detector, tracker, OCR, calibration, and action spotting use adapters so one vendor/model does not control the domain schema.
3. **Large-upload reliability:** media goes directly to object storage; SQL records lifecycle state; ambiguous S3/database acknowledgements replay to the same job.
4. **Concurrency correctness:** versioned compare-and-set transitions, leases, bounded retries, deterministic IDs, and retryable cleanup prevent duplicate or orphaned work.
5. **Media integrity:** output is temporary until FFmpeg/ffprobe validates codec, geometry, FPS, duration, frame count, and playback compatibility.
6. **GPU placement:** small models run locally; the RTX 3080 is reserved for heavier experiments; the core remains usable without SSH.
7. **Licensing awareness:** GPL/AGPL or unclear model weights are benchmarked behind boundaries instead of silently shipped.

## Honest current limitations

The default detector is synthetic/degraded on real broadcasts; IoU tracking can switch IDs; full-match processing, OCR, heat maps, pitch calibration, team classification, and action spotting are not implemented yet. Full-match upload is ready, but the uploaded job still needs the chunk runner described in `docs/DEEPSEEK_HANDOFF.md`.
