"""スクリーニング・ハーネス自体の検出力(MDE)を測る。

背景: 新規特徴軸の screening は 1 年以上「生存ゼロ」である。これには 2 通りの読みがある。

  (i)  本当に情報が尽きた
  (ii) ハーネスが、採用ゲートが扱う効果量の帯(winner NLL で 0.002〜0.005)を
       そもそも検出できない

(ii) を排除しない限り「軸が閉じた」とは言えない。ここでは**効果量が既知の合成信号**を
実レース構造(頭数・p の分布・レース数)に注入し、順列ヌルつきオラクルがそれを
「情報あり」と言えるかを β を振って調べる。検出できる最小の真の効果量 = このハーネスの MDE。

真の効果量は模擬せずに解析で出す(標本ノイズを混ぜない):
  q0 = softmax(log p), q1 = softmax(log p + δ) のとき
  winner NLL の population 改善量 = -mean_r KL(q1_r || q0_r)

使い方: cd training && uv run python ../scripts/screen_power_probe.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from screen_axes import Oracle  # noqa: E402
from screen_track_bias import load_pred, load_race_outcomes, horse_style, q  # noqa: E402

BETAS = [0.05, 0.10, 0.15, 0.25, 0.40]
SIM_SEEDS = [1, 2, 3]


def main() -> None:
    d = load_pred()
    d = d.merge(horse_style(load_race_outcomes()), on=["race_id", "horse_id"], how="left")
    d = d.reset_index(drop=True)

    # 注入先は「実在しうる形」の per-horse 軸(自馬の枠 4 分位 × 自馬の先行度 4 分位)。
    # レース定数だと softmax で消えて検出力の話にならない。
    cells = q(d["draw_rel"], 4, "自枠") + "|" + q(d["own_early"], 4, "自先行")
    codes, uniq = pd.factorize(cells)
    K = len(uniq)
    rng0 = np.random.default_rng(20260825)
    pattern = rng0.normal(size=K)
    pattern -= pattern.mean()
    pattern /= pattern.std()

    rid, _ = pd.factorize(d["race_id"])
    nR = int(rid.max()) + 1
    lp = np.log(d["p"].to_numpy())

    def softmax_by_race(s: np.ndarray) -> np.ndarray:
        m = np.full(nR, -np.inf)
        np.maximum.at(m, rid, s)
        e = np.exp(s - m[rid])
        den = np.zeros(nR)
        np.add.at(den, rid, e)
        return e / den[rid]

    q0 = softmax_by_race(lp)
    print(f"rows={len(d)} races={nR} cells K={K}")
    print("β      真の効果(winner NLL)   検出(順列ヌルを下回った seed 数 /3)   実測Δ の代表値")
    print("-" * 88)

    for beta in BETAS:
        delta = beta * pattern[codes]
        q1 = softmax_by_race(lp + delta)
        kl = np.zeros(nR)
        np.add.at(kl, rid, q1 * (np.log(q1) - np.log(q0)))
        true_effect = -kl.mean()

        hits, reals = 0, []
        for s in SIM_SEEDS:
            rng = np.random.default_rng(s)
            u = rng.random(nR)
            # レース内で q1 の累積分布から勝ち馬を 1 頭引く
            order = np.lexsort((np.arange(len(d)), rid))
            cum = np.zeros(len(d))
            acc = np.zeros(nR)
            for idx in order:
                acc[rid[idx]] += q1[idx]
                cum[idx] = acc[rid[idx]]
            win = np.zeros(len(d), dtype=int)
            prev = np.zeros(nR)
            for idx in order:
                r = rid[idx]
                if prev[r] <= u[r] < cum[idx]:
                    win[idx] = 1
                prev[r] = cum[idx]
            sim = d.copy()
            sim["is_win"] = win
            o = Oracle(sim)
            real, nullmin, info = o.screen(f"  β={beta} seed={s}", cells, n_null=8,
                                           rng=np.random.default_rng(100 + s))
            hits += int(info)
            reals.append(real)
        print(f"β={beta:<5} 真の効果={true_effect:+.6f}   検出={hits}/3   "
              f"実測Δ中央値={np.median(reals):+.6f}\n")


if __name__ == "__main__":
    main()
