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
| [T-DEED](https://github.com/arturxe2/T-DEED) | Released 17-class SoccerNet and ball-action checkpoints; single-video inference | GPL-3.0 repo; Drive weights have no separate license; isolated Torch 2.3/NumPy 1 stack | Published small model 12.31M params/39.58 GFLOPs per 100 frames; no wall-clock/VRAM result | Do not ship; private benchmark only after explicit license acceptance |
| [AdaSpot](https://github.com/arturxe2/AdaSpot) | MIT Hugging Face checkpoints; ten released ball-action classes; single-video path needs preprocessing fixes | MIT code/weights; dynamic ROI makes ONNX export harder | Paper reports 1.97 GB for one clip; no full-match runtime | Optional small-model benchmark; cannot supply goal/free-kick evidence |
| [PnLCalib](https://github.com/mguti97/PnLCalib) | SoccerNet-trained keypoint/line weights and nonlinear point-line refinement | GPL-2.0; released weights lack separate terms/checksums; Python 3.9/Torch 2.3 environment | Two ~265 MB checkpoints; official VRAM/FPS absent | Process-isolated benchmark only after legal approval and quality/runtime measurement |
| [Broadcast2Pitch](https://github.com/yinmayoo185/SoccernetGSR) | Full detection/tracking/ReID/calibration/jersey pipeline and Google Drive weights | No root license or weight manifest; source-build/GPU-heavy stack | No published end-to-end runtime/VRAM | Reference its interfaces/evaluation only; do not copy or distribute |
| [Tesseract 5 + tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) | English OCR model for manual scoreboard crops; score/clock TSV confidence | Apache-2.0 engine and model; native CPU process | Small model (~4.1 MB), CPU at 1 FPS ROI | MVP OCR adapter; validate broadcaster-specific accuracy |

## Event coverage notes

SoccerNet-v2 covers 17 broadcast events including goals, shots on/off target, fouls, corners, cards, substitutions, offside, and restarts. The recokick task is separate and covers 12 dense ball actions including pass, drive, header, high pass, cross, shot, tackle, free kick, and goal. Neither consumes our YOLO tracks directly; feature extraction and domain validation are separate work.

AdaSpot's released model intentionally has only ten ball-action classes and excludes the sparse goal/free-kick classes, so it cannot replace the 17-event candidate. T-DEED is technically closer but fails the default distribution-license gate. PnLCalib and Broadcast2Pitch were inspected as calibration/game-state candidates, not event models.

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

## Verified Tesseract packaging

The CPU image installs Tesseract 5.5.0 and downloads `eng.traineddata` from pinned `tessdata_fast` commit `87416418657359cb625c412a48b6e1d6d41c29bd`. The build verifies SHA-256 `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2` and installs the upstream Apache-2.0 license at `/usr/share/doc/tessdata-fast/LICENSE`. A real container test read a generated manual scoreboard crop through the CLI/TSV adapter. This proves packaging and evidence plumbing, not broadcaster-specific OCR accuracy.

## CALF action spotting — qualified 2026-08-18

- Source: `https://github.com/SoccerNet/sn-spotting/tree/main/Benchmarks/CALF` (Apache-2.0). README states "License Apache v2.0"; LICENSE file confirms.
- Weights: `Benchmarks/CALF/models/CALF_benchmark/model.pth.tar` (6,966,034 bytes). SHA-256 `6a9befb8f30741da53756f89d2df675783ef8fc893457d1a920c1ed900399b4e`. Checkpoint keys: `epoch` (181), `state_dict` (27 tensors), `best_loss`, `optimizer`. 578,245 trainable parameters.
- Taxonomy: the 17 SoccerNet-v2 actions (Penalty, Kick-off, Goal, Substitution, Offside, Shots on/off target, Clearance, Ball out of play, Throw-in, Foul, Indirect/Direct free-kick, Corner, Yellow/Red/Yellow->red card).
- Architecture: `ContextAwareModel` (TemporalConv + capsule segmentation + spotting heads). Consumes precomputed SoccerNet features `(1,1,chunk_frames,512)` at 2 fps; produces segmentation `(1,240,17)` and spotting `(1,15,19)` = 15 detections x `[object_conf, normalized_frame_pos, ...17 class scores]`.
- Qualification (isolated, torch only, CPU in the vllm/OCR dev image):
  - weights load: OK (`strict=True`).
  - forward on a real-weight model: OK in 0.055 s for one 240-frame chunk (CPU).
  - decode: timestamps via `floor(frame_pos*(chunk_len-1))` -> `frame_index*500 ms`; per-class NMS over a 20 s window; unknown classes dropped.
  - end-to-end adapter on 480 random 512-d feature frames produced 19 normalized spots (e.g. `ball_out_candidate` 41.0 s, `throw_in_candidate` 46.5 s, `ball_out_candidate` 80.0 s) that normalize into `FootballEvent` with `needs_review=True`, source `action.calf`.
- Integration: `football_intelligence.action` defines the replaceable `ActionSpotter` protocol + normalized `ActionSpot`, the `CalfActionSpotter` adapter (lazy `torch` import, isolated from core deps), `action_spots_from_features` decode, and `normalize_action` -> `FootballEvent`. `action_calf_model.py` is a self-contained Apache-2.0 port of the CALF model.
- Honest boundary: raw-video feature extraction needs the legacy SoccerNet/ResNet-TF2 + PCA stack, which is intentionally NOT installed into the core image. `CalfActionSpotter.spot` accepts a precomputed `(frames, 512)` feature array at 2 fps; the isolated ResNet/PCA feature extractor is the remaining deployment step for arbitrary video. Real broadcast spotting accuracy was not benchmarked because a licensed broadcast match with these features is unavailable this session.
- Runtime: single 240-frame chunk forward 0.055 s CPU (torch 2.11 in dev image). Full-match feature extraction dominates wall-clock; not measured because the feature stack is not installed.
