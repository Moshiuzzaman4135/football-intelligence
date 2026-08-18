# Implementation Status

## CURRENT MILESTONE

Real-broadcast OCR score-change recovery + debug surface. Fixed the real OCR failure (score changed but no event): the configured `.env` ROI (center-top) read only broadcast ads on the Arsenal/Community-Shield clip; the real scoreboard is a small top-left overlay graphic (≈ x=0,y=0,w=0.30,h=0.16) that stays fixed on screen regardless of camera motion. The tolerant parser/consensus fix (bounded rolling window, bounded consecutive misses, clock/team best-effort, teams learned once and carried) now accepts a real score change; verified on `19b56077-...mp4` (one `score_change_candidate` `0-0 -> 2-0`). CALF action spotting is qualified and integrated behind an `ActionSpotter` interface, and a semantic default timeline hides kick spam. Added a `/full-match/debug` surface that shows raw OCR reads, per-chunk consensus state, peak observations, and detector provenance so the UI can explain why spectators are marked / why no score event fired. Honest boundary: a moving broadcast camera means the static playing-area polygon is only a loose fallback — per-frame homography calibration (PnLCalib / Broadcast2Pitch) and T-DEED/AdaSpot spotter benchmarks are the next GPU-qualification steps (remote RTX 3080 was unreachable this session). Next exact action: qualify the spotter + calibration models on the RTX 3080 and replace the static polygon with a per-frame homography seam.

## LAST VERIFIED STATE

The mandatory M0-M8 vertical slice is implemented and has run both through the CLI and the hardened live Docker API/UI stack. A deterministic 30-second fixture produced detection/tracking overlays, compact track summaries, persisted media/model metadata, one continuous-track evidence-backed kick candidate, and a browser-playable H.264 MP4. A downloaded 17.44-second real football clip completed through both the degraded detector and actual YOLO11n on the local RTX 3050. The latest API upload/start reached completed/100%, events and summaries returned normalized JSON, and the artifact returned HTTP 200. Backend/UI run as UID 1000 on loopback ports 8010/8510. Full-match Tasks 1-2 provide normalized domain/chunk contracts plus durable SQLAlchemy job/stage stores, atomic lease-aware compare-and-set versions, a default three-attempt cap, delivery-bound idempotent completion, monotonic checkpoints, reversible Alembic migrations requiring `FOOTBALL_DATABASE_URL`, and a read-only/repeatable importer for the original SQLite repository. Task 3 adds restart-safe SQL upload sessions, race-safe deterministic job finalization, retryable terminal cleanup, direct-to-object-store resumable multipart upload with 16 MiB parts, a 12 GiB cap, opaque keys, owner/expiry enforcement, ETag and streamed SHA-256 validation, local adapters, and a real S3/MinIO adapter. The hardened MVP runner adds explicit v1-to-v2 manifest migration, measured runtime provenance, durable source/proxy/final identities, crash-recoverable completion, restart-safe source cleanup, cancellable non-exhaustive probes, and strict final decode validation. Compose bootstraps persistent PostgreSQL through Alembic before backend startup. The browser uploader page (`GET /full-match`) computes the file SHA-256 in JavaScript, transfers 16 MiB parts directly to MinIO via presigned URLs (MinIO CORS configured), and renders chunk progress, annotated video, events, scoreboard OCR, and heat map without Streamlit buffering. A synthetic broadcast-style scoreboard source produced 125 clean OCR observations and one evidence-backed `score_change_candidate` (0.938 confidence, 5 s stability window). The protected suite is now 231 passing tests plus five environment-gated integration/Node-required skips.

## CURRENT MILESTONE

Stable after the single-host full-match MVP, browser uploader/results page, event clips/timeline, job history/deletion, and the broadcast quality-recovery layer. The short-clip M0-M8 showcase, durable multipart upload/control plane, restartable full-match runner, and the FastAPI-served `/full-match` browser flow are all implemented and verified. Detection/tracking/overlay/event quality now has a playing-area filter, track confirmation + ceiling, CLEAN/TACTICAL/DEBUG overlay modes with jump-aware short trails, and a contact→release kick state machine with earned, capped confidence. The next milestone is a real-broadcast OCR/detector benchmark with YOLO on GPU (driver unavailable this session) plus a conservative extra-event layer (possession/pass/ball-out) and CALF action spotting.

