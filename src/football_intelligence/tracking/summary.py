"""Compact persisted summaries derived from frame-level visual observations."""

from collections import defaultdict

from football_intelligence.domain import TrackObservation, TrackSummary


def summarize_tracks(observations: list[TrackObservation]) -> list[TrackSummary]:
    groups: dict[tuple[int, str], list[TrackObservation]] = defaultdict(list)
    for observation in observations:
        groups[(observation.track_id, observation.object_class)].append(observation)

    summaries = []
    for (track_id, object_class), items in sorted(groups.items()):
        ordered = sorted(items, key=lambda item: (item.timestamp_ms, item.frame_index))
        confidences = [item.confidence for item in ordered]
        known_teams = [item.team_id for item in ordered if item.team_id != "unknown"]
        summaries.append(
            TrackSummary(
                track_id=track_id,
                object_class=object_class,
                start_ms=ordered[0].timestamp_ms,
                end_ms=ordered[-1].timestamp_ms,
                first_frame=ordered[0].frame_index,
                last_frame=ordered[-1].frame_index,
                observation_count=len(ordered),
                mean_confidence=sum(confidences) / len(confidences),
                max_confidence=max(confidences),
                team_id=known_teams[0] if known_teams else "unknown",
            )
        )
    return summaries
