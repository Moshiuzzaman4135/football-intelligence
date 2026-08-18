"""Streamlit showcase for upload, progress, video, and evidence timeline."""

import os

import requests


def confidence_label(confidence: float) -> str:
    return f"{confidence:.0%}"


def format_evidence(evidence: dict) -> str:
    return (
        f"{evidence['kind']}: {evidence.get('value')} "
        f"({confidence_label(evidence.get('confidence', 0))})"
    )


def main() -> None:
    import streamlit as st

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
        st.rerun()

    job_id = st.session_state.get("job_id")
    if not job_id:
        st.info("Upload a 30-120 second clip to begin.")
        return

    status_response = requests.get(f"{api_url}/jobs/{job_id}/status", timeout=10)
    status_response.raise_for_status()
    job_status = status_response.json()
    st.progress(
        job_status["progress"] / 100,
        text=f"{job_status['status']} — {job_status['progress']}%",
    )
    if job_status.get("error"):
        st.error(job_status["error"])
    if job_status["status"] in {"created", "running", "stopping"}:
        if st.button("Refresh progress"):
            st.rerun()
        return

    if job_status["status"] != "completed":
        st.warning(f"Job ended with status: {job_status['status']}")
        return

    left, right = st.columns([2, 1])
    with left:
        st.subheader("Annotated video")
        video_response = requests.get(
            f"{api_url}/jobs/{job_id}/annotated-video", timeout=120
        )
        video_response.raise_for_status()
        st.video(video_response.content)
        if job_status.get("metrics"):
            st.json(job_status["metrics"], expanded=False)

    with right:
        st.subheader("Event timeline")
        events_response = requests.get(f"{api_url}/jobs/{job_id}/events", timeout=10)
        events_response.raise_for_status()
        events = events_response.json()
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
                st.caption("Needs review" if event["needs_review"] else "High-confidence evidence")
                for evidence in event["evidence"]:
                    st.write(f"- {format_evidence(evidence)}")
                st.caption("Sources: " + ", ".join(event["source"]))


if __name__ == "__main__":
    main()