## COMPLETED MILESTONES

- M0 repository/reference/compute discovery.
- M1 durable harness, runtime skeleton, and Docker baseline.
- M2 video probe and timestamp-preserving frame ingestion.
- M3 replaceable color and Ultralytics detector adapters; YOLO11n CUDA inference tested.
- M4 normalized IoU visual tracking IDs.
- M5 overlays, trails, timestamps, event banners, and H.264 output.
- M6 temporal kick candidates, deduplication, and noisy-or fusion.
- M7 SQLite persistence, semantic in-process bus, background job API, progress, stop, tracks/events/artifacts.
- M8 Streamlit upload, progress, annotated player, metrics, and evidence timeline.
- Full-match Task 1 normalized domain contracts and deterministic chunk planning.
- Full-match Task 2 durable SQLAlchemy job/stage persistence, compare-and-set lifecycle operations, Alembic schema, protocol migration, and read-only legacy SQLite import.
- Full-match Task 3 multipart upload service and API, in-memory/filesystem/S3 adapters, and MinIO Compose runtime.
- Full-match MVP single-host runner with atomic manifest/resume, 120-second/5-second-overlap bounded chunks, manual Tesseract OCR, 32x18 screen-space heat map, non-overlap H.264 finalization/audio mux, and run/status/scoreboard/heat-map APIs.
- Browser multipart uploader and results page (`GET /full-match`): self-contained HTML/JS that computes the file SHA-256 in the browser, transfers 16 MiB parts directly to MinIO via presigned URLs, starts the full-match runner, polls chunk progress, and renders annotated video, event timeline, scoreboard OCR, and heat map without buffering in Streamlit.
- Event clips and live timeline: synchronous 8-second H.264/AAC clip and PNG thumbnail extraction from the annotated video (`GET /jobs/{id}/events/{eid}/clip` and `/thumbnail`), plus auto-refreshing Streamlit progress and click-to-seek/clip event timelines in both the Streamlit and `/full-match` UIs.
- Job history and deletion: `JobStore.delete` on both repositories, `DELETE /jobs/{id}` (removes the job row plus annotated video, raw-track artifact, clips/thumbnails, and full-match workspace; preserves external sources; rejects running jobs), and a Streamlit sidebar to list, load, and delete past jobs.

## CURRENT WORK

DeepSeek session: (1) recovered the clean worktree, (2) diagnosed the real OCR score-change failure on `19b56077-...mp4` (ROI misconfigured, parser too strict, consensus reset on any miss), (3) made the scoreboard parser/consensus tolerant and verified a real `score_change_candidate`, (4) qualified CALF (weights load + forward + 17-class decode) and integrated it behind an `ActionSpotter` interface, (5) added evidence fusion (goal + score change => strong goal) and dedup/NMS, (6) made the default timeline semantic with a debug toggle, (7) rebuilt/restarted the stack on the new code, and (8) added a `/jobs/{id}/full-match/debug` API surface plus a debug panel in the `/full-match` UI showing raw OCR reads, per-chunk consensus state, peak observations, and detector provenance. Verified the debug endpoint returns chunk/peak data for the completed Community-Shield job.

## LAST SUCCESSFUL COMMAND

Full protected suite through the backend image: `pytest -q` => 288 passed, 5 skipped (node/MinIO/Docker-gated), 1 known non-failing Starlette warning. `ruff check .` => All checks passed. `git diff --check` clean. Debug endpoint `GET /jobs/{id}/full-match/debug` returns `{chunks:[...], peak_observations, options}` (verified live: 2 chunks, peak obs 428). Demo smoke test `football-intelligence process data/uploads/synthetic-football-demo.mp4` completed end to end.

## NEXT EXACT ACTION

