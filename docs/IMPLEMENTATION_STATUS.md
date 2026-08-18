# Implementation Status

## LAST VERIFIED STATE

M0 discovery and the M1 recovery/runtime harness are verified. The CPU Docker image builds, imports the package/OpenCV, includes FFmpeg, and passes Ruff. No pipeline behavior has yet been claimed.

## CURRENT MILESTONE

M2 domain, persistence, and video-ingestion foundations (test-first).

## COMPLETED MILESTONES

- M0 Repository discovery.
- M1 Harness + project skeleton.

## CURRENT WORK

Write and observe the first failing domain/timebase tests, then implement their normalized interfaces.

## LAST SUCCESSFUL COMMAND

`docker compose run --rm --no-deps backend ruff check .` -> `All checks passed!`

## NEXT EXACT ACTION

Create `tests/test_timebase.py` and `tests/test_domain.py`, run them to observe the expected missing-module failures, then implement `timebase.py` and `domain.py`.

## FILES MODIFIED

Harness documents, design/plan, package metadata, Docker/Compose configuration, and package initializer.

## MODELS INSTALLED

None in this project.

## MODELS TESTED

None in this project.

## LOCAL GPU STATUS

RTX 3050 4 GB visible through host and CUDA container. Docker `--gpus all` successfully exposed the 4096 MiB GPU with driver 610.43.03. System Python 3.14.6 has no PyTorch.

## REMOTE GPU STATUS

Connectivity verified. RTX 3080 10 GB was idle except display usage; Docker 29.3.0/Compose 5.1.0 available, 62 GiB RAM available. Root filesystem has about 9 GiB free and `/mnt/shared` about 76 GiB; no project directory created yet. `python` is absent; `python3` still needs checking.

## TESTS RUN

- `docker compose build` succeeded.
- Container import printed `football_intelligence 0.1.0` and OpenCV `4.14.0`.
- Container FFmpeg is `7.1.5-0+deb13u1`.
- `ruff check .` passed.

## KNOWN FAILURES

- `import torch` fails in system Python because PyTorch is not installed.
- Remote `python --version` fails because only the `python3` command may be installed.

## KNOWN LIMITATIONS

No implemented pipeline, API, UI, model integration, annotated output, or measured demo result yet. Model research is source verification only. Core dependency versions are broad bounded ranges, not a lockfile yet.

## BLOCKERS

None.
