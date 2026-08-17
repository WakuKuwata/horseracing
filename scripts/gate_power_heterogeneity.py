"""採用ゲートの検出力 — 効果が期間・部分集団で異質な場合(contract v3 の未検証だった仮定)。

v3 は「未証明の部分集団は採用をブロックしない」に変えた。その判断を支えた検出力計算は
**効果が期間・部分集団に一様**と仮定しており、「全体は改善するが直近レジームだけ悪化する候補」
というシナリオを一度も含んでいなかった。ここではそれを直接測る。

このシナリオが重要なのは、供給元切替(JRA-VAN→netkeiba)により **全体平均が既に存在しない
年に支配されている**ため。2026 は 821 開催日のうち 66 日(8%)しかないので、2026 で +0.010
悪化しても全体平均は +0.0008 しか動かない。つまり全体指標は 2026 の劣化をほぼ隠す。
部分集団ガードはまさにこの穴を塞ぐために置かれている。

母数は 088 の実記録(artifacts/088_paired_report.json):
  全体 26,338 レース / 821 開催日 / SE 0.000863
  2026_only  66 日  CI 半幅 0.0071  margin 0.005 (レース単位 winner NLL)
  nk         92 日  CI 半幅 0.00076 margin 0.001 (馬単位 logloss)
  2026_nk    66 日  CI 半幅 0.00086 margin 0.001 (馬単位 logloss)

使い方:
    cd training && uv run python ../scripts/gate_power_heterogeneity.py
"""

from __future__ import annotations

import numpy as np

N_REP = 40000
SEED = 20260817

# --- 088 の実記録から ---------------------------------------------------------------------
SE_ALL = (0.0022141502215346632 + 0.001170555818467798) / 2 / 1.96  # 0.000863
D_ALL, D_3Y, D_5Y = 821, 324, 540
D_2026 = 66
SE_2026 = (0.008261 + 0.006027) / 2 / 1.96
SE_NK = (0.00101 + 0.000504) / 2 / 1.96
SE_2026NK = (0.001108 + 0.000614) / 2 / 1.96
M_RACE, M_HORSE = 0.005, 0.001
#: 馬単位の差は race 単位の約 1/10(088 実測: canonical −0.000057 vs race −0.00055)
HORSE_SCALE = 0.1

SD_DAY = SE_ALL * np.sqrt(D_ALL)          # 非2026 日の日次ばらつき
SD_DAY_2026 = SE_2026 * np.sqrt(D_2026)   # 2026 日は 1 日あたりのレースが少なくばらつきが大きい


def simulate(delta_rest: float, harm_2026: float, *, min_effect_delta: float = 0.0, rng=None):
    """全体が delta_rest、2026 だけ harm_2026 の真の効果を持つ候補を N_REP 回評価する。

    符号は candidate − active(負が候補有利)。返り値は各ゲートの通過率。
    """
    rng = rng or np.random.default_rng(SEED)
    n_rest = D_ALL - D_2026

    days = np.empty((N_REP, D_ALL))
    days[:, :n_rest] = rng.normal(delta_rest, SD_DAY, size=(N_REP, n_rest))
    days[:, n_rest:] = rng.normal(harm_2026, SD_DAY_2026, size=(N_REP, D_2026))

    all_ = days.mean(1)
    se_all = days.std(1, ddof=1) / np.sqrt(D_ALL)
    r3, r5 = days[:, -D_3Y:].mean(1), days[:, -D_5Y:].mean(1)
    se3 = days[:, -D_3Y:].std(1, ddof=1) / np.sqrt(D_3Y)
    se5 = days[:, -D_5Y:].std(1, ddof=1) / np.sqrt(D_5Y)
    y26 = days[:, -D_2026:].mean(1)
    se26 = days[:, -D_2026:].std(1, ddof=1) / np.sqrt(D_2026)

    # 馬単位の部分集団は別グレイン。真の効果は 2026 の劣化に比例させる(nk は大半が 2026)。
    dh = harm_2026 * HORSE_SCALE
    nk = rng.normal(dh, SE_NK, N_REP)
    nk26 = rng.normal(dh, SE_2026NK, N_REP)

    # --- 主ゲート(top2/top3/ECE は 088 で通過しており n が桁違いなので通過扱い) ---------
    primary = all_ < -min_effect_delta
    stat = all_ + 1.96 * se_all < 0

    # v2: 直近窓は点推定の符号テスト / 部分集団は「全部 PASS」
    recent_v2 = (r3 <= 0) & (r5 <= 0)
    sg_v2 = (
        (y26 + 1.96 * se26 < M_RACE)
        & (nk + 1.96 * SE_NK < M_HORSE)
        & (nk26 + 1.96 * SE_2026NK < M_HORSE)
    )
    adopt_v2 = primary & stat & recent_v2 & sg_v2

    # v3: 直近窓も部分集団も「自信を持って margin より悪い」ときだけ veto
    recent_v3 = (r3 - 1.96 * se3 <= M_RACE) & (r5 - 1.96 * se5 <= M_RACE)
    sg_fail = (
        (y26 - 1.96 * se26 > M_RACE)
        | (nk - 1.96 * SE_NK > M_HORSE)
        | (nk26 - 1.96 * SE_2026NK > M_HORSE)
    )
    adopt_v3 = primary & stat & recent_v3 & ~sg_fail

    return {
        "true_overall": (delta_rest * n_rest + harm_2026 * D_2026) / D_ALL,
        "adopt_v2": adopt_v2.mean(),
        "adopt_v3": adopt_v3.mean(),
        "sg_fail_detected": sg_fail.mean(),       # ガードが害を検出した率
        "sg_full_assurance": sg_v2.mean(),        # 全部 PASS(= v3 の assurance=full)
        "primary_and_stat": (primary & stat).mean(),
    }


