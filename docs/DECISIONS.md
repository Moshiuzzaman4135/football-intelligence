# Decisions

## 2026-08-18: modular monolith before distributed workers

Use an in-process typed bus and background executor for M0-M8. This keeps the demo reproducible while preserving semantic topics and adapter boundaries. Redis/Celery becomes an evidence-driven deployment change, not a prerequisite.

## 2026-08-18: SQLite and filesystem

SQLite stores metadata; filesystem paths refer to media. PostgreSQL and object storage are deferred because setup would not improve the single-user vertical slice.

## 2026-08-18: Python 3.11/3.12 application runtime

The Fedora host has Python 3.14, while current video/ML wheels commonly trail it. Docker/native Python 3.12 provides a compatible baseline. The system interpreter remains untouched.

## 2026-08-18: deterministic degraded path plus optional Ultralytics

A simple image-based detector and IoU tracker make tests and degraded processing reproducible. The showcase prefers Ultralytics YOLO nano + ByteTrack when weights and a compatible runtime are available. This fallback is labeled; it is not presented as equivalent model accuracy.

## 2026-08-18: local-first GPU placement

The core path uses the local RTX 3050 if verified, then CPU. The remote RTX 3080 is for isolated model evaluation and optional heavy inference only; application runtime never depends on SSH.

## 2026-08-18: optional ML licensing boundary

Ultralytics and the published four-class YOLOv8x weight are AGPL-3.0 (or require an Enterprise license for incompatible deployment). Keep them in an optional `ml` extra and never bundle weights. The credential-free core uses OpenCV/IoU and describes its accuracy honestly.

## 2026-08-18: default demo detector remains deterministic

The color detector is the default only so the complete offline mechanics and event timeline are reproducible without a weight download or license decision. A real Pexels clip exposed severe false positives (17,694 observations), while the optional YOLO11n run produced 1,219 much cleaner observations. Therefore documentation labels the color path as synthetic/degraded and never as real-football accuracy.

## 2026-08-18: local GPU was sufficient

YOLO11n processed the selected real clip successfully on the local RTX 3050, so no remote packages, files, weights, or service were created. This follows the cheapest-sufficient-compute rule and keeps the application independent of SSH. The RTX 3080 remains a later four-class/action-model evaluation target.

## 2026-08-18: host ports 8010 and 8510

An unrelated existing service owns host port 8000. Compose maps the backend to 8010 and Streamlit to 8510 by default, with environment overrides, while container-to-container traffic still uses 8000. The unrelated service was left untouched.

## 2026-08-18: generated fixture is the known event demonstration

A code-generated 30-second fixture provides deterministic rapid ball movement near a tracked player. It satisfies the demo-duration target, validates honest evidence flow, and produces a known kick candidate. The downloaded real clip remains the visual detector benchmark and is not forced to produce semantic events it does not support.

## 2026-08-18: atomic lifecycle and bounded local admission

SQLite transitions use `BEGIN IMMEDIATE` plus status-qualified updates. The API reserves `created -> running` before enqueueing, rejects duplicate starts, bounds admitted running/queued jobs at four, and persists background model-initialization failures. Progress updates cannot overwrite a concurrent status change.

## 2026-08-18: media integrity is a completion condition

Premature decode EOF fails the job. A recoverable inference/frame exception writes the unchanged source frame so later timestamps do not shift. FFmpeg no longer truncates to a shorter audio stream, and the finalized output must pass codec, geometry, FPS, frame-count, and duration checks before the job becomes completed.

## 2026-08-18: compact summaries and persisted provenance

Frame observations remain in SQLite for this short-clip prototype, but the public `/tracks` endpoint returns compact per-track summaries. Source and output probes plus detector/model/device/framework metadata are persisted with the job. Pagination and observation retention policies remain future production work.

## 2026-08-18: local-only non-root demo containers

Backend/UI ports bind to `127.0.0.1` by default and both containers run as a non-root UID/GID selected by `FOOTBALL_UID`/`FOOTBALL_GID` (default 1000). This is still an unauthenticated local showcase; any nonlocal deployment requires authentication, rate limiting, and a deliberate network policy.

## 2026-08-18: cancellation wins lifecycle races

Finalization is one SQLite transaction that either completes a still-running job or stops a job whose cancellation arrived first. A reserved worker entering after a stop request transitions directly to stopped, and background initialization errors do not turn a concurrent cancellation into failure. Barrier-based tests cover both cancellation windows.

## 2026-08-18: direct multipart transfer with validation before job creation

Full-match media uses opaque S3 keys and 16 MiB multipart parts. FastAPI returns presigned URLs, required signed length/checksum headers, and part/status metadata but never proxies part bodies. Completion intent and a preallocated job ID are persisted before irreversible S3 completion. Missing multipart acknowledgements recover by probing and streaming the completed object; deterministic job insertion makes database acknowledgement loss replay-safe. Upload state uses versioned SQL compare-and-set transitions, while short per-upload locks avoid blocking unrelated validation streams. Compose uses separate internal and browser-visible MinIO endpoints and separate root/application credentials. The original short-clip filesystem endpoint remains unchanged until the web migration is complete.

## 2026-08-18: model candidates stay behind legal and quality gates

Keep CALF's 17-action ONNX conversion as the main action-spotting qualification target. AdaSpot-small is an optional MIT benchmark, not the default: its released SoccerNet Ball model covers ten actions and omits goal/free-kick, and its preprocessing/export path needs parity fixes. T-DEED is excluded from the default distributable because its repository is GPL-3.0 and its Drive checkpoints have no separate license.

PnLCalib is the strongest reviewed calibration candidate but remains a process-isolated benchmark pending GPL-2.0 and weight-term approval plus RTX 3080 measurements. No Bells Just Whistles is its superseded baseline. Broadcast2Pitch and SoccerNet Game State are useful interface/evaluation references but are not runtime dependencies because of licensing, weight-manifest, dependency, and restartability gaps.

## 2026-08-18: Tesseract OCR for the fast MVP

Use Tesseract 5 with the official Apache-2.0 `tessdata_fast` English model behind `OcrEngine`. A manual scoreboard ROI sampled at 1 FPS is smaller, more reproducible, and leaves GPU capacity for detection. OCR failures produce unknown observations; stable score changes are candidates and never confirm goals by themselves. PaddleOCR and RapidOCR remain later benchmarks after dependency and model-license review.

## 2026-08-18: single-host MVP before enterprise deployment

Deliver a restartable atomic-manifest chunk runner and a minimal FastAPI-served multipart/results page before Celery, React, Caddy, and production authentication. This produces an actually usable full-match path quickly while preserving adapter boundaries for the original production plan. Initial heat maps are explicitly screen-space density, not calibrated tactical coordinates.
