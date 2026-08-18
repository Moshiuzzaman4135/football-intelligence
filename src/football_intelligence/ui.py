"""Streamlit showcase for upload, progress, video, and evidence timeline."""

from __future__ import annotations

import os

import requests
import streamlit as st


def confidence_label(confidence: float) -> str:
    return f"{confidence:.0%}"


def format_evidence(evidence: dict) -> str:
    return (
        f"{evidence['kind']}: {evidence.get('value')} "
        f"({confidence_label(evidence.get('confidence', 0))})"
    )


def _refresh_interval() -> float | None:
    """Auto-refresh the live panel every second while a job is still running.

    Returning ``None`` stops the fragment auto-rerun once the job settles.
    """
    return 1 if st.session_state.get("_job_running", True) else None


@st.fragment(run_every=_refresh_interval)
def render_live(api_url: str, job_id: str) -> None:
    status = requests.get(f"{api_url}/jobs/{job_id}/status", timeout=10).json()
    running = status["status"] in {"created", "running", "stopping"}
    st.session_state["_job_running"] = running

    st.progress(
        status["progress"] / 100,
        text=f"{status['status']} — {status['progress']}%",
    )
    if status.get("error"):
        st.error(status["error"])
    if running:
        return
    if status["status"] != "completed":
        st.warning(f"Job ended with status: {status['status']}")
        return

    left, right = st.columns([2, 1])
    with left:
        st.subheader("Annotated video")
        video_key = f"_video_bytes_{job_id}"
        if video_key not in st.session_state:
            st.session_state[video_key] = requests.get(
                f"{api_url}/jobs/{job_id}/annotated-video", timeout=120
            ).content
        seek_time = st.session_state.get("seek_time", 0.0)
        st.video(st.session_state[video_key], start_time=seek_time)
        if status.get("metrics"):
            st.json(status["metrics"], expanded=False)

    with right:
        st.subheader("Event timeline (click a row to seek / play a clip)")
        events = requests.get(f"{api_url}/jobs/{job_id}/events", timeout=10).json()
        if not events:
            st.info("No temporal candidates crossed the configured thresholds.")
        for event in events:
            seconds = event["start_ms"] / 1000
            title = (
                f"{seconds:07.2f}s · {event['event_type'].replace('_', ' ').upper()} "
                f"· {confidence_label(event['confidence'])}"
            )
            with st.expander(title):
                st.write(event["description"])
                st.caption(
                    "Needs review"
                    if event["needs_review"]
                    else "High-confidence evidence"
                )
                seek_col, clip_col = st.columns(2)
                if seek_col.button("▶ Seek video", key=f"seek_{event['id']}"):
                    st.session_state["seek_time"] = max(0.0, event["start_ms"] / 1000)
                if clip_col.button("▶ Play clip", key=f"clip_{event['id']}"):
                    st.session_state["clip_event_id"] = event["id"]
                if st.session_state.get("clip_event_id") == event["id"]:
                    clip_bytes = requests.get(
                        f"{api_url}/jobs/{job_id}/events/{event['id']}/clip",
                        timeout=120,
                    ).content
                    st.caption(f"Clip around {event['start_ms'] / 1000:.2f}s")
                    st.video(clip_bytes)
                for evidence in event["evidence"]:
                    st.write(f"- {format_evidence(evidence)}")
                st.caption("Sources: " + ", ".join(event["source"]))


def main() -> None:
    api_url = os.getenv("FOOTBALL_API_URL", "http://localhost:8000").rstrip("/")
    st.set_page_config(page_title="Football Video Intelligence", layout="wide")
    st.title("Football Video Intelligence")
    st.caption(
        "Evidence-backed prototype: track IDs are visual IDs, and semantic events need review."
    )

    uploaded = st.file_uploader("Upload a short football video", type=["mp4", "mov", "mkv"])
    if uploaded and st.button("Process video", type="primary"):
        response = requests.post(
            f"{api_url}/jobs/upload",
            files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type)},
            timeout=120,
        )
        response.raise_for_status()
        job = response.json()
        requests.post(f"{api_url}/jobs/{job['id']}/start", timeout=10).raise_for_status()
        st.session_state["job_id"] = job["id"]
        st.session_state.pop("seek_time", None)
        st.session_state.pop("clip_event_id", None)
        st.session_state.pop(f"_video_bytes_{job['id']}", None)
        st.rerun()

    job_id = st.session_state.get("job_id")
    if not job_id:
        st.info("Upload a 30-120 second clip to begin.")
        return

    render_live(api_url, job_id)


if __name__ == "__main__":
    main()
