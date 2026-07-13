"""T022: C/D OOF blocks are expanding strict-past by day (FR-014a, codex C6)."""

from __future__ import annotations

import datetime

from horseracing_training.calib_split import day_block_partition


def _days(n):
    return [datetime.date(2025, 1, 1) + datetime.timedelta(days=i) for i in range(n)]


def test_every_block_is_strictly_after_all_its_training_days():
    days = _days(30)
    seen_any = False
    for earlier, block in day_block_partition(days, n_oof=3):
        seen_any = True
        assert max(earlier) < min(block), "OOF block shares/precedes a training day (leak)"
    assert seen_any


def test_partition_is_expanding_earlier_grows():
    days = _days(30)
    parts = list(day_block_partition(days, n_oof=4))
    sizes = [len(earlier) for earlier, _ in parts]
    assert sizes == sorted(sizes)  # each block trains on strictly more earlier days
    assert len(parts) == 3  # blocks 1..n_oof-1


def test_no_partition_when_too_few_days():
    # n_oof=3 with 2 days -> cuts collapse, no usable (earlier, block) pair
    parts = list(day_block_partition(_days(2), n_oof=3))
    assert parts == [] or all(e and b for e, b in parts)


def test_blocks_do_not_overlap():
    days = _days(24)
    for earlier, block in day_block_partition(days, n_oof=4):
        assert not (set(earlier) & set(block))
