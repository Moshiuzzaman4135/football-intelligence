# Full-Match Football Intelligence Production Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver and publish a restartable, authenticated, evidence-backed full-match football analysis service without regressing the verified short-clip showcase.

**Architecture:** FastAPI coordinates typed, idempotent stages persisted in PostgreSQL and queued through Redis/Celery. MinIO holds media and large analytical artifacts, a React client performs resumable multipart upload and operator review, and a Docker GPU worker runs model adapters on the RTX 3080.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2/Alembic, PostgreSQL, Redis, Celery, MinIO/S3, PyArrow/Parquet, OpenCV/FFmpeg, ONNX Runtime, PaddleOCR adapter, React/TypeScript/Vite, pytest, Vitest/Playwright, Ruff, Docker Compose, Caddy.

**Spec:** `docs/superpowers/specs/2026-08-18-full-match-production-design.md`

## Global Constraints

- Never modify or push either TigerIT reference repository.
- Preserve the existing 64-test short-clip pipeline throughout migration.
- Input is an uploaded MP4, MKV, or MOV; do not implement YouTube downloading.
- Accept at most 12 GiB and 150 minutes; infer from a proxy capped at 1080p and 25 FPS.
- Use 16 MiB multipart parts and restartable 120-second chunks with 5-second overlaps.
- Track IDs are anonymous visual IDs; team labels are only `team_1`, `team_2`, or `unknown`.
- Every semantic event retains confidence, evidence, producers, review state, and original model output.
- Store media and raw observations outside PostgreSQL; do not commit media, weights, secrets, databases, or generated artifacts.
- Do not promote a model without source, version, license, checksum, classes, runtime, device, benchmark, and limitations.
- Production claims require the acceptance measurements in the spec.

---

### Task 1: Full-match domain contracts and chunk planning

**Files:** modify `src/football_intelligence/domain.py`; create `src/football_intelligence/fullmatch/chunks.py`, `tests/test_fullmatch_domain.py`, and `tests/test_chunks.py`.

**Interfaces:** produce `StageName`, `StageStatus`, `EventStatus`, `JobStage`, `ScoreboardRegion`, `ScoreboardObservation`, `CalibrationObservation`, `Artifact`, `UploadSession`, `EventReview`, `ModelManifest`, and `plan_chunks(duration_ms, chunk_ms=120_000, overlap_ms=5_000)`.

- [ ] Write tests with literal schemas, invalid transitions/coordinates, a 90-minute chunk plan, final partial chunks, and overlap boundaries; run them and observe missing-contract failures.
- [ ] Implement the smallest validated models and deterministic chunk planner that make focused tests pass.
- [ ] Extend `FootballEvent` compatibly with status, period, match clock, score transition, producer version, and review fields; add round-trip tests first.
- [ ] Run focused tests, the complete Python suite, and Ruff; update harness status and commit `feat: add full-match domain and chunk contracts`.

### Task 2: Durable stage state, SQLAlchemy repository, and migrations

**Files:** create focused modules under `src/football_intelligence/persistence/`, Alembic configuration/migration, and tests under `tests/persistence/`; adapt the existing repository behind a protocol without deleting its SQLite compatibility path.

**Interfaces:** produce `JobStore`, `StageStore`, `claim_stage`, `checkpoint_stage`, `complete_stage`, `fail_stage`, and `retry_stage`; all writes use compare-and-set state/version checks.

- [ ] Test legal/illegal stage transitions, idempotent completion, monotonic checkpoints, lease expiry, retry limits, duplicate suppression, and SQLite-to-new-schema import behavior; observe failures.
- [ ] Implement SQLAlchemy models/repositories supporting SQLite tests and PostgreSQL production URLs, then add Alembic's initial production schema.
- [ ] Make current API/pipeline consume repository protocols while preserving all existing behavior.
- [ ] Run migration upgrade/downgrade tests, concurrency tests, the full suite, and Ruff; update harness and commit `feat: add durable full-match stage persistence`.

### Task 3: Object storage and resumable multipart upload

