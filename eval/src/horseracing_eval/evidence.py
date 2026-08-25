"""paired 判定の per-race 証拠(feature 100 US1).

判定 1 回は 2〜4 時間かかるのに、これまで per-race の差は CI を計算した直後に捨てられていた
(``PairedReport.diffs_by_day`` は開催日 → 差のリストで、race_id も両アームの個別 loss も持たず、
複数窓を束ねる driver は verdict を書く前にそれすら落としていた — 097 の verdict.json に差の
生値は 1 件も残っていない)。事後解析が構造的に不可能な状態だったので、判定を**再現できる
最小単位**を残す。

要件の中核は「**この artifact だけ**から点推定と CI を再計算して verdict とビット一致する」
(INV-A1)。載っていない依存が見つかったら、それは artifact 側に足す。

浮動小数点の順序について
------------------------
クラスタ bootstrap は開催日を再標本化し、各日の差を連結して平均する。連結の順序が変われば
和の丸めが変わるので、順序を復元できないとビット一致は成立しない。そこで各行に**発行順**
``seq`` を持たせ、再計算は必ず ``seq`` で並べ直してから集約する。おかげでファイル上の行順は
自由に入れ替えてよい(INV-E6)。

共変量について
--------------
記録するのは ``field_size`` と ``race_year`` だけである。市場由来の量(オッズのエントロピー等)
は**意図的に載せていない**: 評価経路で触れる市場データは ``ResultMarket``(結果確定時の
odds/popularity)しかなく、これはリーク参照線専用と明示されている。加えて先行測定
(``scripts/cv_rho_probe.py``)で、頭数・市場エントロピー・本命支持のいずれも paired 差との
相関がほぼ厳密にゼロだと分かっている。**共変量は記録専用で判定式には入らない**(FR-014)
以上、リーク面を増やしてまで載せる理由が無い。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from .bootstrap import (
    BootstrapCI,
    inflate_for_seed_noise,
    race_day_cluster_bootstrap_ci_v1,
)

#: 証拠 artifact の形式版。行の意味が変わったら上げる(評価契約版とは独立)。
EVIDENCE_FORMAT_VERSION = "paired-evidence-v1"

#: 差の符号規約。**この向きを変えてはならない** — 逆向きでも CI の幅はもっともらしく見えるので、
#: 再計算の一致だけでは取り違えを検出できない(INV-E4)。
SIGN_CONVENTION = "candidate_minus_active"


class EvidenceContractError(RuntimeError):
    """証拠 artifact が契約を満たさない(fail-closed)。"""


@dataclass(frozen=True)
class PairedEvidenceRow:
    """1 レース 1 行。判定を再現するのに十分な最小単位(data-model.md §1)。"""

    seq: int                     #: 発行順。再計算はこれで並べ直す(浮動小数点の再現)
    race_id: str
    race_day: str                #: ISO 日付。bootstrap のクラスタキー
    candidate_winner_nll: float
    active_winner_nll: float
    diff: float                  #: candidate − active(SIGN_CONVENTION)
    covariates: dict = field(default_factory=dict)  #: 記録専用。判定式には入らない

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PairedEvidenceArtifact:
    """per-race 行 + 行だけでは再現できない判定パラメータ(data-model.md §2)。"""

    rows: tuple[PairedEvidenceRow, ...]
    bootstrap: dict              #: {b, seed, alpha, block}
    seed_noise: dict             #: v4 の seed 成分宣言。空なら合成は恒等(v3 以前互換)
    evaluation_contract_version: str
    gate_config_hash: str
    race_id_set_hash: str
    candidate_recipe_hash: str
    active_recipe_hash: str
    window: dict                 #: {from, to}
    artifact_kind: str = "paired_evidence"
    eligible_for_verdict: bool = False
    sign_convention: str = SIGN_CONVENTION
    format_version: str = EVIDENCE_FORMAT_VERSION

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rows"] = [r.to_dict() for r in self.rows]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> PairedEvidenceArtifact:
        rows = tuple(PairedEvidenceRow(**r) for r in d.get("rows", ()))
        rest = {k: v for k, v in d.items() if k != "rows"}
        return cls(rows=rows, **rest)


def build_rows(
    entries: list[tuple[str, str, float, float]],
    *,
    covariates: dict[str, dict] | None = None,
) -> tuple[PairedEvidenceRow, ...]:
    """``(race_id, race_day, candidate_nll, active_nll)`` の**発行順**列から行を作る。

    差はここでだけ計算する。呼び出し側が別途引き算した値を渡す口を作らない — 二重実装は
    符号規約の取り違えが起きる唯一の場所だからである。
    """
    cov = covariates or {}
    return tuple(
        PairedEvidenceRow(
            seq=i, race_id=rid, race_day=day,
            candidate_winner_nll=float(c), active_winner_nll=float(a),
            diff=float(c) - float(a), covariates=dict(cov.get(rid, {})),
        )
        for i, (rid, day, c, a) in enumerate(entries)
    )


def race_covariates(race_id: str, *, field_size: int, race_day: str) -> dict:
    """証拠行に載せる共変量。**結果を読まない量のみ**(INV-E5)。"""
    return {"field_size": int(field_size), "race_year": int(str(race_day)[:4])}


def assert_contract(artifact: PairedEvidenceArtifact, *, n_races: int | None = None) -> None:
    """証拠が契約を満たすことを fail-closed で検査する(INV-E1..E5)。"""
    rows = artifact.rows
    if artifact.sign_convention != SIGN_CONVENTION:
        raise EvidenceContractError(
            f"sign_convention={artifact.sign_convention!r} は契約外。差は "
            f"{SIGN_CONVENTION!r}(candidate − active)でなければならない(INV-E4)"
        )
    if n_races is not None and len(rows) != n_races:
        raise EvidenceContractError(
            f"証拠の行数 {len(rows)} が verdict の n_races {n_races} と一致しない(INV-E1)"
        )
    seen: set[str] = set()
    for r in rows:
        if r.race_id in seen:
            raise EvidenceContractError(f"race_id {r.race_id!r} が重複している(INV-E2)")
        seen.add(r.race_id)
        if r.diff != r.candidate_winner_nll - r.active_winner_nll:
            raise EvidenceContractError(
                f"race {r.race_id}: diff が candidate − active と厳密一致しない(INV-E3)"
            )
    if sorted(r.seq for r in rows) != list(range(len(rows))):
        raise EvidenceContractError("seq が 0..n-1 の並べ替えになっていない(再現不能)")


def diffs_by_day(rows) -> dict[str, list[float]]:
    """``seq`` 順に並べ直してから開催日でまとめる。

    ファイル上の行順に依存しないので、artifact をソートし直しても再計算が変わらない(INV-E6)。
    """
    out: dict[str, list[float]] = {}
    for r in sorted(rows, key=lambda x: x.seq):
        out.setdefault(r.race_day, []).append(r.diff)
    return out


def recompute(artifact: PairedEvidenceArtifact) -> dict:
    """証拠**だけ**から点推定・sampling CI・total CI を再計算する(INV-A1 / FR-008)。

    モデルにも DB にも触らない。ここが verdict と食い違うなら、再現に必要な何かが artifact に
    載っていない — **要件を緩めるのではなく artifact に足す**のが正しい対処である。
    """
    assert_contract(artifact)
    by_day = diffs_by_day(artifact.rows)
    b = artifact.bootstrap
    sample_ci: BootstrapCI = race_day_cluster_bootstrap_ci_v1(
        by_day,
        b=int(b.get("b", 2000)),
        seed=int(b.get("seed", 20260712)),
        alpha=float(b.get("alpha", 0.05)),
    )
    sn = artifact.seed_noise or {}
    total_ci = inflate_for_seed_noise(
        sample_ci,
        sd_fold=float(sn.get("sd_fold", 0.0) or 0.0),
        n_folds=int(sn.get("n_folds", 1) or 1),
        k_seeds=int(sn.get("k_seeds", 1) or 1),
        alpha=float(b.get("alpha", 0.05)),
    )
    return {
        "point": sample_ci.point,
        "sample_ci": asdict(sample_ci),
        "total_ci": asdict(total_ci),
        "n_races": len(artifact.rows),
        "n_days": sample_ci.n_days,
    }


def write(artifact: PairedEvidenceArtifact, path) -> None:
    """append-only: 既存ファイルを上書きしない(INV-A2)。"""
    import pathlib

    p = pathlib.Path(path)
    if p.exists():
        raise EvidenceContractError(
            f"{p} は既に存在する。証拠は append-only で、再実行は新しいファイルを作る(INV-A2)"
        )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2) + "\n")


def read(path) -> PairedEvidenceArtifact:
    import pathlib

    return PairedEvidenceArtifact.from_dict(json.loads(pathlib.Path(path).read_text()))


def with_rows(artifact: PairedEvidenceArtifact, rows) -> PairedEvidenceArtifact:
    return replace(artifact, rows=tuple(rows))


def _unused(_: Any) -> None:  # pragma: no cover - keeps ``Any`` import honest for typing users
    return None
