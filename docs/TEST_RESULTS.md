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

No application behavior tests or performance measurements have run yet.

## Domain, persistence, and events — 2026-08-18

- Red evidence: domain/timebase imports failed before implementation; storage/bus/events imports failed before implementation.
- Domain/timebase: 20 tests passed.
- Storage/bus/events: 10 tests passed after fixing a reproduced class-scope annotation shadowing issue.
- Full suite: 30 tests passed in 0.20 seconds.
- Covered: timestamp conversion, schema validation, track normalization defaults, event time validation, lifecycle transitions, progress monotonicity, persistence round trips, subscriber isolation, temporal kick evidence, deduplication, and noisy-or fusion.