def main() -> None:
    rng = np.random.default_rng(SEED)

    print("=" * 96)
    print("シナリオ: 候補は 2026 以外で delta_rest だけ改善し、2026 でだけ harm_2026 だけ悪化する")
    print("(2026 は 821 開催日中 66 日 = 8%。全体平均はこの劣化をほとんど隠す)")
    print("=" * 96)
    print(f"{'2026以外':>9}{'2026の害':>10}{'真の全体':>10}{'主+CI':>8}"
          f"{'v2採用':>8}{'v3採用':>8}{'害の検出':>9}{'全PASS':>8}")
    for delta_rest in (-0.005, -0.003, -0.001):
        for harm in (0.0, 0.002, 0.005, 0.010, 0.020, 0.040):
            r = simulate(delta_rest, harm, rng=rng)
            print(f"{delta_rest:>9.4f}{harm:>10.3f}{r['true_overall']:>10.5f}"
                  f"{r['primary_and_stat']:>8.3f}{r['adopt_v2']:>8.3f}{r['adopt_v3']:>8.3f}"
                  f"{r['sg_fail_detected']:>9.3f}{r['sg_full_assurance']:>8.3f}")
        print("-" * 96)

    print("\n【1】ガードが 2026 の害を検出する力(v3 の veto はこれだけが頼り)")
    print(f"{'2026の害':>10}{'FAIL 検出率':>14}")
    for harm in (0.002, 0.005, 0.008, 0.010, 0.015, 0.020, 0.030, 0.040):
        r = simulate(-0.003, harm, rng=rng)
        print(f"{harm:>10.3f}{r['sg_fail_detected']:>14.3f}")

    print("\n【2】min_effect_delta=0.002 を課した場合(091 の設定)")
    print(f"{'2026の害':>10}{'v2採用':>10}{'v3採用':>10}")
    for harm in (0.0, 0.005, 0.010, 0.020):
        r = simulate(-0.005, harm, min_effect_delta=0.002, rng=rng)
        print(f"{harm:>10.3f}{r['adopt_v2']:>10.3f}{r['adopt_v3']:>10.3f}")

    print("\n【3】採用されたもののうち有害なものの割合(事前確率を仮定)")
    print("  仮定: 全体を改善する候補のうち p_harm の割合が 2026 で +0.010 悪化している")
    good = simulate(-0.003, -0.003, rng=rng)   # 一様に改善する候補
    bad = simulate(-0.003, 0.010, rng=rng)     # 2026 だけ悪化する候補
    print(f"  一様改善(-0.003)     : v2 採用 {good['adopt_v2']:.3f} / "
          f"v3 採用 {good['adopt_v3']:.3f}")
    print(f"  2026 のみ +0.010 悪化 : v2 採用 {bad['adopt_v2']:.3f} / "
          f"v3 採用 {bad['adopt_v3']:.3f}")

    def harmful_share(p_harm: float, key: str) -> float:
        num = p_harm * bad[key]
        den = num + (1 - p_harm) * good[key]
        return num / den if den > 0 else float("nan")

    print(f"{'p_harm':>8}{'v2 の有害採用率':>18}{'v3 の有害採用率':>18}")
    for p in (0.1, 0.2, 0.5):
        print(f"{p:>8.1f}{harmful_share(p, 'adopt_v2'):>18.3f}"
              f"{harmful_share(p, 'adopt_v3'):>18.3f}")

    print("\n【4】v2 の『有害採用ゼロ』は安全性ではなく不採用率の系である")
    print(f"  v2 は一様に -0.003 改善する候補ですら {good['adopt_v2']:.3f} しか採用しない。")
    print("  有害候補を 0.000 で止めているのは、良い候補も同じ機構で止めているから。")


if __name__ == "__main__":
    main()
