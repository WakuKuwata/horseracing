"""Guard the 馬番/枠番 column order, verified against real JRA-VAN rows.

Fixtures write via the layout constants, so they can't catch a real-file column swap on their own.
This pins the empirically-verified order: in the file, index 31 is the unique 馬番 and index 32 is
the 1-8 枠番. (A prior swap put 枠番 into horse_number, collapsing the canonical field for ~97% of
races — see ingest/layout.py note.)
"""

from __future__ import annotations

from horseracing_ingest import layout


def test_horse_number_and_frame_indices_not_swapped():
    # 馬番 (unique per race) is read from index 31; 枠番 (1-8 bracket) from index 32.
    assert layout.HORSE_NUMBER == 31
    assert layout.FRAME == 32
    assert layout.HORSE_NUMBER != layout.FRAME