**Files:** create `src/football_intelligence/object_store.py`, upload service/routes, and focused tests; extend settings and Compose with MinIO.

**Interfaces:** `ObjectStore` protocol; `create_upload`, `presign_part`, `complete_upload`, and `abort_upload`; API paths and values exactly match the spec.

- [ ] Test 16 MiB part validation, 12 GiB quota, allowed extensions, ownership, checksum/ETag completion, resume listing, expiry, abort cleanup, and creation of a job only after successful completion; observe failures.
- [ ] Implement an in-memory/filesystem test adapter and an S3/MinIO adapter with opaque object keys and no client-supplied paths.
- [ ] Add multipart APIs and status responses without retaining file bodies in application memory.
- [ ] Start MinIO in Compose, run a real multipart integration test plus all regressions/Ruff, update harness, and commit `feat: add resumable full-match uploads`.

### Task 4: Idempotent Celery stage orchestration and proxy generation

**Files:** create orchestration/stage modules, Celery app/worker entry points, tests, and CPU/GPU Compose profiles; update video handling and settings.

**Interfaces:** stage task accepts only `job_id`, `stage_name`, and `attempt`; artifacts/checkpoints are retrieved from stores. The production chain follows the exact ordered stages in the spec.

- [ ] Test routing to CPU/GPU queues, idempotent replay, retry backoff, stop propagation, expired-lease recovery, progress aggregation, and absence of duplicate downstream scheduling; observe failures.
- [ ] Implement task orchestration with Redis/Celery and eager-mode tests.
- [ ] Implement ffprobe validation and FFmpeg proxy generation capped at 1080p/25 FPS, producing chunk manifests from `plan_chunks`.
- [ ] Kill/restart a worker during a Compose integration fixture and verify resumption, then run full tests/Ruff, update harness, and commit `feat: orchestrate restartable full-match stages`.

### Task 5: Scoreboard OCR and temporal consensus

**Files:** create `ocr/` protocols, region discovery, parsing, consensus, Paddle adapter, stage integration, tests, and OCR API routes.

**Interfaces:** `OcrEngine.read(frame, region) -> list[OcrToken]`; `ScoreboardParser.parse`; `ScoreboardConsensus.observe`; ROI update invalidates OCR and downstream fusion only.

- [ ] Test clock formats, team/score parsing, confidence propagation, monotonic time, halftime reset, five-second score stability, rejected reversals, missing graphics, and manual ROI rerun; observe failures.
- [ ] Implement pure parsing/consensus and a deterministic fake engine first, then the optional PaddleOCR mobile adapter with lazy model loading.
- [ ] Sample OCR at 2 FPS, persist raw and consensus observations, and emit score-change evidence without directly confirming goals.
- [ ] Run adapter smoke inference on scoreboard fixtures, focused/full tests and Ruff; record runtime/license/checksum, update harness, and commit `feat: add scoreboard score and clock intelligence`.

### Task 6: Football tracking, teams, calibration, and heat maps

**Files:** add tracker adapters, team classifier, calibration/heat-map modules, Parquet storage, stage integration, APIs, and tests.

**Interfaces:** normalized `Tracker` remains stable; `TeamClassifier.classify(crops, history)`; `PitchCalibrator.calibrate(frame)`; `HeatMapAccumulator.observe`; raw chunk observations are Parquet artifacts.

- [ ] Test ByteTrack normalization and resets at shot boundaries, team temporal voting/swap, homography validation/rejection, pitch conversion, heat-map bins, anonymous-track behavior, Parquet round trips, and chunk overlap deduplication; observe failures.
- [ ] Implement ByteTrack as default and keep IoU fallback; benchmark BoT-SORT without selecting it unless it beats ByteTrack's IDF1 and runtime gates.
- [ ] Implement team clustering and calibration with manual correction, never inventing named identities or physical values for rejected calibration.
- [ ] Produce team/track heat-map APIs and artifacts, run real-footage model/tracking/calibration smoke tests plus full tests/Ruff, update harness, and commit `feat: add full-match tracking and tactical heatmaps`.

