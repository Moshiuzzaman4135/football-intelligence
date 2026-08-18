# Demo

## DEMO STARTUP

```bash
cp .env.example .env
# Replace every replace-with-... value in .env. Never commit .env.
docker compose up --build -d
docker compose run --rm --no-deps \
  -e FOOTBALL_OBJECT_STORE_BACKEND=filesystem -e FOOTBALL_DATABASE_URL= \
  -v "$PWD:/app" backend \
  python -m football_intelligence.demo_fixture \
  /app/data/uploads/synthetic-football-demo.mp4
```

Open `http://localhost:8510`. FastAPI documentation is at `http://localhost:8010/docs`. Both URLs were verified on 2026-08-18. If these host ports are occupied, set `FOOTBALL_BACKEND_PORT` and `FOOTBALL_UI_PORT` before starting Compose.

## DEMO VIDEO

Primary known-event fixture: `data/uploads/synthetic-football-demo.mp4`, generated locally by `football_intelligence.demo_fixture`. It is 30 seconds, 640x360, and 10 FPS, with two colored player shapes and a white ball whose rapid movement is deliberately temporal rather than a one-frame label.

Real-footage evaluation fixture: ignored `tests/assets/football-demo.mp4`, from [Pexels Training Field](https://www.pexels.com/video/training-field-854173/), listed Free to use (CC0). It is 17.44 seconds, H.264, 1920x1080, 25 FPS. SHA-256: `00b79b097cc0e9a68fcb40f29aa9bfd18ebc2bfa81e9cd299d61ded86db9e49a`.

## WHAT TO SHOW

1. Choose `data/uploads/synthetic-football-demo.mp4` in the browser and click **Process video**.
2. Show status/progress advancing to completion.
3. Play the H.264 annotated output; point out player/ball boxes, visual track IDs, timestamp, and trajectory trail.
4. Expand the event timeline entry and show confidence, ball-speed evidence, source, and the explicit review flag.
5. Show processing metrics in the video panel.
6. Optionally explain the actual YOLO11n real-clip result in `docs/MODELS.md` and why the deterministic detector is not claimed as accurate on real footage.

## EXPECTED EVENTS

The generated fixture produces one `KICK CANDIDATE` around 1.60-1.70 seconds at confidence 85.0%. Its evidence records approximately 300 pixels/second ball speed, about 59 pixels of player proximity, frames 16 and 17, continuous ball track 4, relevant player track 3, source `heuristic.temporal`, and `needs_review=true`.

The selected real clip is not expected to produce an event with current heuristics. It is useful for showing actual person/ball model behavior, not an event-rich match sequence.

## KNOWN LIMITATIONS

- Default OpenCV color detection is a deterministic synthetic/degraded path and is noisy on real footage.
- Optional YOLO11n has only COCO person/sports-ball coverage; goalkeeper/referee roles require football-specific weights.
- IoU visual tracking is not ByteTrack and can switch IDs during occlusion/camera motion.
- Team clustering, pitch/radar coordinates, RTSP, action spotting, OCR/audio/VLM evidence, and websocket updates are deferred.
- Event types are reviewable candidates, not Opta-level assertions.
- Durable full-match processing is single-host and one-at-a-time; its manual-ROI OCR and screen-space heat map are auditable MVP outputs, not robust goal/free-kick recognition or pitch calibration.

## WHAT CAN BE TESTED NOW

1. Run the short generated fixture through Streamlit and verify progress, visual tracking IDs, the kick candidate/evidence, and annotated-video playback.
2. Use FastAPI `/docs` to test jobs, status, stop, events, compact tracks, and the annotated-video endpoint.
3. Use `/uploads` plus the presign/status/complete/abort endpoints to test restart-safe MinIO multipart upload. Each presign response supplies the exact length and SHA-256 headers required for the direct PUT.
4. After completing a MinIO upload, call `POST /jobs/{id}/full-match/run`. Poll `/full-match/status`, then inspect `/scoreboard`, `/heat-map`, `/events`, and the range-capable `/annotated-video` response. Repeating the run request is safe and resumes an orphaned running manifest after an API restart.
5. The full-match manifest and artifacts live under `data/fullmatch/{job_id}`. Completed chunks have SHA-256 checksums; raw OCR text/confidence remains audit evidence in the manifest, while raw frame observations never enter SQL.
6. Run `pytest -q`; live MinIO and Docker-host Compose tests remain environment-gated, while the generated 121-second two-chunk media integration runs in the normal Docker suite.

The full-match MVP is deliberately single-host and admits one match at a time. Set the normalized `FOOTBALL_SCOREBOARD_REGION_*` values for the broadcaster before a real run. OCR cannot confirm a goal by itself, and the heat map is screen-space density—not pitch-calibrated player position.