On the RTX 3080: qualify the spotter (T-DEED or AdaSpot as a private GPL/MIT benchmark) and a per-frame calibration model (PnLCalib GPL-2.0, or Broadcast2Pitch), then replace the static playing-area polygon with a per-frame homography `Calibrator` seam so spectators are rejected under camera motion. Also add the isolated SoccerNet ResNet-TF2 + PCA feature extractor so `CalfActionSpotter.spot` accepts a raw video path. Wire `fuse_semantic_events` into the full-match runner's event finalization. Distributed Celery execution, automatic ROI discovery, and the four-class YOLO on GPU remain deferred.

## FILES MODIFIED

Implemented the video, detector, tracker/summary, overlay, pipeline, settings, API, UI, worker, CLI, and demo-fixture modules; added integration/unit tests, atomic lifecycle/backpressure, media integrity checks, non-root Docker hardening, and refreshed README/harness documentation. Tasks 1-2 added full-match contracts, chunk planning, SQLAlchemy repositories/stage operations, migrations, and legacy import. Task 3 added focused object-store and upload modules, metadata-only multipart API routes, Boto3 S3 support, MinIO configuration, and upload service/API/adapter/integration tests while retaining the legacy short-clip upload path. The MVP runner added `fullmatch/{media,manifest,ocr,heatmap,runner,provenance}.py` plus full-match API seams. The browser page added `fullmatch/web.py`, the `GET /full-match` route, MinIO CORS in Compose, `tools/check_web_js.py`, `tools/live_fullmatch_check.py`, and `tests/fullmatch/test_web_page.py`. The clip/timeline milestone added `clips.py`, per-event clip/thumbnail API routes, the live Streamlit fragment UI, the `/full-match` click-to-seek timeline and clip modal, and `tests/test_clips.py`. The broadcast quality layer added `pitch.py` (playing-area filter), `quality.py` (bundled options), `trails.py` (jump-aware trails), overlay modes, track confirmation, and the contact→release kick state machine.

## MODELS INSTALLED

- Optional ignored local weight: `models/yolo11n.pt`, SHA-256 `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`.
- Optional ignored Python packages: `data/ml-site/`, including Ultralytics 8.4.121 and local CUDA PyTorch runtime dependencies supplied by the existing test image.
- Pinned image asset: `tessdata_fast` English model SHA-256 `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2`, Apache-2.0.

## MODELS TESTED

YOLO11n loaded through this repository's `UltralyticsDetector` and processed all 436 frames of the real clip on CUDA with zero frame errors. It emitted 1,207 `player` and 12 `ball` observations; role-specific goalkeeper/referee labels were not available from COCO weights.

## LOCAL GPU STATUS

RTX 3050 4 GB is visible through host and Docker CUDA runtime. The actual YOLO run completed at 25.309 end-to-end FPS and 54.223 detector FPS. Peak VRAM was not sampled, so no VRAM consumption claim is made. System Python 3.14.6 has no PyTorch; Python 3.12 Docker is the supported application runtime.

## REMOTE GPU STATUS

Connectivity verified read-only. RTX 3080 10 GB was idle except display usage; Docker 29.3.0/Compose 5.1.0 and 62 GiB RAM were available. Root had about 9 GiB free and `/mnt/shared` about 76 GiB. No workspace, weights, package installs, or server changes were made because the local GPU was sufficient for the mandatory slice.

## TESTS RUN

