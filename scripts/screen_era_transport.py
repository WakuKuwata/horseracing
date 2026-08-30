"""年代移送: 追加レース数を揃えて「古さによる価値低下率 ρ」を測る。

なぜこの形か
------------
`screen_data_scaling.py` が **同分布でレース数を 2 倍にした場合の楽観上限 −0.0143(全域)
〜 −0.0101(限界区間)** を出した。ディスクには 1986-2006 の 905,152 行(≒72,000 レース)が
2007+ と同一の 73 列 CSV で眠っている。残る問いは **古いレースが現代のレースの何割の価値を
持つか(ρ)** だけ。採用バー −0.0031 に対して必要な ρ は 0.22〜0.35。

学習窓の開始年をずらす ablation は **量と年代を同時に動かす**ので交絡する。ここでは
**追加レース数を厳密に揃えて年代だけを変える**。

さらに **同年代の対照アームを実験の中に置く**(codex 設計の改良)。scaling screen で測った
傾き定数を外から持ち込むと、その定数の誤差が ρ にそのまま乗る。core を 2017-2024 の半分にし、
**残り半分を足すアーム**を基準にすれば ρ は実験内で完結する(定義上 ρ=1)。

アーム(2025 年より前のプールで定義。2025+ の学習行は全アームに共通で入る)
--------------------------------------------------------------------------
  A core      : 2017-2024 を年別層化で半分
  S +同年代    : core + 2017-2024 の**残り半分**          ← ρ=1 の基準
  M +2012-2016 : core + 2012-2016 から同数            ← 9〜14 年前
  O +2007-2011 : core + 2007-2011 から同数            ← 14〜19 年前

判定規則(実行前に固定)
----------------------
  ρ_M = Δ_M/Δ_S, ρ_O = Δ_O/Δ_S  (Δ は winner NLL の差。負が改善)
  Δ_S > -0.002 なら **NO_DECISION**(基準アームが登録しないので ρ が定義できない)
  r = ρ_O/ρ_M を 5 年 1 段の減衰率とし、1996-2006 を 2 段先として ρ̂ = clip(ρ_O · r², 0, 1)
  予測 = ρ̂ × 0.0206 × ln((65872+38000)/65872) ≒ ρ̂ × 0.00938
    (0.0206 = scaling screen の全域傾き。**凍結定数**として引用する)

  ρ_M <= 0.15        → 9〜14 年前ですら移送しない。**pre-2007 路線を閉じる**
  予測 <= -0.0031    → 1996-2006 の最小 canary(実取込)へ進む
  その間             → 曖昧

**SCREENING ONLY — can_adopt=false。**

    cd training && uv run python ../scripts/screen_era_transport.py
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import time

from horseracing_db.session import create_db_engine
from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1
from horseracing_eval.dataset import load_eval_races, population_masks
from horseracing_eval.foldfit import predict_over_folds
from sqlalchemy.orm import Session

from horseracing_training.calib_split import CalibSplitFactory
from horseracing_training.recipe import ModelRecipe

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
SALT = "screen_era_transport/v1"
#: scaling screen(2026-08-27)の全域傾き。凍結定数として引用する。
B_GLOBAL = 0.020618
#: 現行の学習レース数と、1986-2006 を足したときの増分レース数(実測)。
N_NOW, N_ADD_9606 = 65872, 38000


def stable_u(rid: str) -> float:
    return int.from_bytes(hashlib.sha1(f"{SALT}|{rid}".encode()).digest()[:8], "big") / 2**64


def stratified_take(by_year: dict[int, list[str]], n: int) -> set[str]:
    """年別に比例配分して決定論的に n 件取る(年代構成を保つ)。"""
    total = sum(len(v) for v in by_year.values())
    out: set[str] = set()
    for _y, ids in sorted(by_year.items()):
        k = int(round(n * len(ids) / total))
        out.update(sorted(ids, key=stable_u)[:k])
    return out


class EraFactory(CalibSplitFactory):
    """train_races を `keep` ∪ (最近の共通行) に絞る。"""

    keep: set[str] = frozenset()
    always_from: datetime.date = datetime.date(2025, 1, 1)

    def fit(self, train_races, *, num_threads=None):
        sel = [r for r in train_races
               if r.race_id in self.keep or r.race_date >= self.always_from]
        if not sel:
            raise RuntimeError("学習レースが 0 になった")
        self.last_n = len(sel)
        return super().fit(sel, num_threads=num_threads)


def diffs_by_day(valid, pa, pb) -> dict:
    out: dict[str, list[float]] = {}
    for er in valid:
        pop = population_masks(er)
        if not pop.eligible:
            continue
        a, b = pa[er.context.race_id].get(pop.winner_horse_id), \
            pb[er.context.race_id].get(pop.winner_horse_id)
        if a is None or b is None:
            continue
        f = lambda p: -math.log(min(max(float(p), 1e-15), 1 - 1e-15))  # noqa: E731
        out.setdefault(er.context.race_date.isoformat(), []).append(f(a.win) - f(b.win))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=900)
    ap.add_argument("--n-oof-blocks", type=int, default=3)
    ap.add_argument("--first-valid-year", type=int, default=2025)
    ap.add_argument("--to", default="2026-08-16")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--num-threads", type=int, default=8)
    ap.add_argument("--bootstrap-b", type=int, default=1000)
    ap.add_argument("--materialized-path", default="../artifacts/features.parquet")
    ap.add_argument("--json", dest="json_out", default="../out/era_transport.json")
    args = ap.parse_args()

    print("*** SCREENING ONLY — can_adopt=false ***")
    print(__doc__.split("判定規則(実行前に固定)")[1].split("**SCREENING")[0].strip(" -\n"))
    print()

    engine = create_db_engine(DB)
    with Session(engine) as session:
        races = load_eval_races(session, end_date=datetime.date.fromisoformat(args.to))
        pool: dict[str, dict[int, list[str]]] = {"core": {}, "mid": {}, "old": {},
                                                 "canary": {}}
        for er in races:
            y = er.context.race_date.year
            if 2017 <= y <= 2024:
                pool["core"].setdefault(y, []).append(er.context.race_id)
            elif 2012 <= y <= 2016:
                pool["mid"].setdefault(y, []).append(er.context.race_id)
            elif 2007 <= y <= 2011:
                pool["old"].setdefault(y, []).append(er.context.race_id)
            elif 2002 <= y <= 2006:
                pool["canary"].setdefault(y, []).append(er.context.race_id)

        core = stratified_take(pool["core"], sum(len(v) for v in pool["core"].values()) // 2)
        rest = {y: [r for r in ids if r not in core] for y, ids in pool["core"].items()}
        n_add = sum(len(v) for v in rest.values())
        arms = {
            "A core": set(core),
            "S +同年代": core | stratified_take(rest, n_add),
            "M +2012-2016": core | stratified_take(pool["mid"], n_add),
            "O +2007-2011": core | stratified_take(pool["old"], n_add),
        }
        if pool["canary"]:
            n_can = sum(len(v) for v in pool["canary"].values())
            if n_can < n_add:
                raise RuntimeError(f"2002-2006 が {n_can:,} レースしかなく "
                                   f"追加ブロック {n_add:,} に足りない")
            arms["C +2002-2006"] = core | stratified_take(pool["canary"], n_add)
            print("  (2002-2006 が DB にあるので canary アームを追加した)")
        print(f"core={len(core):,} レース  追加ブロック={n_add:,} レース(全アーム共通の大きさ)")
        for k, v in arms.items():
            print(f"  {k:<14} プール {len(v):,}")
        sizes = {len(v) for k, v in arms.items() if k != "A core"}
        if max(sizes) - min(sizes) > 0.01 * len(core):
            raise RuntimeError(f"追加ブロックの大きさが揃っていない: {sizes}")
        print()

        preds, valid, ntrain = {}, None, {}
        for name, keep in arms.items():
            recipe = ModelRecipe(objective="pl_topk", calibration="none", calib_frac=0.0,
                                 seed=args.seed, params=(("n_estimators", args.rounds),),
                                 label=f"era-transport:{name}")
            fac = EraFactory(session, recipe, n_oof_blocks=args.n_oof_blocks,
                             method="isotonic", require_sufficient=False,
                             use_materialized=True,
                             materialized_path=args.materialized_path, pin_snapshot=True)
            fac.keep = keep
            t0 = time.time()
            p, v = predict_over_folds(fac, races, first_valid_year=args.first_valid_year,
                                      num_threads=args.num_threads)
            preds[name], valid, ntrain[name] = p, v, fac.last_n
            print(f"  {name:<14} {time.time()-t0:7.1f}s  train={fac.last_n:,} races "
                  f"(最終 fold)  valid={len(v):,}")

    base = preds["A core"]
    out = {}
    print()
    for name in [k for k in arms if k != "A core"]:
        nd = sum(1 for er in valid for hid, q in base[er.context.race_id].items()
                 if preds[name][er.context.race_id].get(hid) is not None
                 and preds[name][er.context.race_id][hid].win != q.win)
        if nd == 0:
            raise RuntimeError(f"{name} の予測が core と完全一致(097 型の縮退)")
        ci = race_day_cluster_bootstrap_ci_v1(diffs_by_day(valid, preds[name], base),
                                              b=args.bootstrap_b, seed=20260827)
        out[name] = {"diff": ci.point, "ci_low": ci.ci_low, "ci_high": ci.ci_high,
                     "n_train": ntrain[name], "n_differing": nd}
        print(f"  {name:<14} Δ vs core = {ci.point:+.6f}  "
              f"CI[{ci.ci_low:+.6f}, {ci.ci_high:+.6f}]")

    dS = out["S +同年代"]["diff"]
    dM, dO = out["M +2012-2016"]["diff"], out["O +2007-2011"]["diff"]
    print("\n=== 移送率 ===")
    if dS > -0.002:
        verdict, pred = "no_decision", None
        print(f"  基準アーム(同年代)が Δ={dS:+.6f} で登録しない → **NO_DECISION**")
        print("  (ρ の分母が立たないので年代移送を測れない)")
    else:
        rM, rO = dM / dS, dO / dS
        r_raw = rO / rM if abs(rM) > 1e-9 else float("nan")
        # **古くなって価値が上がることは仮定しない**。r_raw>1 は減衰が識別できていない印なので、
        # 外挿せず min(ρ_M, ρ_O) をフラットな(楽観側の)上限として使う。
        decay_identified = math.isfinite(r_raw) and r_raw <= 1.0
        if decay_identified:
            rho_hat = min(max(rO * r_raw * r_raw, 0.0), 1.0)
        else:
            rho_hat = min(max(min(rM, rO), 0.0), 1.0)
        pred = rho_hat * B_GLOBAL * math.log((N_NOW + N_ADD_9606) / N_NOW)
        print(f"  ρ(2012-2016) = {rM:+.3f}   ρ(2007-2011) = {rO:+.3f}   "
              f"5年1段の減衰 r = {r_raw:+.3f}")
        if not decay_identified:
            print("  ! r>1 = 減衰が識別されていない(古いほど良いという外挿はしない)。"
                  "min(ρ_M, ρ_O) をフラットな上限として使う")
        print(f"  外挿 ρ̂(1996-2006) = {rho_hat:.3f}  → 予測 {-pred:+.6f}")
        if "C +2002-2006" in out:
            dC = out["C +2002-2006"]["diff"]
            rC = dC / dS
            print(f"\n  ★ 実測 ρ(2002-2006) = {rC:+.3f}   "
                  f"(外挿の 3 段目に相当。外挿値 {rO * r_raw:+.3f} と比べる)")
            c_sig = out["C +2002-2006"]["ci_high"] < 0
            print(f"     Δ_C = {dC:+.6f}  CI[{out['C +2002-2006']['ci_low']:+.6f}, "
                  f"{out['C +2002-2006']['ci_high']:+.6f}]  有意={c_sig}")
            print("     → **これが canary の答え**。regime break(クラス表記・距離体系・馬場・"
                  "血統プールの断絶)が移送を壊すかは、外挿ではなくこの実測で判断する")
        # 比の分子が個別にゼロと区別できないなら、比に意味は無い
        o_significant = out["O +2007-2011"]["ci_high"] < 0
        if rM <= 0.15:
            verdict = "close"
            print("  → 9〜14 年前ですら移送しない。**pre-2007 路線を閉じる**")
        elif -pred <= -0.0031 and o_significant:
            verdict = "go_canary" if decay_identified else "go_canary_decay_unidentified"
            print("  → 1996-2006 の最小 canary(実取込)へ進む")
        else:
            verdict = "ambiguous"
            if -pred <= -0.0031 and not o_significant:
                print("  → 曖昧: 予測はバーを越えるが、**Δ_O 自体がゼロと区別できない**")
                print("     (有意でない量の比に基づいて数日がかりの取込へ進むことはしない)")
            else:
                print("  → 曖昧")
    print("  注: 5 年 2 段の外挿であって、20〜30 年前への保証ではない。"
          "クラス表記・距離体系・馬場の変化は線形の『古さ』では表せない")

    with open(args.json_out, "w") as fh:
        json.dump({"screening_only": True, "can_adopt": False, "arms": out,
                   "n_core": len(core), "n_add_block": n_add, "b_global_frozen": B_GLOBAL,
                   "rounds": args.rounds, "seed": args.seed,
                   "predicted_9606": (-pred if pred is not None else None),
                   "verdict": verdict}, fh, indent=2)
    print(f"  wrote {args.json_out}")


if __name__ == "__main__":
    main()
