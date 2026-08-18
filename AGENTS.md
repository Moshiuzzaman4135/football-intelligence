# Football Intelligence Operating Manual

## Mission and boundaries

Build and preserve a demonstrable football-video intelligence pipeline: video input -> detection -> tracking -> temporal event candidates -> annotated MP4 -> FastAPI -> Streamlit timeline.

- Project root: `/home/moshiuzzamanshatil/projects/personal_git/football-intelligence`
- Read-only references: `/home/moshiuzzamanshatil/projects/tigerit_repos/vision-relay` and `/home/moshiuzzamanshatil/projects/tigerit_repos/govms`
- Never modify, commit, or push from either `tigerit_repos` repository.
- Keep the mandatory demo usable without the remote GPU.
- Do not commit videos, weights, credentials, generated artifacts, databases, or `.env` files.

## Recovery protocol

Before modifying code, run:

```bash
pwd
git status
git diff
git log --oneline -n 10
```

Then read, in order:

```text
AGENTS.md
docs/PRODUCT_REQUIREMENTS.md
docs/ARCHITECTURE.md
docs/EXECUTION_PLAN.md
docs/IMPLEMENTATION_STATUS.md
docs/DECISIONS.md
docs/TEST_RESULTS.md
```

Resume from `NEXT EXACT ACTION` in `docs/IMPLEMENTATION_STATUS.md`. Before claiming something works: RUN IT and record the exact command and result.

## Compute strategy

- Local: RTX 3050 4 GB, development and nano/small YOLO, tracking, OpenCV, overlays, API/UI, and short clips.
- Remote: RTX 3080 10 GB at the authorized host, reserved for larger-model evaluation or training. Use an isolated `~/football-intelligence-gpu` directory only. Do not store its access credential anywhere.
- Fallback order: local GPU -> remote GPU -> smaller model -> CPU -> disable optional component.
- Check GPU load before expensive work. Never stop unrelated processes.
- Python target: 3.11 or 3.12. The host's system Python 3.14 is not the application runtime.

## Setup and commands

```bash
cp .env.example .env
docker compose build
docker compose run --rm app pytest
docker compose up backend ui
```

Native development, if Python 3.11/3.12 is available:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,ml]'
pytest
ruff check .
uvicorn football_intelligence.api:app --reload --port 8000
streamlit run src/football_intelligence/ui.py --server.port 8501
python -m football_intelligence.worker --job-id JOB_ID
```

Demo processing:

```bash
python -m football_intelligence.cli process tests/assets/football-demo.mp4
```

## Architecture and coding rules

- Use focused modules and typed Pydantic/domain schemas.
- Hide vendor APIs behind `Detector` and `Tracker` protocols.
- Normalize timestamps in integer milliseconds; derive them from source FPS/frame index and retain frame index.
- Use semantic internal events defined in `football_intelligence.bus`; the prototype bus is in-process and replaceable.
- Persist metadata in SQLite and media on the filesystem.
- Keep job transitions explicit and validated. A stopped job must not later become completed.
- Continue past an isolated unreadable frame or inference failure when safe; retain/log the failure. Fail the job for source, writer, or persistent model failures.
- Render to a temporary video and use FFmpeg to produce browser-playable H.264 MP4 while preserving resolution, aspect ratio, and source FPS.
- Use structured logging. Never log credentials or full sensitive source URLs.

## Model and semantic rules

- Default detector is a replaceable Ultralytics adapter using a small, legally usable pretrained weight; CPU fallback is mandatory.
- Track IDs are visual continuity identifiers, never player identities.
- Team labels are only `team_1`, `team_2`, or `unknown`.
- Every semantic event contains confidence, evidence, source, and `needs_review`.
- Candidate events use temporal windows. Never infer shots, passes, fouls, cards, goals, identity, or offside from one static frame.
- Do not claim a research model is integrated until its weights load and inference runs in this repository.
- Physical coordinates/speeds remain approximate unless calibration is validated.

## Testing and git rules

- Use test-driven development: write a behavior test, observe the intended failure, implement minimally, and rerun relevant/full tests.
- Required automated coverage includes timestamps, schemas, deduplication/fusion, normalization, heuristics, state transitions, health/job API, and a real video integration path.
- Run `pytest`, `ruff check .`, and the documented demo smoke test before a completion claim.
- Inspect `git diff --check` and `git status` before commits.
- Commit coherent verified milestones; never commit broken output as complete.
- Push only this repository. Do not rewrite published history unless explicitly requested.

## Definition of done

The UI accepts a football clip, starts a job, shows progress, displays a confidence/evidence timeline, and plays an annotated MP4 with attempted player/ball detection and visual tracking IDs. The API and documented commands start cleanly, tests pass, failures and limitations are honest, and this harness identifies the last verified state and next action.

Persistent records live in `docs/IMPLEMENTATION_STATUS.md`, `docs/TEST_RESULTS.md`, `docs/DECISIONS.md`, `docs/MODELS.md`, `docs/REFERENCE_REPOS.md`, and `docs/DEMO.md`.

