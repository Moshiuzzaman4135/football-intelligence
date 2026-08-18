# Football Intelligence Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, validate, document, and publish a CPU-capable upload-to-annotated-timeline football intelligence demo.

**Architecture:** A FastAPI/Streamlit modular monolith runs typed video, detection, tracking, event, overlay, and persistence components behind replaceable protocols. SQLite holds metadata, the filesystem holds media, and an in-process event bus preserves future worker boundaries.

**Tech Stack:** Python 3.12, Pydantic 2, FastAPI, SQLAlchemy/SQLite, OpenCV, FFmpeg, NumPy, optional Ultralytics/PyTorch, Streamlit, pytest, Ruff, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-18-football-intelligence-vertical-slice-design.md`

## Global Constraints

- Never modify or push `/home/moshiuzzamanshatil/projects/tigerit_repos/govms` or `vision-relay`.
- The mandatory pipeline works without the remote GPU and without network access after setup.
- Track IDs are visual IDs, not identities.
- Every semantic event retains confidence, evidence, source, and `needs_review`.
- Media stays on filesystem; no SQL video blobs.
- Production behavior follows test-first red/green cycles.

---

### Task 1: Harness and reproducible runtime

**Files:** create `pyproject.toml`, `.env.example`, `Dockerfile`, `docker-compose.yml`, `README.md`; maintain all required `docs/*.md`.

**Interfaces:** produces Python package `football_intelligence`, settings derived from `FOOTBALL_*`, and `pytest`/`ruff` commands used by every task.

- [x] Add package metadata with a Python `>=3.11,<3.13` constraint, core and optional `ml`/`dev` dependencies.
- [x] Build the CPU image and run `python -c 'import football_intelligence'` to prove the runtime imports.
- [x] Record exact local/remote compute observations and dependency decisions.
- [x] Commit `chore: initialize football intelligence harness` after verification.

### Task 2: Domain schemas and timebase (TDD)

**Files:** create `tests/test_domain.py`, `tests/test_timebase.py`, `src/football_intelligence/domain.py`, `src/football_intelligence/timebase.py`.

**Interfaces:** `frame_to_ms(frame_index: int, fps: float) -> int`; `Detection`, `TrackObservation`, `EventEvidence`, `FootballEvent`, `JobRecord`, and `VideoMetadata` Pydantic models.

- [x] Write literal expectations for 25 FPS conversion and invalid FPS; run tests and observe missing-module failures.
- [x] Write schema tests that reject invalid boxes/confidence/timestamp ordering and accept the normalized examples; observe failure.
- [x] Implement minimal validated models/conversion and rerun focused then full tests.
- [x] Commit `feat: add normalized football intelligence domain`.

### Task 3: Job storage and state machine (TDD)

**Files:** create `tests/test_storage.py`, `src/football_intelligence/storage.py`, `src/football_intelligence/settings.py`.

**Interfaces:** `JobRepository.create/get/list/transition/update_progress/save_events/save_tracks`; allowed states `created -> running -> completed|failed|stopped` and `running -> stopping -> stopped`.

- [x] Test creation defaults, legal transitions, illegal terminal transitions, progress monotonicity, and event/track round trips against temporary SQLite.
- [x] Observe expected failures, implement SQLite tables/repository, and rerun focused/full tests.
- [ ] Commit `feat: persist jobs events and track summaries` (included in final vertical-slice commit).

### Task 4: Semantic bus and event engine (TDD)

**Files:** create `tests/test_bus.py`, `tests/test_events.py`, `src/football_intelligence/bus.py`, `src/football_intelligence/events.py`.

**Interfaces:** `EventBus.publish(topic, payload) -> EventEnvelope`; `deduplicate_events(events, window_ms)`; `fuse_events(events, window_ms)`; `TemporalEventEngine.observe(tracks)` and `.finalize()`.

- [x] Test subscriber isolation and semantic envelope timestamps.
- [x] Test continuous-track temporal kick heuristics with hand-authored observations, deduplication, and independent-source evidence fusion. Pass/shot/ball-out remain later milestones.
- [x] Observe failures, implement the minimum honest temporal rule, and rerun focused/full tests.
- [ ] Commit `feat: add temporal event candidates and fusion` (included in final vertical-slice commit).

### Task 5: Video, detector, and tracker adapters (TDD)

**Files:** create `tests/test_video.py`, `tests/test_detection.py`, `tests/test_tracking.py`, `src/football_intelligence/video.py`, `src/football_intelligence/detection/{base,color,ultralytics}.py`, `src/football_intelligence/tracking/{base,iou}.py`.

**Interfaces:** `probe_video(path) -> VideoMetadata`; `iter_frames(path)`; `Detector.detect(frame, frame_index, timestamp_ms)`; `Tracker.update(detections, frame_index, timestamp_ms)`.

- [x] Generate a small moving-object MP4 fixture during tests and first prove probe/frame/timestamp expectations fail.
- [x] Test normalized color detector output and stable/expired IoU tracks with literal boxes.
- [x] Implement OpenCV source handling, deterministic fallback, IoU assignment, and optional lazy Ultralytics adapter; rerun tests.
- [ ] Commit `feat: add video detection and tracking adapters` (included in final vertical-slice commit).

### Task 6: Overlay and end-to-end pipeline (TDD)

**Files:** create `tests/test_overlay.py`, `tests/test_pipeline.py`, `src/football_intelligence/overlay.py`, `src/football_intelligence/pipeline.py`, `src/football_intelligence/cli.py`.

**Interfaces:** `draw_overlay(frame, tracks, events, timestamp_ms, trails) -> ndarray`; `Pipeline.run(job_id) -> JobRecord`; CLI `football-intelligence process VIDEO`.

- [x] Test pixel-visible overlays and a full deterministic clip run producing completed metadata, tracks/events, and output.
- [x] Observe failure, implement orchestration, progress/stop/fault handling, temporary writer, FFmpeg H.264 finalization, metrics, and semantic bus publications.
- [x] Rerun integration test and validate output for H.264 codec, dimensions, FPS, duration, frame count, and readability.
- [ ] Commit `feat: generate annotated football video` (included in final vertical-slice commit).

### Task 7: FastAPI lifecycle and media API (TDD)

**Files:** create `tests/test_api.py`, `src/football_intelligence/api.py`.

**Interfaces:** `POST /jobs/upload`, `POST /jobs/{id}/start`, `POST /jobs/{id}/stop`, `GET /jobs`, `/jobs/{id}`, `/events`, `/tracks`, `/annotated-video`, `/status`, and `GET /health`.

- [x] Test health, upload/job creation, invalid extension/path protection, atomic start/status, stop, event/track JSON, missing IDs, model-init failure, admission backpressure, and media response against a temporary app.
- [x] Observe failures, implement dependency-injected app state and bounded background executor, then rerun focused/full tests.
- [ ] Commit `feat: expose football processing API` (included in final vertical-slice commit).

### Task 8: Streamlit showcase

**Files:** create `src/football_intelligence/ui.py`, `tests/test_ui_helpers.py`.

**Interfaces:** UI API client functions, confidence labels, evidence formatting, upload/start/poll/video/timeline panels.

- [x] Test pure confidence/evidence formatting; observe missing-helper failure.
- [x] Implement the minimal UI without duplicating backend domain logic; manual refresh is documented.
- [x] Start Streamlit headlessly, fetch its health endpoint, and record result.
- [ ] Commit `feat: add event timeline demo` (included in final vertical-slice commit).

### Task 9: Real fixture and selected ML path

**Files:** update `tests/assets/README.md`, `docs/MODELS.md`, `docs/TEST_RESULTS.md`, `docs/DEMO.md`; do not commit downloaded video/weights.

**Interfaces:** documented download/checksum command and `FOOTBALL_DETECTOR=ultralytics` model selection.

- [x] Provide a generated 30-second primary fixture and document a separate legal 17.44-second Pexels real-footage evaluation clip with source/license/checksum.
- [x] Verify Docker CUDA and run YOLO11n on the local RTX 3050.
- [x] Process the real clip, inspect representative frames, and measure detector/overlay/total FPS. Peak VRAM was not sampled and is explicitly not claimed.
- [x] Defer the larger remote model because local YOLO was sufficient for the vertical slice; record the decision.
- [ ] Commit `test: validate real football video pipeline` (included in final vertical-slice commit).

### Task 10: Final packaging and publication

**Files:** update `README.md`, `docs/IMPLEMENTATION_STATUS.md`, `docs/TEST_RESULTS.md`, `docs/DECISIONS.md`, `docs/DEMO.md`, `docs/REFERENCE_REPOS.md`.

**Interfaces:** fresh engineer can follow `Demo in 5 Minutes` without reading source.

- [x] Run full tests, Ruff, Docker build, API/UI health, deterministic CLI/live demo, real-video model run, and ffprobe checks from fresh commands.
- [x] Reconcile all 20 acceptance criteria and record gaps as limitations rather than silently weakening them.
- [x] Inspect `git diff --check`, `git status`, and recent commits; perform final independent code review.
- [ ] Create the GitHub repository if absent, push only this repository's `main` branch, verify remote/commit, and record URL.
