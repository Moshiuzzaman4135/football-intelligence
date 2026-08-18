# DeepSeek Handoff

## Read this first

This repository is at a stable checkpoint. Do not claim that 90-minute analysis, scoreboard OCR, heat maps, or reliable goal/free-kick spotting are finished. The short-clip showcase works end to end, and the full-match upload/control-plane foundation is implemented and tested.

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
- Current verification: 148 tests passed, two environment-gated skips, Ruff clean, empty-volume Compose/PostgreSQL/Alembic/MinIO smoke passed.

## What does not work yet

- A job created from an `s3://` full-match upload cannot yet be processed by the legacy local-file pipeline.
- No restartable 120-second processing runner/proxy exists.
- No scoreboard OCR implementation or ROI UI exists.
- No full-match heat map, team classification, pitch calibration, ByteTrack, action model, or core goal/free-kick fusion exists.
- No browser multipart uploader exists; multipart APIs are currently exercised through API/tests.
- No authentication. `X-Owner-ID` is a trusted local seam only. Keep ports loopback-only.

## Next exact implementation

Implement `.superpowers/sdd/2026-08-18-full-match-production/mvp-runner-brief.md` using TDD. In summary:

1. Stream the configured same-bucket S3 object to an atomic local file; ffprobe and generate a validated 720p/25 FPS proxy.
2. Add an atomic manifest and process `plan_chunks()` windows one at a time, resuming completed chunks.
3. Reuse detector/tracker/events/overlay without retaining all full-match observations or storing them in SQL.
4. Add manual-ROI Tesseract OCR at 1 FPS, clock/score consensus, and `score_change_candidate` events.
5. Add an explicitly screen-space 32x18 density heat map.
6. Concatenate non-overlapping annotated chunks, mux audio, validate media, and expose status/OCR/heat-map endpoints.
7. Add a small FastAPI-served JavaScript multipart/results page. Do not buffer the file or final video in Streamlit.

Do not start with Celery, React, auth, PnLCalib, T-DEED, or AdaSpot. Finish and run the single-host MVP first.

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
