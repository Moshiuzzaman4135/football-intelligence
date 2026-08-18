# Models

Research date: 2026-08-18. Availability below means a primary source exposes code/checkpoints. The YOLO11n row has now been loaded and run through this repository; other research candidates remain unintegrated. VRAM statements are planning estimates until measured.

## Rules and fallback

Verify source, license, weights, classes/events, Python compatibility, VRAM, and an actual inference run. Fallback order is local GPU -> remote RTX 3080 -> smaller model -> CPU -> disable optional component. Track IDs never imply named identity.

## Candidate matrix

| Candidate | Verified availability / coverage | License / compatibility | Compute expectation | Decision |
|---|---|---|---|---|
| [gianpaj four-class YOLOv8x](https://huggingface.co/gianpaj/football-players-detection-1) | `best.pt`/`last.pt`; ball, goalkeeper, player, referee; 640 input; 68.1M params | AGPL-3.0/Ultralytics; Python >=3.8 stated | Borderline 2.5-4 GB estimate on 3050; practical on 3080 | Best verified role detector; test on 3080 and do not commit weights |
| [Ultralytics COCO nano/small](https://github.com/ultralytics/yolov8) | Official auto-download weights; only person + sports ball, not keeper/referee | AGPL-3.0 or Enterprise | YOLO11n ran successfully on local 4 GB GPU | Adapter and CUDA inference verified; never claim four-role classification |
| [Roboflow four-class project](https://universe.roboflow.com/roboflow-jvuqo/football-players-detection-3zvbc) | Hosted RF-DETR endpoint has four classes; standalone public weight entitlement not verified | Dataset CC-BY-4.0 does not establish model/service license; credentials required | Hosted, not local | Reject for credential-free core; optional comparison only |
| [ByteTrack](https://github.com/FoundationVision/ByteTrack) | Detector-box association; no football tracker weight required | MIT; original dependency stack is old | Negligible VRAM relative to detector | Preferred permissive tracker; use lightweight in-repo IoU fallback first |
| [Ultralytics ByteTrack/BoT-SORT](https://github.com/ultralytics/ultralytics) | Built-in configs; ByteTrack needs no extra weight; BoT-SORT ReID optional | Ultralytics AGPL/Enterprise | Light without ReID | Shortest ML demo path; gate on license and measured ID quality |
| [Original BoT-SORT](https://github.com/NirAharon/BoT-SORT) | MOT examples, no verified football ReID weight; moving-camera compensation | MIT; legacy pins | Use with small detector on 3050 or 3080 | Evaluate only if ByteTrack ID switches justify added complexity |
| [Yahoo Spivak SoccerNet](https://github.com/yahoo/spivak) | Model zoo has pretrained 17-class SoccerNet-v2 spotters over precomputed features | Apache-2.0 code; CC-BY-4.0 weights; legacy TensorFlow ~2.7 | Inference likely modest; feature extraction/RAM dominate | Top later offline event-spotting option; isolate its legacy environment |
| [SoccerNet NetVLAD++](https://github.com/SoccerNet/sn-spotting/tree/main/Benchmarks/TemporallyAwarePooling) | 17-class code; claimed five models but public checkpoint files/download were not verified | Apache-2.0 benchmark; legacy Python 3.8/PyTorch 1.6 | Source reports <2 GB for training over features | Reproducible fallback only after training or documented weights |
| [VideoMAE](https://github.com/MCG-NJU/VideoMAE) | Generic checkpoints, no official SoccerNet 17-event checkpoint verified | Mostly CC-BY-NC-4.0 | 4 GB constrained; 10 GB inference experiments plausible | Research/fine-tuning only, not vertical slice |
| [recokick ball-action spotting](https://github.com/recokick/ball-action-spotting) | Drive-linked weights; 12 dense ball actions; tuned for RTX 3080 | MIT repo; NVIDIA video stack, Python unspecified | 3080 is documented target | Strong later 3080 experiment after M0-M8 |

## Event coverage notes

SoccerNet-v2 covers 17 broadcast events including goals, shots on/off target, fouls, corners, cards, substitutions, offside, and restarts. The recokick task is separate and covers 12 dense ball actions including pass, drive, header, high pass, cross, shot, tackle, free kick, and goal. Neither consumes our YOLO tracks directly; feature extraction and domain validation are separate work.

## Licensing decision

The repository can expose an optional Ultralytics adapter but will not bundle its package or weights into the core install/image until the intended deployment license is confirmed. The deterministic OpenCV/IoU path remains the credential-free baseline. A private/commercial deployment must resolve Ultralytics AGPL/Enterprise terms; a dataset license is not a model/code license.

## Verified YOLO11n local run

Weight: ignored `models/yolo11n.pt`; SHA-256 `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`. Runtime: existing CUDA/PyTorch image plus ignored `data/ml-site` containing Ultralytics 8.4.121. This was a development experiment, not a dependency of the published core image.

```bash
docker run --rm --gpus all \
  -e PYTHONPATH=/app/data/ml-site:/app/src \
  -e YOLO_CONFIG_DIR=/tmp \
  -e FOOTBALL_DETECTOR=ultralytics \
  -e FOOTBALL_MODEL_NAME=yolo11n.pt \
  -e FOOTBALL_DEVICE=0 \
  -v "$PWD:/app" -w /app/models \
  --entrypoint /usr/bin/python3 \
  vllm/vllm-openai:unlimited-ocr \
  -m football_intelligence.cli process \
  /app/tests/assets/football-demo.mp4 --data-root /app/data
```

Result: 436/436 frames, zero frame errors, 17.2268 seconds total, 25.309 end-to-end FPS, 54.223 detector FPS, 1,207 normalized player observations, and 12 ball observations. Output was H.264 1920x1080 at 25 FPS and 17.44 seconds. The clip was a wide, somewhat blurred training-field view and produced no temporal event candidate. Peak VRAM was not sampled.

The command depends on a machine-local image and ignored package directory, so it is evidence of the adapter/GPU execution—not the general quick-start path. A portable optional ML image is a later packaging task.

## Next model test sequence

1. Run the deterministic pipeline to validate mechanics.
2. Install optional ML dependencies in a separate image/environment.
3. ~~Measure COCO nano player/ball output and FPS locally.~~ Completed for YOLO11n; qualitative recall was not formally labeled.
4. Download the four-class YOLOv8x weight into the isolated remote workspace, run it on only the selected clip, retrieve results, and compare.
5. Promote a real-match default only after recording checksum, exact command, runtime, FPS, VRAM, qualitative failures, and license.

Remote commands will be documented here without credentials. The application runtime never calls SSH.
