# Football Intelligence Vertical Slice Design

## Scope

Implement M0-M8 as a CPU-capable modular monolith, validate it on a short legal football clip, and preserve extension seams for M9-M16. The system produces candidate intelligence with evidence, not authoritative match data.

## Decisions

FastAPI + Streamlit, SQLite + filesystem, OpenCV + FFmpeg, an in-process semantic bus, replaceable detector/tracker protocols, and a background thread executor provide the smallest deployable shape. Ultralytics YOLO nano and ByteTrack are preferred when the optional ML dependency is installed; a deterministic color/contour detector and IoU tracker keep integration tests and degraded demo operation independent of weight downloads.

## Data flow and interfaces

The API stores an upload and creates a `created` job. Starting it atomically moves to `running` and schedules `Pipeline.run(job_id)`. The pipeline probes video metadata, iterates frames, calculates timestamps, invokes `Detector.detect`, normalizes tracks, accumulates temporal observations, renders overlays, and periodically stores progress. After iteration it derives/deduplicates/fuses candidates, finalizes H.264 output, persists metrics/artifacts, and transitions to `completed`. Stop or failure paths are explicit.

Every detection contains class, confidence, bounding box, frame index, and timestamp. Every track additionally contains track ID and nullable team/pitch values. Every event contains job ID, type, start/end milliseconds, confidence, evidence, sources, review flag, track IDs, and frame references.

## API and UI

The REST API implements the endpoints in the product brief. Media responses support browser playback. The Streamlit UI uploads a clip, starts processing, polls status, shows progress and runtime metrics, plays annotated output, and renders event cards with confidence/evidence. Event seeking is best-effort because Streamlit's native video control does not expose a reliable programmatic seek API.

## Testing

Domain and lifecycle behavior is built test-first. A synthetic generated clip tests the deterministic pipeline on every machine; a separately downloaded, legally reusable football clip exercises real-video ingestion and the selected ML adapter when available. API tests use a temporary SQLite/media root. The final gate runs unit tests, lint, Docker build/smoke, API health, CLI processing, ffprobe validation, and browser/UI startup checks.

## Deferred work

Team clustering, football-specific weights, action spotting, calibration/radar, OCR/audio/VLM, multimodal fusion refinements, RTSP reconnect policy, distributed queues, PostgreSQL, and remote serving are M9-M16. None may block M0-M8.

