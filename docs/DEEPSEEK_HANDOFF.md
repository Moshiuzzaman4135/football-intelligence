# DeepSeek Handoff

## Read this first

This repository is at a stable checkpoint after the single-host full-match MVP and the browser uploader/results page. Do not claim reliable goal/free-kick spotting, broadcaster-independent OCR, pitch-calibrated heat maps, or distributed production execution. The short-clip showcase, bounded restartable full-match mechanics, and the `/full-match` browser flow all work end to end.

Before editing:

```bash
pwd
git status
git diff
git log --oneline -n 10
```

Then read `AGENTS.md`, `docs/IMPLEMENTATION_STATUS.md`, `docs/DECISIONS.md`, `docs/MODELS.md`, and `docs/superpowers/plans/2026-08-18-full-match-production.md`.

## What works

- Short MP4/MOV/MKV upload through Streamlit and the legacy FastAPI endpoint.
- Background job progress, cancellation, error handling, SQLite metadata, compact track summaries, event timeline, evidence, and browser-playable annotated H.264 MP4.
- Replaceable detector/tracker interfaces. The deterministic color detector is only a demo fallback. YOLO11n was separately run on the RTX 3050.
- Durable PostgreSQL job/stage contracts with Alembic migrations and CAS/lease/retry behavior.
- Direct-to-MinIO multipart upload: 16 MiB parts, 12 GiB maximum, opaque keys, owner seam, signed length/checksum headers, resume, abort, expiry, streamed SHA-256 validation, deterministic job creation, restart/concurrency recovery, and retryable cleanup.
- Docker Compose bootstraps PostgreSQL, runs Alembic, provisions a bucket-scoped MinIO application user, then starts the backend/UI.
- Same-bucket streaming localization, FFprobe validation, 720p/25 proxy, atomic manifest, restartable 120-second chunks with five seconds of context, non-overlap H.264 rendering/audio mux, and media validation.
- Manual normalized-ROI Tesseract OCR at 1 FPS with raw evidence, clock/score consensus, candidate-only score changes, and a fixed screen-space 32x18 heat map.
- Idempotent/resumable full-match run/status/scoreboard/heat-map APIs with one admission slot; raw full-match track observations never enter SQL.
- FastAPI-served browser page (`GET /full-match`): computes the file SHA-256 in JavaScript, transfers 16 MiB parts directly to MinIO via presigned URLs (MinIO CORS configured in Compose), starts the runner, polls chunk progress, and renders annotated video, events, scoreboard OCR, and heat map without buffering in Streamlit.
- Current verification: 231 tests passed, five environment-gated skips (three Node-required JS tests skip inside Docker), Ruff clean, `python3 tools/check_web_js.py` Node checks pass, and a live Compose browser-flow check passed end to end on a generated 130-second two-chunk source.

## What does not work yet

- No automatic scoreboard ROI discovery or ROI browser control exists; configure normalized ROI environment values.
- No team classification, pitch calibration, ByteTrack, action model, or core goal/free-kick fusion exists.
- The browser uploader hashes multi-GiB files slowly in JavaScript (WebAssembly/server-side verification is future work).
- No authentication. `X-Owner-ID` is a trusted local seam only. Keep ports loopback-only.
- The runner is single-host and one-at-a-time, not Celery/distributed. A real 90-minute broadcast runtime/quality benchmark has not been recorded.

## Next exact implementation

Run a representative legal real broadcast through the single-host MVP with its manual scoreboard ROI configured. Record total/chunk runtime, peak memory, OCR precision/failures, event review quality, and final media probe. Use that evidence to choose the next narrow milestone (likely browser multipart/results UX or detector/tracker quality). Do not jump to Celery, React, PnLCalib, T-DEED, or AdaSpot without measured need and a new approved brief.

## Verification commands

Create a local `.env` from `.env.example` and replace every `replace-with-...` value. `.env` is ignored and must never be committed.

```bash
cp .env.example .env
sed -i "s/^FOOTBALL_UID=.*/FOOTBALL_UID=$(id -u)/; s/^FOOTBALL_GID=.*/FOOTBALL_GID=$(id -g)/" .env
docker compose up --build -d
docker compose ps
curl -fsS http://127.0.0.1:8010/health
docker compose run --rm --no-deps \
  -e FOOTBALL_OBJECT_STORE_BACKEND=filesystem \
  -e FOOTBALL_DATABASE_URL= \
  -v "$PWD:/app" backend pytest -q
docker compose run --rm --no-deps \
  -e FOOTBALL_OBJECT_STORE_BACKEND=filesystem \
  -e FOOTBALL_DATABASE_URL= \
  -v "$PWD:/app" backend ruff check .
```

Short demo:

```bash
docker compose run --rm --no-deps \
  -e FOOTBALL_OBJECT_STORE_BACKEND=filesystem \
  -e FOOTBALL_DATABASE_URL= \
  -v "$PWD:/app" backend \
  python -m football_intelligence.demo_fixture /app/data/uploads/synthetic-football-demo.mp4
```

Open `http://127.0.0.1:8510`, upload the generated video, and process it. The known kick candidate appears around 1.6 seconds.

## Safety

- Never modify or push either repository under `tigerit_repos`.
- Never commit `.env`, credentials, videos, weights, databases, or generated media.
- Preserve the short-clip regression path while adding full-match processing.
- Track IDs are visual IDs, OCR score changes are candidates, and all semantic outputs need confidence/evidence/review state.
