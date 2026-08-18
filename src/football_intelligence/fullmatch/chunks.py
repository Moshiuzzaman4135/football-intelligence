"""Deterministic planning for overlapping full-match processing chunks."""


def plan_chunks(
    duration_ms: int,
    chunk_ms: int = 120_000,
    overlap_ms: int = 5_000,
) -> list[tuple[int, int]]:
    """Return contiguous coverage windows with the requested overlap."""
    if duration_ms <= 0:
        raise ValueError("duration_ms must be greater than zero")
    if chunk_ms <= 0:
        raise ValueError("chunk_ms must be greater than zero")
    if overlap_ms < 0 or overlap_ms >= chunk_ms:
        raise ValueError("overlap_ms must be non-negative and less than chunk_ms")

    chunks: list[tuple[int, int]] = []
    start_ms = 0
    while True:
        end_ms = min(start_ms + chunk_ms, duration_ms)
        chunks.append((start_ms, end_ms))
        if end_ms == duration_ms:
            return chunks
        start_ms = end_ms - overlap_ms
