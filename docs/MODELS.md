# Models

Research date: 2026-08-18. Availability below means a primary source exposes code/checkpoints; nothing is integrated until this repository loads the weight and runs inference. VRAM statements are planning estimates until measured.

## Rules and fallback

Verify source, license, weights, classes/events, Python compatibility, VRAM, and an actual inference run. Fallback order is local GPU -> remote RTX 3080 -> smaller model -> CPU -> disable optional component. Track IDs never imply named identity.

## Candidate matrix

| Candidate | Verified availability / coverage | License / compatibility | Compute expectation | Decision |
|---|---|---|---|---|
| [gianpaj four-class YOLOv8x](https://huggingface.co/gianpaj/football-players-detection-1) | `best.pt`/`last.pt`; ball, goalkeeper, player, referee; 640 input; 68.1M params | AGPL-3.0/Ultralytics; Python >=3.8 stated | Borderline 2.5-4 GB estimate on 3050; practical on 3080 | Best verified role detector; test on 3080 and do not commit weights |
| [Ultralytics COCO nano/small](https://github.com/ultralytics/yolov8) | Official auto-download weights; only person + sports ball, not keeper/referee | AGPL-3.0 or Enterprise | Expected comfortable at 640 on 4 GB | Initial lightweight ML smoke only; never claim four-role classification |
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

## Planned test sequence

1. Run the deterministic pipeline to validate mechanics.
2. Install optional ML dependencies in a separate image/environment.
3. Measure COCO nano player/ball recall and FPS locally.
4. Download the four-class YOLOv8x weight into the isolated remote workspace, run it on only the selected clip, retrieve results, and compare.
5. Promote a model only after recording checksum, exact command, runtime, FPS, VRAM, qualitative failures, and license.

Remote commands will be documented here without credentials. The application runtime never calls SSH.