### Task 7: Action spotting, core event fusion, clips, and overlays

**Files:** create `action/` protocol/adapters/registry, core-event fusion, clip/overlay stages, tests, and event review APIs.

**Interfaces:** `ActionSpotter.spot(video_chunk) -> list[ActionSpot]`; model registry enforces manifest completeness; event statuses are `candidate`, `confirmed`, or `rejected`.

- [ ] Test all core-event mappings, two-source confirmation, stable-score goal rules, free-kick restart rules, overlap deduplication, calibrated confidence, producer independence, operator review audit, and artifact idempotency; observe failures.
- [ ] Add an ONNX action-spotter adapter and a deterministic fake; qualify CALF conversion only after prediction parity and registry/license checks.
- [ ] Fuse OCR, action, tracking, scene, and heuristic evidence; generate seekable event clips and full-match overlays with preserved audio/timing.
- [ ] Run real model smoke/benchmark where licensed weights are available, full tests/Ruff/media probes, update harness, and commit `feat: add multimodal core match events`.

### Task 8: React production UI

**Files:** create `web/` Vite React/TypeScript application, API client, tests, and production container; retain Streamlit only as a deprecated development profile until parity passes.

**Interfaces:** typed clients for auth, uploads, jobs/SSE, events/reviews, OCR ROI, pitch calibration, heat maps, and artifacts.

- [ ] Write Vitest component/behavior tests for resumable upload, retries, stage progress, event filtering/seeking, evidence, review, ROI/pitch correction, and team/track heat maps; observe failures.
- [ ] Implement multipart upload using browser file slices and persisted resume metadata without reading the whole file.
- [ ] Implement dashboard, HTML5 player/timeline seeking, correction tools, event review, heat maps, and accessible error/loading states.
- [ ] Run unit tests, production build, Playwright happy/failure paths against Compose, update harness, and commit `feat: add full-match analyst web application`.

### Task 9: Authentication and production hardening

**Files:** add auth/audit/rate-limit modules, Caddy and operations Compose configuration, backup/restore scripts, metrics, security tests, and documentation.

**Interfaces:** admin/operator/viewer authorization; Argon2 hashes; signed HTTP-only sessions; CSRF on mutations; `/health`, `/ready`, and `/metrics`.

- [ ] Test authentication, role matrices, session expiry/revocation, CSRF, upload/job ownership, rate limits, audit records, secret validation, and unsafe-path/media rejection; observe failures.
- [ ] Implement local accounts and bootstrap-admin workflow without default credentials or committed secrets.
- [ ] Add Caddy TLS, Prometheus metrics, structured redacted logging, container health/readiness, backup/restore, retention dry-run/admin purge, locked dependencies, SBOM, and scans.
- [ ] Perform security/failure/restore drills, run all Python/web tests, builds, scans, and Ruff, update harness, and commit `feat: harden on-prem football intelligence deployment`.

### Task 10: Full-match validation, documentation, and publication

**Files:** update README, AGENTS.md, all persistent docs, benchmark manifests/results, demo and operations runbooks; add final integration tests without committing video or weights.

**Interfaces:** fresh operators can deploy local control plane and remote GPU worker, upload a legal match, review outputs, and recover/restore from documented commands.

- [ ] Run all Python/web unit, integration, browser, migration, security, Compose, lint, and build checks from clean commands.
- [ ] Process a legal 90-minute 1080p broadcast on the RTX 3080; measure every acceptance gate and label unmet model-quality targets honestly as beta blockers.
- [ ] Verify annotated media, OCR, timeline, event clips, heat maps, restart recovery, backups, CPU/local-GPU fallback, and no credential/artifact leakage.
- [ ] Request final independent review, address Critical/Important findings, reconcile the full spec, update harness `NEXT EXACT ACTION`, and commit `test: validate production full-match workflow`.
- [ ] Merge the reviewed branch into `main`, push only this repository, and verify GitHub commit/clean status.