- Latest full suite after all review fixes: 64 passed in 2.09 seconds; one non-failing Starlette `TestClient` deprecation warning.
- Latest Ruff, Compose configuration, and `git diff --check`: clean.
- Review-fix focused suites passed for atomic start/backpressure/model failure, media integrity, event continuity/fusion, metadata, settings, API, storage, and the 30-second fixture.
- Latest deterministic live pipeline: 300 frames, 0 errors, 146.486 FPS, kick candidate confidence 0.850.
- Real clip degraded path: 436 frames, 0 errors, 25.086 FPS; 17,694 noisy observations, demonstrating why this fallback is not a real-match detector.
- Real clip YOLO11n/CUDA: 436 frames, 0 errors, 25.309 end-to-end FPS, 54.223 detector FPS, 1,219 observations.
- Fresh non-root Docker backend/UI healthy at `127.0.0.1:8010`/`127.0.0.1:8510`; ports are loopback-only; upload/start completed, events/summaries returned, artifact endpoint HTTP 200.
- Final rebuilt-image artifact FFprobe: H.264, 640x360, 10 FPS, 300 frames, 30.000 seconds.
- Full-match Task 1 review fixes focused contracts/chunks: 25 passed in 0.13 seconds; complete suite: 89 passed in 2.16 seconds with the known non-failing Starlette `TestClient` deprecation warning; Ruff and `git diff --check` clean.
- Full-match Task 3 recovery-focused upload/persistence suite: 77 passed and one opt-in integration skipped; live MinIO multipart integration: 1 passed; complete suite: 144 passed and one opt-in integration test skipped.
- Full-match Task 3 Fix Round 2 upload/persistence suite: 54 passed and one opt-in integration skipped; complete suite: 148 passed and two environment-gated skips. An isolated empty-volume Compose project initialized PostgreSQL, ran Alembic to head, bootstrapped MinIO, returned backend health, and was removed with its volumes.
- Full-match MVP focused suite: 25 passed. Complete protected suite: 173 passed, two environment-gated skips, and the known Starlette warning. The generated two-chunk H.264+AAC integration and real Tesseract crop run in the normal Docker suite.
- Full-match MVP Fix Round 1 focused suite: 54 passed. Complete protected suite: 202 passed, two environment-gated skips, and the known Starlette warning. The rebuilt image repeated the real two-chunk H.264+AAC, strict probe/decode/faststart, Tesseract ROI, and HTTP byte-range coverage.
- Full-match MVP Fix Round 2 focused suite: 72 passed. Complete protected suite: 220 passed, two environment-gated skips, and the known Starlette warning. The rebuilt image repeated the generated two-chunk H.264+AAC/range integration, corrupted-media strict decode, and measured-provenance tests.
- DeepSeek recovery session: merged worktree into `main`; full suite on merged `main`: 220 passed, 2 skips. After the browser page: full suite 231 passed, 5 skips (3 Node-required JS tests skip inside Docker); `python3 tools/check_web_js.py` passed all Node SHA-256/part-planner checks on the host; live Compose full-match browser flow passed end to end (130 s source, 2/2 chunks, OCR observation, PNG heat map, H.264+AAC faststart artifact); MinIO CORS preflight and signed PUT verified.
- DeepSeek recovery session OCR evidence: a synthetic broadcast-style scoreboard source (`12:00 ARS 0 - 0 CHE` → `13:00 ARS 0 - 1 CHE`) produced 125 accepted OCR observations at 0.93-0.94 raw confidence and exactly one `score_change_candidate` at 65.0 s (0.938 confidence, source `ocr.tesseract.consensus`, monotonic clock 720 s → 780 s, `needs_review: true`). Real-broadcast accuracy remains unverified pending a licensed clip.
- DeepSeek recovery session clip/timeline: full suite 238 passed, 5 skips; live clip endpoint returned an 8.000 s H.264/AAC MP4 and the thumbnail a valid PNG; Streamlit and `/full-match` timelines verified serving seek/clip controls.

## KNOWN FAILURES

- Host system Python cannot import PyTorch; use the documented Docker runtime.
- Port 8000 was already occupied by an unrelated container, so this project defaults to host port 8010.
- Existing ignored artifacts created by the earlier root image required a one-time ownership correction before the new non-root image could reuse the development database. Fresh clones do not have this migration issue; other host owners can export their UID/GID as documented.
- The real wide-angle clip did not cross heuristic event thresholds in either detector run; use the deterministic fixture for the known event walkthrough.
- The remote GPU (`ssh tigerit@192.168.100.68`) was unreachable this session (connection timed out), so CALF was qualified on CPU in the isolated torch/vllm dev image and no broadcast CALF benchmark was run. The authorized remote workspace was not modified.

