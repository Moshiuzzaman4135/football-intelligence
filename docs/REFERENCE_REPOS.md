# Reference Repository Findings

Both TigerIT repositories were inspected statically and remain unmodified (`main...origin/main`, clean after audit). These are architectural references, not copied implementations.

| Pattern observed | Repository | Relevant file/module | Why useful | Decision |
|---|---|---|---|---|
| FastAPI dispatches distinct long-running worker queues | vision-relay | `app/main.py`, `app/celery/celery_worker.py`, `app/routers/job.py` | Separates HTTP latency from file, RTSP, clip, audio, and maintenance workloads | Adopt module/event boundaries now; defer Celery/Redis until workload evidence requires processes |
| Latest-frame RTSP reader and sampling clock | vision-relay | `app/utils/video_utils.py:531-741` | Keeps live inference fresh instead of accumulating stale frames | Adopt for later RTSP with freshness/drop metrics |
| Bounded queue with drop-oldest backpressure | vision-relay | `app/utils/video_utils.py:553-604,680-739`, `app/config.py:160-165` | Bounds memory and latency when inference is slower than capture | Adopt for live streams only; never silently drop archival evidence |
| Stream health/retry is explicit product state | vision-relay | `app/worker/celery_tasks.py:337-647`, `app/utils/video_utils.py:606-670` | Makes outages visible and supports recovery transitions | Adopt state-machine idea; add cancellation/backoff when RTSP ships |
| Representative sharp frame per scene | vision-relay | `app/utils/video_utils.py:30-268`, `app/PySceneDetect/scenedetect_rtsp.py` | Reduces optional VLM work while retaining useful evidence | Defer to multimodal milestone; cap scene duration and validate replay/cut behavior |
| First/final frame sampling and FPS-based skipping | vision-relay | `app/utils/video_utils.py:311-348,431-527` | Predictable file processing coverage | Adopt concept; reject blank analytic frames and prefer PTS for variable-rate sources |
| OpenCV decode plus FFmpeg media operations | vision-relay | `app/utils/video_utils.py`, `app/worker/celery_tasks.py` | Practical division between per-frame logic and codec/container work | Adopt with pinned image, captured stderr/return codes, and output validation |
| Redis carries control/routing, MinIO carries JPEG/media | vision-relay | `app/utils/redis_utils.py:156-441`, `app/worker/celery_tasks.py:1225-1257` | Avoids putting large binary payloads on the control plane | Adopt principle; current prototype uses in-memory control and filesystem media |
| Durable start/stop uses lock, tracked task IDs, revoke, cleanup phases | vision-relay | `app/routers/job.py:126-807`, `app/utils/job_lifecycle.py`, Celery signals | Demonstrates lifecycle/cleanup as an idempotent workflow | Adopt explicit legal transitions now; persist operation journal if distributed later |
| RTSP timestamp is ingest-time surrogate (`frame_timestamp=0`) | vision-relay | `app/worker/celery_tasks.py:559-584`, `app/utils/frame_utils.py` | Important warning for a sports timeline | Reject: retain frame/PTS time and ingest/capture wall times separately |
| Source-specific URL resolution | vision-relay | `app/VisionRelayDB/app/models.py:78-94` | Centralizes local/object/RTSP resolution | Adopt typed source descriptors instead of string-prefix branching in later RTSP work |
| Correlated monitor/job/task logging | vision-relay | `app/worker/celery_tasks.py`, `app/routers/job.py` | Makes multi-worker incident tracing possible | Adopt structured correlation; reject full settings dumps that may expose secrets |
| FFprobe metadata before persistence | govms | `internal/utils/video_utils.go:14-65` | Dimensions/duration/FPS are prerequisites for timestamps and overlays | Adopt and harden with timeout/stderr/codec validation |
| Overlapping temporal video shards with global offset | govms | `internal/utils/video_utils.go:67-106`, `internal/services/file_services.go:26-58` | Prevents short events disappearing at segment boundaries | Defer until long-match scaling; require PTS mapping and overlap deduplication |
| Hierarchical media object keys | govms | `internal/services/file_services.go:26-124` | Supports per-job traceability and cleanup | Adopt directory intent; store relative object/path keys rather than permanent endpoint URLs |
| Explicit visible job states | govms | `internal/templates/partials/monitor_detail_view.html:239-276` | Users can distinguish preparing/running/stopped/completed/failed | Adopt in API/UI with legal state transitions |
| Responsive Canvas boxes synchronized to video | govms | `internal/templates/partials/event.html:1794-2249` | Scales source coordinates, interpolates tracks, redraws on playback, permits hit testing | Adopt concepts in later rich UI; M0-M8 burns overlays into MP4 for reliability |
| Track interpolation and deterministic colors | govms | `internal/templates/partials/event.html:2035-2191` | Makes sampled detections visually continuous and IDs comprehensible | Adopt trails/colors; cap gaps so cuts/occlusions do not create false continuity |
| Multi-video master time and event timeline drill-down | govms | `internal/templates/partials/event.html:897-1015,1304-1584` | Valuable long-match review interaction | Adopt timeline now; defer multi-camera until clock offset/drift is represented |
| Canvas ROI editor | govms | `internal/templates/partials/draw_regions.html` | Maps naturally to pitch zones/goal areas | Defer calibration milestone; use normalized geometry and validation when built |
| Poll status/event list every 10 seconds | govms | `monitor_detail_view.html`, `event.html`; no WebSocket/SSE found | Simple fallback for a small demo | Adopt short adaptive polling in Streamlit; consider SSE after proven need |
| Request-thread upload/FFmpeg/orchestration | govms | `internal/handlers/monitor_handler.go:465-758` | Exposes latency and failure coupling to avoid | Reject; schedule background job after fast validated upload |
| Dynamic event SQL and state-changing GETs | govms | `internal/services/event_services.go:24-178`, handlers/routes | Security and semantics risks | Reject; use parameterized repository operations and POST lifecycle endpoints |

## Net architectural effect

The audits support a modular single-process first release with explicit lifecycle, typed timestamps, bounded background work, filesystem media, and simple polling. They also define the upgrade path: RTSP latest-frame queues, durable distributed lifecycle, time-indexed event APIs, and interactive Canvas overlays can be added behind existing boundaries without making today’s demo depend on Redis, Celery, MinIO, or a large frontend.

## Audit caveats

The findings are static source observations, not runtime validation. `vision-relay` had no Docker/Compose definitions and no obvious test directory; `govms` had no worker queue/WebSocket/OpenCV test infrastructure. External ML services used by GOVMS were outside the repository and therefore outside the audit.
