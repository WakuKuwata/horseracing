"""δ の出所を実行時に検証する(feature 100 US4 / FR-029a).

`min_effect_delta` は gate-config に凍結された literal で、コードは読むだけである。だから
「``sd_fold`` を変えても δ が動かない」は**すでに自明に真**であり、その回帰テストだけでは
何も守れない。

実際の危険は次の凍結のときに起きる — **人が `sd_fold` を見て δ を決め直す**。それを止めるには、
config に導出の出所を必須で持たせ、その導出が測定ノイズを入力にしていないことまで見るしかない。

導出の正本は ``method="multiple_testing_budget"``: 年間の判定回数 N と許容 net-harm 確率から
δ を決める。入力は運用の外生量だけで、推定量の分散の**内訳**は使わない。
"""

from __future__ import annotations

import json
from pathlib import Path

#: gate-config が δ の出所を指すキー。
DELTA_REF_KEY = "delta_derivation_ref"

#: 受理する導出方法。
REQUIRED_METHOD = "multiple_testing_budget"

#: 導出の入力に現れてはならないキー(測定ノイズ由来の混入を機械的に弾く)。
FORBIDDEN_INPUT_KEYS = ("sd_fold", "seed_noise", "seed_sd", "k_seeds")


class DeltaProvenanceError(RuntimeError):
    """δ の出所が解決できない・測定ノイズ由来である(fail-closed)。"""


def _resolve(ref: str, root: Path | None) -> dict:
    p = Path(ref)
    if not p.is_absolute() and root is not None:
        p = Path(root) / ref
    if not p.is_file():
        raise DeltaProvenanceError(
            f"delta_derivation_ref={ref!r} を解決できない。δ の出所が辿れない config は"
            "受理しない — 次の凍結で測定ノイズから導き直されても検出できないため(FR-029a)"
        )
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise DeltaProvenanceError(f"{ref} を解決できない(JSON として読めない): {e}") from None


def assert_delta_provenance(cfg: dict, *, root: Path | None = None) -> dict:
    """gate-config の δ が正しい導出に紐づいていることを検証する。

    δ を持たない config(δ を使わない判定)は対象外。持つなら出所を必ず要求する。
    """
    if cfg is None or "min_effect_delta" not in cfg:
        return {}
    ref = cfg.get(DELTA_REF_KEY)
    if not ref:
        raise DeltaProvenanceError(
            f"min_effect_delta を持つ gate-config は {DELTA_REF_KEY!r} を必須とする。"
            "δ の値だけでは、それが実務価値から決まったのか測定ノイズから決まったのか"
            "区別できない(FR-029a)"
        )
    d = _resolve(str(ref), root)
    if d.get("method") != REQUIRED_METHOD:
        raise DeltaProvenanceError(
            f"導出の method={d.get('method')!r} は受理しない。δ は {REQUIRED_METHOD!r} "
            "(年間判定回数と許容 net-harm 確率)から導出しなければならない(FR-030)"
        )
    for key in FORBIDDEN_INPUT_KEYS:
        if key in d:
            raise DeltaProvenanceError(
                f"導出が {key!r} を入力に持っている。δ を測定ノイズから決めてはならない — "
                "US3 が sd_fold を動かすと δ がひとりでに動く自己参照になる(FR-029)"
            )
    want, got = d.get("derived_delta"), cfg["min_effect_delta"]
    if want is None or float(want) != float(got):
        raise DeltaProvenanceError(
            f"gate-config の min_effect_delta={got!r} が導出結果 {want!r} と一致しない。"
            "片方だけを手で書き換えると、凍結された根拠と実際に使う閾値が乖離する"
        )
    return d
