# Full-Match Football Intelligence Production Design

## Objective

Upgrade the verified short-clip showcase into an authenticated on-prem service that accepts downloaded MP4, MKV, or MOV broadcasts up to 150 minutes and 12 GiB. A 90-minute 1080p match should complete on the RTX 3080 in no more than 90 minutes and produce evidence-backed core events, scoreboard OCR, tracking, team/anonymous-track heat maps, event clips, and an annotated match video.

YouTube URL ingestion is out of scope. Track IDs remain anonymous visual continuity identifiers. Production readiness is a measured release gate, not a claim made from implementation alone.

## Architecture

The production deployment consists of FastAPI, PostgreSQL, Redis, Celery CPU/GPU workers, MinIO, a React/TypeScript web application, and Caddy. The local machine hosts control-plane services and CPU work. The remote RTX 3080 runs a Docker GPU worker over LAN service APIs; SSH is only for deployment and maintenance.

Uploads use S3 multipart transfer with 16 MiB parts, retries, resumption, and checksums. The original remains immutable in object storage. FFmpeg produces an inference proxy capped at 1080p and 25 FPS. Processing is split into restartable 120-second chunks with 5-second overlaps and explicit stage checkpoints.

Stages are: upload validation, source probe/proxy, shot classification, OCR, detection/tracking, team/calibration, action spotting, event fusion, heat maps, clips, and annotated rendering. Each task is idempotent and writes immutable versioned artifacts before atomically recording completion.

PostgreSQL stores users, jobs, stages, event metadata, reviews, OCR state, calibration quality, track summaries, model manifests, and audit logs. Large videos, clips, raw observations, Parquet files, heat maps, and rendered outputs stay in MinIO.

## Intelligence pipeline

Scoreboard discovery samples early frames for temporally stable text regions. PaddleOCR runs behind `OcrEngine`; an operator can correct the ROI and rerun only OCR and downstream fusion. `ScoreboardObservation` records video time, match clock, period, teams, score, confidence, region, and frame reference. Consensus requires monotonic clocks, stable score changes for five seconds, and explicit period reset handling.

Live wide shots receive football-specific detection at 12.5 FPS. Ball inference uses source-rate frames in a bounded predicted search area and event windows. ByteTrack is the default normalized tracker; BoT-SORT remains a benchmark adapter. Jersey clustering produces `team_1`, `team_2`, or `unknown` with temporal voting and an operator swap correction.

Pitch keypoints/lines produce homographies into a 105 by 68 coordinate system. Homographies with poor geometry or excessive reprojection error are rejected. Manual pitch-point correction is available. Heat maps are team-level and anonymous-track-level only.

`ActionSpotter` isolates model runtimes. The first core candidate is SoccerNet CALF's pretrained 17-action model converted to ONNX with parity validation. A permitted SoccerNet ball-action model may supplement goal, free-kick, shot, and throw-in evidence. Every runtime requires a registry entry containing source, exact version, license, weights checksum, classes, device, benchmark, and limitations. GPL/AGPL or unclear components are excluded from the default distributable image unless obligations are explicitly accepted.

Fusion produces `confirmed`, `candidate`, or `rejected` events. Confirmed events normally require two independent producers. A confirmed goal requires a stable score increase plus compatible action/replay evidence. Free kicks require an action spot plus stoppage/restart or spatial ball evidence. Operator decisions append audit history without replacing original evidence.

## Public contracts

Authentication exposes login, logout, and current-user endpoints with admin, operator, and viewer roles. Upload endpoints create multipart sessions, issue presigned part URLs, and complete verified uploads. Job endpoints start, stop, retry, report stages, stream progress using SSE, return filtered events/tracks/heat maps/artifacts, update scoreboard or pitch regions, and rerun individual stages.

New normalized types are `JobStage`, `ScoreboardObservation`, `CalibrationObservation`, `UploadSession`, `Artifact`, `EventReview`, and `ModelManifest`. `FootballEvent` gains status, period, numeric match clock, score transition, producer version, and review metadata while retaining current fields for compatibility.

## Security and operations

Local accounts use Argon2 password hashes and signed HTTP-only sessions with CSRF protection. Caddy terminates TLS. Secrets are runtime-only and never committed. APIs enforce roles, rate limits, file quotas, extension plus ffprobe validation, path isolation, and audit logging. Health/readiness, structured logs, Prometheus metrics, backups, restore verification, configurable 30-day retention, dependency locks, SBOM, and container scanning are release requirements.

## Acceptance gates

- Existing 64 tests remain green.
- Confirmed core-event precision is at least 85 percent within five seconds; candidate recall is at least 75 percent on held-out annotated footage.
- Score state accuracy is at least 99 percent after ROI confirmation; match-clock median error is at most one second.
- Player mAP50 is at least 0.80, ball mAP50 at least 0.50, wide-shot tracking IDF1 at least 0.65, and accepted calibration median pitch error at most 1.5 metres.
- A 90-minute 1080p match completes within 90 minutes on the RTX 3080.
- Worker interruption resumes from the last checkpoint without duplicate events or artifacts.
- Annotated output preserves duration, aspect ratio, audio, timestamp consistency, and browser playback.
- Backup/restore reproduces users, jobs, events, reviews, and artifact references.

Until these measurements exist, the release remains beta and exposes uncertainty honestly.
