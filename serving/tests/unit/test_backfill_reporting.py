"""backfill が「静かに成功」しないこと(2026-08-24)。

日単位の例外隔離は意図的(1 日の失敗が range 全体を止めてはいけない)。だが例外そのものを
捨てていたので、オペレータには数字だけが残り、何を直せばよいか分からなかった。
`recommend_backfill` は以前からレース単位のエラー文言を出していたが、predict 側は出さない。

さらに CLI は無条件で 0 を返していた。スケジューラは終了コードを読むので、
「全日失敗して 1 件も書かれなかった」が「成功」と区別できなかった。
"""

from __future__ import annotations

from horseracing_serving.pipeline import BackfillCounts


def test_counts_without_errors_do_not_carry_an_errors_key():
    """正常系の出力形は変えない(既存の読み手が壊れない)."""
    d = BackfillCounts(generated=5).as_dict()
    assert "errors" not in d
    assert d["error_days"] == 0


def test_the_cause_of_each_failed_day_is_surfaced():
    d = BackfillCounts(
        error_days=2,
        errors=[("2026-01-01", "ValueError: bad"), ("2026-01-02", "KeyError: 'x'")],
    ).as_dict()
    assert d["error_days"] == 2
    assert d["errors"] == [
        {"day": "2026-01-01", "error": "ValueError: bad"},
        {"day": "2026-01-02", "error": "KeyError: 'x'"},
    ]


def test_errors_default_is_not_shared_between_instances():
    """dataclass の可変既定値は取り違えると全インスタンスで共有される."""
    a, b = BackfillCounts(), BackfillCounts()
    a.errors.append(("d", "e"))
    assert b.errors == []
