# Football Video Intelligence

A production-minded showcase that turns a football video into normalized detections and visual tracks, evidence-backed temporal event candidates, an annotated H.264 MP4, a FastAPI job API, and a Streamlit timeline. The short-clip path remains available; completed MinIO uploads can also run through a restartable, bounded full-match MVP.

The default image is deliberately credential-free and offline-capable. It uses a deterministic OpenCV color detector plus IoU tracking so the complete workflow is always demonstrable. An optional Ultralytics adapter has also been run successfully with YOLO11n on the local RTX 3050; it is kept outside the core image because of package size and licensing.

## Demo in 5 Minutes

Requirements: Docker, Docker Compose, and ports 8010/8510/9010/9011 available.

```bash
git clone https://github.com/Moshiuzzaman4135/football-intelligence.git
cd football-intelligence
cp .env.example .env
# Replace every replace-with-... value in .env; never commit this file.
export FOOTBALL_UID="$(id -u)" FOOTBALL_GID="$(id -g)"
docker compose up --build -d
docker compose run --rm --no-deps \
  -e FOOTBALL_OBJECT_STORE_BACKEND=filesystem -e FOOTBALL_DATABASE_URL= \
  -v "$PWD:/app" backend \
  python -m football_intelligence.demo_fixture \
  /app/data/uploads/synthetic-football-demo.mp4
```

Open [http://localhost:8510](http://localhost:8510), choose `data/uploads/synthetic-football-demo.mp4`, click **Process video**, and refresh progress until complete. The 30-second known fixture produces a reviewable kick candidate around 1.6 seconds, an evidence panel, tracking IDs, trajectories, and a playable annotated video.

For a full match, open the FastAPI-served browser page — [http://localhost:8010/full-match](http://localhost:8010/full-match) — select a match video (MP4/MKV/MOV, up to 12 GiB), and click **Upload and process**. The page computes the required SHA-256 in the browser, transfers 16 MiB parts directly to MinIO through presigned URLs, then starts the restartable full-match runner and shows chunk progress, the annotated video, the event timeline, scoreboard OCR observations, and the screen-space heat map.

Useful endpoints:

- UI: [http://localhost:8510](http://localhost:8510)
- Full-match browser page: [http://localhost:8010/full-match](http://localhost:8010/full-match)
- API docs: [http://localhost:8010/docs](http://localhost:8010/docs)
- Health: [http://localhost:8010/health](http://localhost:8010/health)

Stop the demo with:

```bash
docker compose down
```

## Test and CLI

```bash
docker compose run --rm --no-deps -v "$PWD:/app" backend pytest -q
docker compose run --rm --no-deps -v "$PWD:/app" backend ruff check .
docker compose run --rm --no-deps -v "$PWD:/app" backend \
  football-intelligence process \
  /app/data/uploads/synthetic-football-demo.mp4 --data-root /app/data
```

Generated uploads, SQLite metadata, outputs, weights, and downloaded fixtures are ignored by Git. See `docs/DEMO.md` for the narrated walkthrough and `docs/MODELS.md` for the optional GPU/YOLO command.

For a completed multipart-upload job, start or resume the single-host full-match runner with `POST /jobs/{id}/full-match/run`. Inspect `GET /jobs/{id}/full-match/status`, `/scoreboard`, `/heat-map`, `/events`, and `/annotated-video`. Per event, `GET /jobs/{id}/events/{event_id}/clip` returns a short 8-second annotated clip and `GET /jobs/{id}/events/{event_id}/thumbnail` returns a frame. The status manifest checkpoints 120-second chunks with five seconds of context; a repeated run request resumes an orphaned running job after process restart and only one full match is admitted at a time. The browser page at `/full-match` drives this entire flow without buffering the file or final video in Streamlit, and both the Streamlit showcase and the `/full-match` page show a live, click-to-seek event timeline with per-event clips.

## Current capability boundary

The short-video UI flow is usable end to end. The backend also supports durable resumable full-match upload to MinIO, creates a job only after validation, and processes that `s3://` source through safe localization, a 720p/25 proxy, restartable chunks, manual-ROI Tesseract OCR, a screen-space heat map, and a validated annotated video. A small FastAPI-served browser page (`/full-match`) drives upload/process/results. OCR score changes are reviewable candidates only; robust semantic goal/free-kick spotting and production distributed execution remain later milestones.

## What this prototype does not claim

Tracking IDs are visual continuity IDs, not player identities. The default color fallback is useful for deterministic mechanics and synthetic fixtures, not real-match accuracy. Real footage needs a suitable detector, and all semantic events keep confidence, evidence, source, and `needs_review`. Team assignment, pitch calibration, ByteTrack, four-role football weights, action spotting, and multimodal fusion remain later milestones.

## Documentation

- Product and truthfulness constraints: `docs/PRODUCT_REQUIREMENTS.md`
- Architecture: `docs/ARCHITECTURE.md`
- Execution and recovery: `docs/EXECUTION_PLAN.md`, `docs/IMPLEMENTATION_STATUS.md`
- Verification evidence: `docs/TEST_RESULTS.md`, `docs/MODELS.md`, `docs/REFERENCE_REPOS.md`
- Demo script: `docs/DEMO.md`
