import pytest

from football_intelligence.fullmatch.chunks import plan_chunks


def test_plan_chunks_covers_a_90_minute_match_with_default_overlap():
    chunks = plan_chunks(5_400_000)

    assert len(chunks) == 47
    assert chunks[0] == (0, 120_000)
    assert chunks[1] == (115_000, 235_000)
    assert chunks[-2] == (5_175_000, 5_295_000)
    assert chunks[-1] == (5_290_000, 5_400_000)


def test_plan_chunks_keeps_overlap_at_each_chunk_boundary():
    chunks = plan_chunks(duration_ms=350_000, chunk_ms=120_000, overlap_ms=5_000)

    assert chunks == [
        (0, 120_000),
        (115_000, 235_000),
        (230_000, 350_000),
    ]


def test_plan_chunks_returns_a_final_partial_chunk():
    assert plan_chunks(duration_ms=125_000, chunk_ms=120_000, overlap_ms=5_000) == [
        (0, 120_000),
        (115_000, 125_000),
    ]


@pytest.mark.parametrize("duration_ms", [230_001, 234_999])
def test_plan_chunks_avoids_redundant_short_tails_after_partial_predecessors(duration_ms):
    assert plan_chunks(duration_ms) == [
        (0, 120_000),
        (115_000, duration_ms),
    ]


@pytest.mark.parametrize(
    ("duration_ms", "chunk_ms", "overlap_ms"),
    [
        (0, 120_000, 5_000),
        (1_000, 0, 0),
        (1_000, 1_000, 1_000),
        (1_000, 1_000, -1),
    ],
)
def test_plan_chunks_rejects_non_progressing_or_invalid_ranges(
    duration_ms, chunk_ms, overlap_ms
):
    with pytest.raises(ValueError):
        plan_chunks(duration_ms, chunk_ms, overlap_ms)
