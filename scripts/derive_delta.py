"""最小効果 δ を**多重検定予算**から導出する(feature 100 US4 / FR-030)。

現行 δ=0.002 の由来は測定ノイズ `sd_fold=0.001816` そのもので、これは「推定量のノイズ」と
「実務上の最小価値」の取り違えである。US3 が `sd_fold` を動かすと **δ がひとりでに動く**。

代わりに、外生的な量から決める:

    年に N 回判定を回し、通ったものを**全部**採用したとき、
    正味で害になる確率が許容範囲 α_year に収まるように δ を決める。

必要な入力(すべて実行前に凍結する):

  N            年間の判定回数。実績から取る(直近 1 年で約 8 回)
  alpha_year   1 年の運用全体で「正味の害」を出してよい確率
  sd_total     1 判定あたりの total CI の SE(sampling + seed)。実測から取る
  harm_frac    採用候補のうち、真の効果が悪い側にあるものの割合(事前)

導出の骨子
----------
片側判定(CI 上限 < 0 かつ点推定 < −δ)を N 回独立に回すとき、真に有害な候補が誤って通る
確率を 1 回あたり p_fp とすると、1 年で 1 件でも通る確率は 1−(1−p_fp·harm_frac)^N。これを
alpha_year 以下に抑える p_fp を求め、そこから δ を逆算する。

真に有害な候補(効果 0 以上)が「点推定 < −δ かつ CI 上限 < 0」を満たす確率は、最悪ケース
(真の効果がちょうど 0)で

    p_fp = P(Z < −δ/sd_total − z_{1−α/2})

となる(CI 上限 < 0 は点推定 < −z·sd を意味するので、δ と z·sd の厳しい方が効く)。

**この導出は `sd_fold` を入力に取らない。** 使うのは total の SE であって、その内訳ではない。
US3 で seed 成分が縮んでも δ は動かず、動くのは検出力の方である(FR-029)。

使い方:
    python scripts/derive_delta.py --n 8 --alpha-year 0.10 --sd-total 0.00158 --harm-frac 0.5
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import asdict, dataclass

METHOD = "multiple_testing_budget"


@dataclass(frozen=True)
class DeltaDerivation:
    """δ の導出根拠(data-model.md §4)。`sd_fold` を入力に持たないことが要件。"""

    method: str
    judgments_per_year: int
    acceptable_net_harm_prob: float
    sd_total: float
    harm_fraction: float
    ci_alpha: float
    per_judgment_fp_budget: float
    derived_delta: float
    frozen_at: str
    notes: str

    def to_dict(self) -> dict:
        return asdict(self)


def per_judgment_budget(*, n: int, alpha_year: float, harm_frac: float) -> float:
    """1 年で 1 件でも誤採用する確率を alpha_year 以下に抑える、1 判定あたりの偽陽性率。"""
    if not 0 < alpha_year < 1 or n < 1 or not 0 < harm_frac <= 1:
        raise ValueError("n>=1, 0<alpha_year<1, 0<harm_frac<=1 が要る")
    # 1 - (1 - p*harm)^n <= alpha_year
    return (1.0 - (1.0 - alpha_year) ** (1.0 / n)) / harm_frac


def derive_delta(
    *, n: int, alpha_year: float, sd_total: float, harm_frac: float, ci_alpha: float = 0.05
) -> tuple[float, float]:
    """(δ, 1 判定あたりの偽陽性予算) を返す。"""
    budget = per_judgment_budget(n=n, alpha_year=alpha_year, harm_frac=harm_frac)
    nd = statistics.NormalDist()
    z_ci = nd.inv_cdf(1.0 - ci_alpha / 2.0)
    # 真の効果 0 のとき、点推定 < -delta かつ CI 上限 < 0 を満たす確率 = Phi(-max(delta, z*sd)/sd)
    # これを budget 以下にする最小の delta
    z_needed = -nd.inv_cdf(budget)
    delta = max(z_needed * sd_total, 0.0)
    # CI 条件だけで既に budget を満たすなら δ は追加の縛りとして意味を持たない
    if z_ci >= z_needed:
        delta = 0.0
    return delta, budget


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=8, help="年間の判定回数(実績)")
    ap.add_argument("--alpha-year", type=float, default=0.10,
                    help="1 年の運用全体で正味の害を出してよい確率")
    ap.add_argument("--sd-total", type=float, required=True,
                    help="1 判定あたり total CI の SE(sampling+seed の合成・実測)")
    ap.add_argument("--harm-frac", type=float, default=0.5,
                    help="採用候補のうち真に有害なものの割合(事前)")
    ap.add_argument("--ci-alpha", type=float, default=0.05)
    ap.add_argument("--frozen-at", required=True, help="凍結日 YYYY-MM-DD")
    ap.add_argument("--json", dest="json_out", default=None)
    a = ap.parse_args()

    delta, budget = derive_delta(n=a.n, alpha_year=a.alpha_year, sd_total=a.sd_total,
                                 harm_frac=a.harm_frac, ci_alpha=a.ci_alpha)
    d = DeltaDerivation(
        method=METHOD, judgments_per_year=a.n, acceptable_net_harm_prob=a.alpha_year,
        sd_total=a.sd_total, harm_fraction=a.harm_frac, ci_alpha=a.ci_alpha,
        per_judgment_fp_budget=budget, derived_delta=delta, frozen_at=a.frozen_at,
        notes=("δ は sd_fold に依存しない。入力は年間判定回数・許容 net-harm 確率・"
               "total SE・有害割合のみ(FR-029/030)。"),
    )
    print(json.dumps(d.to_dict(), ensure_ascii=False, indent=2))
    if a.json_out:
        with open(a.json_out, "w") as fh:
            json.dump(d.to_dict(), fh, ensure_ascii=False, indent=2)
        print(f"wrote {a.json_out}")


if __name__ == "__main__":
    main()
