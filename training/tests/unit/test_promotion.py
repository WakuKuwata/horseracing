"""Feature 078 US3 (T014): the append-only promotion record (separate from immutable manifests)."""

from __future__ import annotations

import pytest

from horseracing_training.promotion import (
    PromotionError,
    current_promotion,
    read_promotions,
    record_promotion,
)

_B = "b" * 64
_M1, _M2 = "1" * 64, "2" * 64


def test_first_promotion_becomes_current(tmp_path):
    assert current_promotion(tmp_path) is None
    record_promotion(tmp_path, manifest_digest=_M1, bundle_digest=_B, at="2026-07-21T00:00:00Z")
    cur = current_promotion(tmp_path)
    assert cur["manifest_digest"] == _M1 and cur["bundle_digest"] == _B


def test_promotion_is_append_only_and_newest_wins(tmp_path):
    record_promotion(tmp_path, manifest_digest=_M1, bundle_digest=_B, at="2026-07-21T00:00:00Z")
    record_promotion(tmp_path, manifest_digest=_M2, bundle_digest=_B, at="2026-07-22T00:00:00Z",
                     note="better fit")
    entries = read_promotions(tmp_path)
    assert [e["manifest_digest"] for e in entries] == [_M1, _M2]  # both preserved, in order
    assert current_promotion(tmp_path)["manifest_digest"] == _M2


def test_rollback_is_a_new_line_not_an_edit(tmp_path):
    record_promotion(tmp_path, manifest_digest=_M1, bundle_digest=_B, at="t1")
    record_promotion(tmp_path, manifest_digest=_M2, bundle_digest=_B, at="t2")
    record_promotion(tmp_path, manifest_digest=_M1, bundle_digest=_B, at="t3")  # roll back to M1
    assert [e["manifest_digest"] for e in read_promotions(tmp_path)] == [_M1, _M2, _M1]
    assert current_promotion(tmp_path)["at"] == "t3"


def test_re_promoting_current_is_idempotent(tmp_path):
    record_promotion(tmp_path, manifest_digest=_M1, bundle_digest=_B, at="t1")
    record_promotion(tmp_path, manifest_digest=_M1, bundle_digest=_B, at="t2")  # same → no new line
    assert len(read_promotions(tmp_path)) == 1


def test_missing_digest_rejected(tmp_path):
    with pytest.raises(PromotionError):
        record_promotion(tmp_path, manifest_digest="", bundle_digest=_B, at="t1")


def test_corrupt_log_is_reported(tmp_path):
    path = tmp_path / "artifacts" / "oof" / "promotions.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"manifest_digest": "x"}\nnot json\n', encoding="utf-8")
    with pytest.raises(PromotionError, match="corrupt"):
        read_promotions(tmp_path)