## KNOWN LIMITATIONS

The core image defaults to a synthetic/degraded color detector. YOLO is optional and AGPL/Enterprise licensing must be evaluated for deployment. Tracking uses IoU plus a bounded ball-center fallback rather than ByteTrack/BoT-SORT. YOLO COCO classes only normalize person and sports ball, not goalkeeper/referee. Frame observations remain stored for short clips even though `/tracks` returns summaries; full-match raw observations are deliberately not persisted. Full-match OCR requires an operator-supplied broadcaster ROI and emits score-change candidates, never confirmed goals. Its heat map is screen-space/not pitch calibrated. The browser uploader hashes the full file in JavaScript, which is slow for multi-GiB sources (WebAssembly or server-side verification is future work), and its direct part PUTs rely on the MinIO CORS setting that Compose enables only for the loopback demo. Team classification, RTSP, automatic ROI discovery, and distributed execution are not implemented. CALF action spotting is qualified and integrated behind `ActionSpotter` but consumes precomputed SoccerNet features; raw-video feature extraction (ResNet-TF2 + PCA) is not installed in the core image. Fusion combines CALF + OCR evidence, but the full-match runner does not yet auto-wire the action spotter. Authentication arrives later, so `X-Owner-ID` is only the current ownership seam and nonlocal exposure remains unsupported. MinIO should have an operator-managed incomplete-multipart lifecycle rule longer than the 24-hour application expiry; completed sources must not use that expiry rule. No lockfile or remote heavy-model benchmark exists yet.

## BLOCKERS

None for the stable short-clip showcase, durable multipart upload foundation, or single-host full-match MVP. Deferred accuracy/distributed features are scope limitations, not external blockers.

## PUBLICATION

Public repository: `https://github.com/Moshiuzzaman4135/football-intelligence`. Initial complete vertical-slice publication commit: `a9410af3056b2365a25746512b3665a40db80b45` on `main`.

---

# DEEPSEEK RECOVERY AUDIT — 2026-08-18

Audit performed by a fresh DeepSeek session after the previous Codex session ended. Every item below was re-inspected and re-run rather than trusted from checkboxes.

## Last Codex commit

`2c0ca5a fix: make full-match completion recoverable` on branch `feat/full-match-production` in the git worktree at `.worktrees/full-match-production`.

## Current git state

- `main` now at `99e713c` — a merge commit that brings the three MVP-runner commits (`9612aed`, `5105e0b`, `2c0ca5a`) from the worktree branch into `main`.
- Working tree clean after the merge; local `.env` created from `.env.example` (gitignored, distinct local dummy values).
- `origin/main` advanced from `2287eb2` to `9cae154` (merge + browser-page commit) during this session.
- Branch `feat/full-match-production` and its worktree remain registered; `.worktrees/` is ignored.

## Implemented and verified (re-run this session)

- Full protected suite on merged `main` through Docker: 220 passed, 2 environment-gated skips, 1 known non-failing Starlette `TestClient` warning (17.92 s).
- Ruff: all checks passed. `git diff --check`: clean.
- Short-clip vertical slice: `demo_fixture` generation plus `football-intelligence process` completed with H.264 640x360/10 FPS/300 frames/30.000 s output and persisted metadata.
- Full-match MVP runner (streaming localization, validated proxy, atomic manifest v2 + v1 migration, 120 s/5 s-overlap chunks, manual-ROI Tesseract OCR consensus, 32x18 screen-space heat map, non-overlap H.264 finalize with audio mux and strict validation, idempotent run/status/scoreboard/heat-map APIs) — covered by 72 focused full-match tests plus the complete suite.
- Docker images: `full-match-production-backend:latest` (Python 3.12, FFmpeg, Tesseract 5.5.0 + pinned `tessdata_fast`) present and used for all verification runs.

## Implemented but not verified

