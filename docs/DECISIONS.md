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

## 2026-08-18: single-file FastAPI-served browser uploader page

The full-match browser UX is one self-contained HTML/JS document served at `GET /full-match` from the same origin as the API (no CORS for API calls, no build step, no React). The client computes the required full-file SHA-256 incrementally in JavaScript (verified against `node:crypto` by `tools/check_web_js.py`), transfers each 16 MiB part directly to a presigned MinIO URL with the signed length/checksum headers, then starts the runner and polls the manifest. MinIO needs `MINIO_API_CORS_ALLOW_ORIGIN=*` (enabled in Compose, loopback-only) so the browser preflight for `content-length,x-amz-checksum-sha256` succeeds; the backend still never proxies part bodies. JS hashing is acknowledged as slow for multi-GiB files and is a documented limitation rather than silently claiming scale.

## 2026-08-18: live browser-flow verification is a repeatable tool

`tools/live_fullmatch_check.py` performs the exact API calls the page makes against a running Compose stack, and `tools/check_web_js.py` verifies the embedded JavaScript on any host with Node. These keep the browser flow verifiable without a Selenium/browser dependency.

## 2026-08-18: synchronous event clips before Celery

Event clips and thumbnails are cut synchronously from the annotated video with FFmpeg and cached under `data/clips`, served by `GET /jobs/{id}/events/{eid}/clip` and `/thumbnail`. This mirrors the `vision-relay` `POST /events/clip` idea without adding Celery/Redis, and keeps a clip request self-contained and idempotent (cached, faststart H.264/AAC). Clip duration defaults to 8 s with 2 s of pre-roll; a slow source or a very long clip can be tuned later. Clips never enter SQL or the object store — they are derived, disposable artifacts of the annotated output.

## 2026-08-18: live UI via fragment polling, not websockets

Both UIs auto-refresh by polling the existing status/events endpoints: Streamlit uses `@st.fragment(run_every=...)` and stops refreshing once the job settles, and the browser page polls on a fixed interval. This avoids adding websocket/SSE infrastructure while remaining one simple change away if a later task needs push updates.

## 2026-08-18: three presentation layers (raw -> analytics -> presentation)

Real-broadcast output previously exposed raw detector/tracker internals directly, producing a spectator-box wall, thousands of IDs, trajectory spaghetti, and kick spam. The system now separates raw AI output, analytics/evidence, and user presentation:

- `CLEAN` (default): confirmed tracks, compact IDs (`P7`, `GK1`, `B`), ball marker, timestamp, transient event banner; no trails, no team/confidence spam.
- `TACTICAL`: adds short (0.5-1.5 s) footpoint/center trails with age, point, and jump limits.
- `DEBUG`: raw boxes, rejected boxes + reason, confidence, track state, and the playing-area polygon.

Raw detector output is retained in the `*.tracks.json` artifact and metrics, never auto-promoted to presentation.

## 2026-08-18: playing-area filter before tracking

A normalized `PlayingAreaPolygon` (manual config; no PnLCalib yet) filters person detections by bottom-center footpoint before the tracker. The ball uses an expanded margin because it may be airborne or cross the touchline. This rejects spectators/bench/camera-operator boxes without discarding raw output (kept in metrics/DEBUG).

## 2026-08-18: track confirmation and presentation ceiling

IoU tracks now carry `tentative`/`confirmed` presentation state (`confirm_min_hits`), so a one-frame detection never creates a visible ID. The overlay applies a configurable active-track ceiling (default 30) preferring established continuity + confidence. This is a presentation/quality safety limit, not a semantic assertion that a match has at most N participants.

## 2026-08-18: kick as a contact->release state machine

The kick detector is no longer a bare speed threshold. A `kick_candidate` requires a valid same-track ball across frames, a minimum number of near-player contact frames, separation (distance increasing), inter-frame speed above threshold, and a cooldown. Heuristic-only confidence is capped (default 0.70) and earned from continuity/contact/speed rather than inflating toward 95%. Extreme scene/camera transitions (single-frame ball jump) suppress emission.

## 2026-08-18: tolerant scoreboard consensus and best-effort parser

The real broadcast failure (score changed, no event) traced to three strict behaviours: the configured ROI can read ads instead of a real scoreboard; the parser rejected any read lacking a clock AND both team tokens; and a single missed read reset the developing change. The consensus now uses a bounded rolling window: up to `max_consecutive_misses` missed reads are tolerated before a developing change is abandoned, teams are learned once and carried (an `unknown` placeholder is refined when a real token appears), and the clock is best-effort (falls back to the last known clock or the video timestamp). A stable valid increase emits exactly one `score_change_candidate` with `needs_review=true`; score regression is rejected. OCR alone never confirms a goal.

## 2026-08-18: ActionSpotter as the replaceable action-model seam

CALF is the primary broad action-spotting model, integrated behind the `ActionSpotter` protocol so the pipeline never depends on a single producer. A normalized `ActionSpot` carries event type, ms range, confidence, raw label/score, and producer provenance; `normalize_action` maps it to the existing `FootballEvent` with `needs_review=true`. `torch` and the CALF model port are lazily imported/isolated from the core package; raw-video feature extraction remains a separate optional deployment step. Fusion is evidence-based: CALF `goal_candidate` + stable OCR score change => a single strong `goal_candidate` (needs_review=false), each alone stays a reviewable candidate. Low-level kick spam stays in debug data and is hidden from the default semantic timeline.

## 2026-08-18: semantic default timeline with a debug escape hatch

The default event timeline (Streamlit and `/full-match`) shows only semantic event types (goal, shot, foul, corner, cards, offside, substitution, throw-in, restarts, score change). Low-level `kick_candidate` spam and other debug evidence are still computed, stored, and retrievable, but hidden by default; the browser page adds a "show low-level debug events" toggle. This keeps the UI meaningful without discarding the raw evidence.
