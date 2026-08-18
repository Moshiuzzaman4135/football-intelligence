# Test Results

## Environment baseline — 2026-08-18

| Check | Result |
|---|---|
| Local GPU | RTX 3050 4 GB visible; 11 MiB used at inspection |
| Local FFmpeg | 8.1.2, H.264/NVENC capabilities present |
| Local Docker | 29.6.2; Compose 5.3.1; CUDA base container exposed RTX 3050 with `--gpus all` |
| Local Python | 3.14.6; PyTorch not installed |
| Remote connectivity | SSH succeeded |
| Remote GPU | RTX 3080 10 GB, 64 MiB used at inspection |
| Remote Docker | 29.3.0; Compose 5.1.0 |
| Remote storage | approximately 9 GiB root and 76 GiB `/mnt/shared` free |

## Harness verification — 2026-08-18

| Command | Result |
|---|---|
| `docker compose build` | backend and UI images built successfully on Python 3.12 slim |
| container package/OpenCV import | `football_intelligence 0.1.0`, OpenCV `4.14.0` |
| container FFmpeg version | `7.1.5-0+deb13u1` |
| `docker compose run --rm --no-deps backend ruff check .` | all checks passed |

The core runtime was later rebuilt after adding the application modules. The final verification section below supersedes this early harness-only statement.

## Domain, persistence, and events — 2026-08-18

- Red evidence: domain/timebase imports failed before implementation; storage/bus/events imports failed before implementation.
- Domain/timebase: 20 tests passed.
- Storage/bus/events: 10 tests passed after fixing a reproduced class-scope annotation shadowing issue.
- Full suite: 30 tests passed in 0.20 seconds.
- Covered: timestamp conversion, schema validation, track normalization defaults, event time validation, lifecycle transitions, progress monotonicity, persistence round trips, subscriber isolation, temporal kick evidence, deduplication, and noisy-or fusion.

## Video, API, and UI vertical slice — 2026-08-18

- Full pre-documentation suite: 49 passed in 0.92 seconds. The only warning is a non-failing Starlette `TestClient` deprecation warning.
- Ruff: all checks passed.
- Coverage includes video probing/iteration, color and Ultralytics adapters, IoU tracking continuity, overlay rendering, real encoded-video pipeline integration, API health/upload/start/stop/artifact behavior, and UI formatting helpers.
- Live Docker Compose services: backend healthy at `http://localhost:8010/health`; Streamlit healthy at `http://localhost:8510/_stcore/health`.
- Live API run on the real fixture showed monotonic sampled progress from 0 through 99 and then `completed`/100; annotated artifact returned HTTP 200.

## Media and performance measurements — 2026-08-18

| Run | Frames | Result | Detector FPS | End-to-end FPS | Tracking | Overlay |
|---|---:|---|---:|---:|---:|---:|
| Synthetic 640x360/10 FPS, color + IoU/center fallback | 300 | 0 errors; 1 kick candidate | 1212.176 | 146.486 | 0.011 s | 0.058 s |
| Pexels 1920x1080/25 FPS, color + IoU | 436 | 0 errors; excessive false positives | 85.873 | 25.086 | 1.621 s | 1.211 s |
| Pexels 1920x1080/25 FPS, YOLO11n CUDA + IoU | 436 | 0 errors; 1,207 player + 12 ball observations | 54.223 | 25.309 | 0.0198 s | 0.3442 s |

The latest deterministic output was validated by the pipeline as H.264, 640x360, 10 FPS, 300 frames, and 30.000 seconds. The real outputs were H.264, 1920x1080, 25 FPS, and 17.44 seconds. Measurements are single short-clip development runs, not statistically rigorous benchmarks. GPU model was the local RTX 3050 4 GB; peak VRAM was not sampled.

## Known-event fixture

Live API job `5de00400-4dda-4333-b29f-219d4c6cdadc` emitted `kick_candidate` from 1600-1700 ms at confidence 0.850. Evidence is `ball_speed_px_s=300.04` plus `player_proximity_px=58.96`, source `heuristic.temporal`, frame references 16 and 17, track references 3 and 4, and `needs_review=true`.

The real clip produced no heuristic events. This is expected for a sparse training-field shot and is recorded rather than manufacturing an event.

## Independent review hardening — 2026-08-18

An independent read-only review found no critical issues and eight important issues. Regression-driven fixes now cover atomic start/transition semantics, duplicate-start rejection, bounded admission, background model-init failure persistence, premature decode EOF, recoverable-frame timing preservation, final artifact validation, continuous ball IDs, same-source fusion, source/output/model metadata, track summaries, environment settings, a 30-second fixture, loopback ports, and non-root containers.

Fresh image checks:

- `docker compose build backend ui` succeeded.
- `docker compose exec -T backend id` returned `uid=1000(app) gid=1000(app)`.
- `ss` showed 8010/8510 bound only to `127.0.0.1`.
- Backend and Streamlit health endpoints returned `ok`.
- Live 30-second upload/start completed in 2.048 seconds, returned four track summaries and one candidate, and served the artifact with HTTP 200.
- Full post-fix suite: 64 passed in 2.09 seconds with one known non-failing Starlette `TestClient` dependency warning.
- Post-fix Ruff, Compose configuration, and `git diff --check`: clean.
- Independent FFprobe of the live artifact: H.264, 640x360, 10/1 FPS, 300 frames, 30.000 seconds.
- Barrier-based tests prove that a stop before worker entry and a stop at finalization both end as `stopped`, never `failed`.
- Final rebuilt-image live job `eef77882-63ab-40a1-bed7-f657ca98461b`: completed/100%, 300 frames, zero errors, 2.0998 seconds, one event, four summaries, artifact HTTP 200.
- Final targeted independent review: no remaining Critical or Important issues; `Ready to merge: Yes`.

