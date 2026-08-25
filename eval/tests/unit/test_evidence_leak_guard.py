"""証拠 artifact がリーク面を増やしていないことを機械的に固定する(100 / T012・INV-E5/A4)。

守るのは 2 つ。

1. **共変量が結果を読まない**。証拠行に載せてよいのはレース属性だけで、着順・オッズ・人気・
   払戻に触れてはならない。共変量は判定式に入らない記録専用(FR-014)なので、リーク面を
   増やしてまで載せる理由が無い。
2. **証拠が特徴量に還流しない**。features パッケージが evidence を import したら落ちる(憲法 II)。
"""

from __future__ import annotations

import ast
import datetime
import pathlib

import horseracing_eval.evidence as evidence_mod
from horseracing_eval.evidence import race_covariates
from horseracing_eval.paired import paired_eval
from horseracing_eval.predictor import HorseEntry, RaceContext

from .test_evidence_recompute_parity import CFG, _FakeFactory, _races


def _repo_root() -> pathlib.Path:
    """``specs/`` と ``features/`` を持つディレクトリを探す(パッケージの入れ子深さに依存しない)。"""
    for parent in pathlib.Path(evidence_mod.__file__).resolve().parents:
        if (parent / "features").is_dir() and (parent / "eval").is_dir():
            return parent
    raise AssertionError("リポジトリ直下を特定できない")


_REPO = _repo_root()

#: 共変量のキーにも値の出所にも現れてはならない語。
FORBIDDEN_TOKENS = (
    "finish", "order", "rank", "win", "place", "show", "odds", "popular",
    "payout", "dividend", "result", "winner", "label", "target",
)


def _imports(path: pathlib.Path) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_covariate_keys_carry_no_result_tokens() -> None:
    cov = race_covariates("202401010101", field_size=12, race_day="2024-01-01")
    for key in cov:
        assert not any(tok in key.lower() for tok in FORBIDDEN_TOKENS), key


def test_covariates_are_unchanged_when_the_result_changes() -> None:
    """**挙動での leak-guard**: 誰が勝ったかを変えても共変量は動かない。

    名前の検査(トークン)は規約であって安全境界ではない。実際に結果を差し替えて不変性を見る。
    """
    races = _races()
    base = paired_eval(_FakeFactory(0.62, "c"), _FakeFactory(0.45, "a"), races,
                       gate_config=CFG, first_valid_year=2020)
    before = {r.race_id: r.covariates for r in base.evidence.rows}

    # 勝ち馬ラベルを別の馬に付け替える
    from horseracing_eval.dataset import EvalRace, ScoringLabel
    swapped = [
        EvalRace(
            context=er.context,
            labels=(ScoringLabel("h1", 1, 1, 1), ScoringLabel("h0", 0, 1, 1),
                    ScoringLabel("h2", 0, 0, 1)),
        )
        for er in races
    ]
    after_rep = paired_eval(_FakeFactory(0.62, "c"), _FakeFactory(0.45, "a"), swapped,
                            gate_config=CFG, first_valid_year=2020)
    after = {r.race_id: r.covariates for r in after_rep.evidence.rows}

    assert before == after, "結果を変えたら共変量が動いた = 共変量が結果を読んでいる"
    # 対照: 差そのものは当然動く(テストが何も測っていない状態を排除する)
    assert {r.race_id: r.diff for r in base.evidence.rows} != \
           {r.race_id: r.diff for r in after_rep.evidence.rows}


def test_covariates_depend_only_on_declared_race_attributes() -> None:
    """頭数と開催年だけで決まる(それ以外の入力に反応しない)。"""
    a = race_covariates("A", field_size=16, race_day="2024-03-05")
    b = race_covariates("B", field_size=16, race_day="2024-11-30")
    c = race_covariates("A", field_size=8, race_day="2024-03-05")
    assert a == b            # race_id と月日には依存しない
    assert a != c            # 頭数には依存する


def test_evidence_module_does_not_import_training_or_features() -> None:
    for imported in _imports(pathlib.Path(evidence_mod.__file__)):
        assert not imported.startswith(("horseracing_training", "horseracing_features")), imported


def test_features_package_never_imports_evidence() -> None:
    """憲法 II: 評価由来の値をモデル特徴に還流させない(INV-A4)。"""
    src = _REPO / "features" / "src" / "horseracing_features"
    assert src.is_dir(), src
    for py in src.rglob("*.py"):
        for imported in _imports(py):
            assert "evidence" not in imported and not imported.startswith("horseracing_eval"), (
                f"{py.relative_to(_REPO)} imports {imported} — 証拠がモデル特徴に還流している"
            )


def test_market_data_is_deliberately_absent_from_covariates() -> None:
    """市場由来の共変量を**意図的に載せていない**ことを pin する。

    評価経路で触れる市場データは ``ResultMarket``(結果確定時の odds/popularity)だけで、
    これはリーク参照線専用と明示されている。加えて先行測定で paired 差との相関はほぼ厳密に
    ゼロだった。共変量は記録専用なので、載せる利得が無く、リーク面だけが増える。
    """
    horses = (HorseEntry(horse_id="h0", horse_number=1),
              HorseEntry(horse_id="h1", horse_number=2))
    ctx = RaceContext(race_id="R1", race_date=datetime.date(2024, 1, 1), started_horses=horses)
    cov = race_covariates(ctx.race_id, field_size=len(ctx.started_horses),
                          race_day=ctx.race_date.isoformat())
    assert set(cov) == {"field_size", "race_year"}