- Local RTX 3050 YOLO11n CUDA run from the earlier session: documented, but `nvidia-smi` cannot currently communicate with the NVIDIA driver on this host, so no fresh GPU verification was possible this session.
- Real-broadcast OCR/detector evidence-quality benchmark (the worktree's documented NEXT EXACT ACTION): not yet executed; no broadcast clip containing a scoreboard is downloaded.

## Partially implemented

- Tesseract OCR: adapter/parser/consensus are implemented and tested on synthetic crops; broadcaster-specific accuracy is unverified. ROI is normalized-environment configuration only; there is no browser ROI control.
- Heat map: fixed 32x18 screen-space density only, explicitly not pitch-calibrated.
- Multipart upload: full API/object-store path is implemented and tested; there is no browser uploader page yet.
- Full-match runner: single-host, one-at-a-time admission; no Celery/distributed execution.

## Planned but not implemented

- Browser multipart uploader + results page (handoff "Next exact implementation" item 7).
- Automatic scoreboard ROI discovery and ROI browser control.
- Team classification; ByteTrack/BoT-SORT; four-role football detector (e.g. gianpaj YOLOv8x on the remote RTX 3080).
- Pitch calibration/radar (PnLCalib/No-Bells-Just-Whistles pending GPL/weight-term approval; Broadcast2Pitch/SoccerNetGSR reference only).
- Action spotting (CALF 17-action ONNX is the qualification target; AdaSpot optional MIT 10-class supplement; T-DEED excluded by GPL).
- Model-based event fusion; Celery/distributed workers; authentication; RTSP ingestion.

## Broken components

- None found in code. Environment notes: host `nvidia-smi` cannot reach the NVIDIA driver (GPU unavailable this session, CPU/Docker paths unaffected); system Python 3.14.6 has no PyTorch — Docker Python 3.12 is the application runtime.

## Downloaded models and checkpoints

- Ignored `models/yolo11n.pt` (SHA-256 `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`).
- Pinned Docker image asset `tessdata_fast` `eng.traineddata` (SHA-256 `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2`, Apache-2.0).
- No external research model repositories are cloned anywhere (no `.research/`, no `/tmp/football-research`).

## Existing Python environments

- System Python 3.14.6 (no PyTorch). No `.venv` exists.
- Docker images: `football-intelligence-backend:latest`, `football-intelligence-ui:latest`, `full-match-production-backend:latest` (Python 3.12).
- Ignored `data/ml-site/` (~382 MB) holds the Ultralytics 8.4.121 + CUDA PyTorch environment used by the earlier YOLO11n run.

## Existing Docker services

- Running containers: unrelated `memora-*` services (ports 8000, 8090, 5432, 6379, 9000-9001) — left untouched.
- No football-intelligence containers currently running; images are built. Compose defines backend/postgres/migrate/minio/minio-init/ui on loopback ports 8010/8510/9010/9011.

## Existing generated videos

- `data/outputs/*.annotated.mp4` (8 H.264 outputs); `data/uploads/synthetic-football-demo.mp4` plus four previously uploaded clips.

## Existing test videos

- Ignored `tests/assets/football-demo.mp4` (17.44 s Pexels training-field clip, CC0, 1920x1080/25 FPS).
- Generated synthetic fixture (30 s, 640x360, 10 FPS).

## Existing checkpoints

- `models/yolo11n.pt` only (see above).

## Existing frontend/UI state

- Streamlit short-clip UI (`src/football_intelligence/ui.py`); its health was verified in earlier sessions, not relaunched this session. No browser multipart/results page exists.

## Current blockers

- Local GPU driver unavailable this session (`nvidia-smi` fails) — does not block the CPU-capable demo.
- No legal broadcast clip with a visible scoreboard is downloaded yet (needed for the OCR quality benchmark).

## FIRST REAL INCOMPLETE MILESTONE

The browser multipart uploader + results page (handoff item 7) — implemented and verified this session: a small FastAPI-served JavaScript page (`GET /full-match`) that creates an upload session, presigns and transfers 16 MiB parts directly, starts the full-match runner, shows progress, and exposes annotated video/heat-map/scoreboard/events without buffering the file or final video in Streamlit. The next milestone is the real-broadcast OCR/detector evidence benchmark with a manual scoreboard ROI.