## Full-match multipart upload — 2026-08-18

- RED evidence: the first service suite failed collection because `football_intelligence.object_store` did not exist; API tests then failed because `create_app` had no upload-service seam; adapter tests failed because `S3ObjectStore` did not exist; settings tests failed because S3 fields were absent.
- Focused unit/API/settings coverage: 12 passed. It covers the 16 MiB constant and final-part sizing, 12 GiB cap, MP4/MKV/MOV allowlist, opaque keys, ownership, resume listing, expiry, abort cleanup, ETag mismatch, streamed full-object checksum/size mismatch, retry after a transient job-store failure, and job creation only after validation.
- Real MinIO integration: 1 passed using a 16 MiB first part plus tail, presigned PUTs, resumed S3 part listing, ETag completion, streamed SHA-256 validation, object existence, job creation, and cleanup.
- The rebuilt backend and pinned MinIO were healthy in Compose. Because the existing baseline container already owned loopback port 8010, the isolated worktree backend was verified on 18010; live create/presign/abort returned a host-visible MinIO URL and cleaned up successfully.
- Complete protected suite: 122 passed, one opt-in MinIO test skipped by default, and the known non-failing Starlette `TestClient` deprecation warning.

## Full-match multipart recovery fix — 2026-08-18

- Durable SQLAlchemy upload-session, restart, two-instance CAS, ambiguous S3 completion, deterministic job commit replay, per-upload concurrency, expiry cleanup/retry, atomic filesystem, signed length/checksum, S3 error, API lifecycle, settings, and migration coverage passed.
- Real MinIO integration passed with distinct application credentials, rejected an oversized signed PUT, resumed parts, completed and validated the source, and finally aborted/deleted storage safely.
- Complete protected suite: 144 passed, one opt-in MinIO test skipped by default, and the known non-failing Starlette `TestClient` deprecation warning.

## Full-match multipart recovery fix round 2 — 2026-08-18

- Barrier coverage proves expiry cleanup cannot delete an object after job finalization begins; concurrent completion still converges on one job.
- Abort and failed-validation storage outages remain durable cleanup-pending terminal states and retry before the original session expiry. The lifecycle loop survives a repository outage and succeeds on its next tick.
- Focused upload/persistence suite: 54 passed and one opt-in MinIO skip. Full protected suite: 148 passed, two environment-gated skips, and the known Starlette warning.
- A fresh isolated Compose project created empty PostgreSQL/MinIO volumes, reached healthy PostgreSQL, ran Alembic to head before backend startup, returned `{"status":"ok"}`, and contained `jobs`, `upload_sessions`, and `alembic_version`; the project and volumes were then removed.

## Restartable full-match MVP runner — 2026-08-18

- RED evidence captured missing media/OCR/heat-map/manifest/runner modules and missing full-match API seams. The real media test then reproduced final concat drift (`0.9837` versus `1.0` FPS); final PTS normalization fixed it. A rebuilt-image OCR test exposed a missing TSV config when using the isolated `tessdata_fast` directory; the adapter now enables TSV output explicitly.
- Focused Docker suite covers exact-bucket URI rejection, streamed atomic localization/failure cleanup, 150-minute/container limits, 720p/25 H.264 proxy/audio, OCR formats/monotonic and halftime consensus/raw evidence, candidate-only score changes, real Tesseract crop execution, fixed 32x18 heat-map bounds/PNG, atomic manifest/immutable options, overlap output ranges, crash/restart/no-reprocessing, stop, deterministic namespaced IDs, bounded observations, absent raw SQL tracks, API idempotence/restart/single admission, range media, and artifact reads.
- A generated 121-second 160x90/1 FPS H.264+AAC source ran through Docker as two planned chunks. The validated chunks were H.264/no-audio with durations exactly 120 and 1 seconds; the final was H.264, 160x90, 1 FPS, approximately 121 seconds, and retained one audio stream.
- The rebuilt image contains Tesseract 5.5.0 and the pinned `tessdata_fast` English SHA-256 `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2`; its real crop test passed.
- Focused full-match suite: 25 passed. Complete protected suite: 173 passed, two environment-gated skips, and the pre-existing Starlette `TestClient` warning.

## Restartable full-match MVP runner fix round 1 — 2026-08-18

- Lifecycle regression tests cover real object-store stream, FFprobe, and proxy failures; corrupt/mismatched manifests; process death during preparation; and deterministic stop barriers during localization, proxy preparation, and final publication.
- The manifest now locks detector/model/device/framework/version/config, tracker/config, OCR engine/model/version/checksum, frame-error policy, proxy/output encoding, chunk/OCR/overlay/trail/heat-map settings, and source/proxy/final/heat-map hashes plus probe identity. A changed runtime is rejected on resume.
- Restart tests cover corrupt proxy rebuilding, localized-source identity checking, durable source removal, successor invalidation after a corrupt chunk, completed-artifact verification, and persisted OCR consensus/raw support evidence.
- Real media coverage includes N/A and `0/0` fallback, VFR average-rate identity, MKV/MOV probes, strict MP4/H.264/yuv420p/AAC timing, faststart ordering, full decode, two non-overlap chunks, and a real HTTP byte-range response.
- Focused Docker suite: 54 passed. Complete protected suite: 202 passed, two environment-gated skips, and the pre-existing Starlette `TestClient` warning.
- Repository-wide Ruff, Compose configuration with ephemeral values, rebuilt backend image, and `git diff --check` passed.
