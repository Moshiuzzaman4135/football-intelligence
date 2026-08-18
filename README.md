# Football Video Intelligence

A production-minded showcase that turns a short football clip into normalized detections and visual tracks, evidence-backed temporal event candidates, an annotated H.264 MP4, a FastAPI job API, and a Streamlit timeline.

The default image is deliberately credential-free and offline-capable. It uses a deterministic OpenCV color detector plus IoU tracking so the complete workflow is always demonstrable. An optional Ultralytics adapter has also been run successfully with YOLO11n on the local RTX 3050; it is kept outside the core image because of package size and licensing.

## Demo in 5 Minutes

Requirements: Docker, Docker Compose, and ports 8010/8510 available.

```bash
git clone https://github.com/Moshiuzzaman4135/football-intelligence.git
cd football-intelligence
export FOOTBALL_UID="$(id -u)" FOOTBALL_GID="$(id -g)"
docker compose up --build -d
docker compose run --rm --no-deps -v "$PWD:/app" backend \
  python -m football_intelligence.demo_fixture \
  /app/data/uploads/synthetic-football-demo.mp4
```

Open [http://localhost:8510](http://localhost:8510), choose `data/uploads/synthetic-football-demo.mp4`, click **Process video**, and refresh progress until complete. The 30-second known fixture produces a reviewable kick candidate around 1.6 seconds, an evidence panel, tracking IDs, trajectories, and a playable annotated video.

Useful endpoints:

- UI: [http://localhost:8510](http://localhost:8510)
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

## What this prototype does not claim

Tracking IDs are visual continuity IDs, not player identities. The default color fallback is useful for deterministic mechanics and synthetic fixtures, not real-match accuracy. Real footage needs a suitable detector, and all semantic events keep confidence, evidence, source, and `needs_review`. Team assignment, pitch calibration, ByteTrack, four-role football weights, action spotting, and multimodal fusion remain later milestones.

## Documentation

- Product and truthfulness constraints: `docs/PRODUCT_REQUIREMENTS.md`
- Architecture: `docs/ARCHITECTURE.md`
- Execution and recovery: `docs/EXECUTION_PLAN.md`, `docs/IMPLEMENTATION_STATUS.md`
- Verification evidence: `docs/TEST_RESULTS.md`, `docs/MODELS.md`, `docs/REFERENCE_REPOS.md`
- Demo script: `docs/DEMO.md`
