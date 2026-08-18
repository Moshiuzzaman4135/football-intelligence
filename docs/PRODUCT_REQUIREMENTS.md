# Product Requirements

## Objective

Deliver a same-day football-video intelligence showcase that processes a short uploaded/local clip end-to-end. Reliability and visible evidence take priority over research-grade semantic accuracy.

## Mandatory vertical slice (M0-M8)

1. Accept MP4/MOV/MKV upload or a validated local video path.
2. Probe and read the source with consistent frame-index/millisecond timestamps.
3. Attempt player and ball detection through a replaceable detector interface.
4. Produce persistent visual track IDs through a replaceable tracker interface.
5. Derive temporal candidate events with confidence and inspectable evidence.
6. Generate a browser-playable annotated MP4 preserving dimensions, aspect ratio, FPS, and timestamps.
7. Persist job, event, track-summary, source, output, and model metadata without storing video blobs in SQL.
8. Expose health, job lifecycle, status, events, tracks, and annotated-video endpoints.
9. Provide a simple upload/progress/video/timeline/evidence browser UI.

## Inputs and outputs

Inputs: uploaded video, existing local video, and RTSP when practical. The first demo is a 30-120 second legal fixture.

Outputs: normalized metadata, job progress, tracks, candidate events, evidence, logs, and annotated MP4. Optional later outputs include team labels, pitch coordinates/radar, OCR/audio signals, action-model evidence, and fused multimodal events.

## Truthfulness and quality constraints

- Track IDs are not player identities.
- Semantic results are candidates unless independently supported.
- Every event has an integer start/end timestamp, confidence in `[0,1]`, evidence, source, and `needs_review`.
- Individual frame failures are recoverable; unrecoverable source/writer/model failures produce a visible failed job.
- The core pipeline works on CPU and remains usable without the remote server.
- Do not claim Opta-level accuracy, perfect events, validated physical metrics, or production live latency.

## Acceptance criteria

The 20 definition-of-done points in the originating brief are binding. Automated tests must cover timestamps, event validation/deduplication/fusion, track normalization, heuristics, state transitions, API health/job creation, and video integration. Exact startup and demo steps belong in `README.md` and `docs/DEMO.md`.

