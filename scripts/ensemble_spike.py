"""US3(k-seed アンサンブル)の足切りスパイク(feature 100 Phase C・中断点)。

**足切り値は `specs/100-eval-contract-v5/spike-config.json` に実行前から凍結してある。**
本スクリプトはそれを読み、hash を照合してから走る。結果を見てから閾値を動かすことは禁止。

測る 2 つ
---------
primary  winner NLL の改善幅 = mean(バンドルの NLL) − mean(seed 単体の NLL)。負が改善。
         これだけが足切りに使われる。
secondary バンドル間 sd と seed 間 sd の比。**報告のみで足切りに使わない**(R16 により
         「アンサンブルは CI を狭める」という測定上の正当化は既に大きく減価しているので、
         弱いと分かっている主張で US3 を殺さない)。`sd/√k` との乖離も併記する。

設計
----
9 個の seed を 3 つの互いに素なバンドルに分ける。**同じ fit 群から** seed 単体の NLL 9 個と
バンドル(k=3)の NLL 3 個の両方が出るので、単一 seed の分散を k で割る近道(FR-026b が禁じた
`sd/√k`)を使わずに済む。

合成は**レース内 softmax 後の確率の平均**(D3/R14)。arm E の校正器は raw race-softmax の
確率ベクトルを受け取るので、平均した `p̄` をそのまま渡せばよい。**isotonic は入力の順序に
しか依存しない**ので、`p̄` に当てるのと `log p̄`(FR-022a)に当てるのは同値である。
校正器は**アンサンブルの OOF で再 fit** する(単一 seed 版の流用は禁止・D5)。

コスト
------
arm E は 1 outer fold あたり base 1 + OOF ブロック `n_oof_blocks` 個の booster fit を要する。
k-seed ではその **seed 数倍**になる。`--cost-probe` で 1 fit の実測時間から総所要を見積もれる。
見積もりなしで本走を始めないこと。

    # 単位コストだけ測る(効果量は出さない)
    cd training && uv run python ../scripts/ensemble_spike.py --cost-probe --to 2024-12-31

    # 本走
    cd training && uv run python ../scripts/ensemble_spike.py --from 2025-01-01 --to 2025-12-31
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import time

import numpy as np


def _d(s: str):
    import datetime
    return datetime.date.fromisoformat(s)

SPEC_DIR = pathlib.Path(__file__).resolve().parents[1] / "specs" / "100-eval-contract-v5"
DB = os.environ.get("DATABASE_URL",
                    "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")


def load_frozen() -> dict:
    """凍結された足切りを読み、hash を照合する(fail-closed)。"""
    from horseracing_eval.decision import gate_config_hash

    cfg = json.loads((SPEC_DIR / "spike-config.json").read_text())
    want = (SPEC_DIR / "spike-config.hash.txt").read_text().strip()
    got = gate_config_hash(cfg)
    if got != want:
        raise SystemExit(
            f"spike-config が凍結後に変更されている: {got} != {want}。"
            "足切りは実行前に凍結したものでなければならない(FR-016)"
        )
    return cfg


def _session():
    from horseracing_db.session import create_db_engine
    from sqlalchemy.orm import Session

    return Session(create_db_engine(DB))


def _factory(session, seed: int, cfg: dict, *, rounds: int | None = None,
             n_oof: int | None = None, materialized: str | None = None):
    """凍結された run 条件 + 指定 seed の arm E ファクトリ。"""
    rc = cfg["run_conditions"]
    from horseracing_training.cli import _factory_from_spec

    return _factory_from_spec(
        session, f"{rc['objective']}:{rc['arm']}",
        use_materialized=materialized is not None, materialized_path=materialized,
        arm_overrides={
            "seed": seed,
            "n_estimators": rounds if rounds is not None else rc["n_estimators"],
            "n_oof_blocks": n_oof if n_oof is not None else rc["n_oof_blocks"],
            "weight_mask_rate": rc["weight_mask_rate"],
            "weight_mask_seed": rc["weight_mask_seed"],
        },
    )


def cost_probe(args, cfg: dict) -> None:
    """booster 1 fit の実測時間から総所要を見積もる。**効果量は一切出さない。**"""
    from horseracing_eval.dataset import load_eval_races

    rc = cfg["run_conditions"]
    n_seeds, n_oof = len(cfg["seeds"]), rc["n_oof_blocks"]
    with _session() as s:
        t0 = time.time()
        races = load_eval_races(s, start_date=_d(args.from_), end_date=_d(args.to))
        t_load = time.time() - t0
        train = [er.context for er in races]
        print(f"レース {len(races)} 件 / ロード {t_load:.1f}s")

        from horseracing_training.predictor import LightGBMPredictor

        print(f"recipe: objective={rc['objective']} arm={rc['arm']} "
              f"rounds={rc['n_estimators']} n_oof_blocks={n_oof} seeds={n_seeds}")
        t0 = time.time()
        base = LightGBMPredictor(
            s, objective=rc["objective"], calibration="none",
            use_materialized=args.materialized_path is not None,
            materialized_path=args.materialized_path,
            params={"n_estimators": int(rc["n_estimators"])},
            seed=int(cfg["seeds"][0]),
        )
        base.fit(train)
        t_fit = time.time() - t0

    per_fold = n_seeds * (1 + n_oof)
    print(f"\nbooster 1 fit = {t_fit:.1f}s(このデータ量で)")
    print(f"1 outer fold あたりの fit 数 = seeds {n_seeds} × (base 1 + OOF {n_oof}) = {per_fold}")
    print(f"→ 1 outer fold の見積もり ≈ {per_fold * t_fit / 3600:.1f} 時間")
    print("\n**効果量は出していない**(cost-probe は所要時間だけを見る)")
    _ = base


def _ensemble_raw(preds, race):
    """レース内 softmax 後の確率を member 間で平均する(D3/R14)。Σ=1 が保たれる。"""
    ids0, acc = None, None
    for p in preds:
        ids, raw = p.raw_win_probs(race)
        arr = np.asarray(raw, dtype=float)
        if ids0 is None:
            ids0, acc = list(ids), arr.copy()
        else:
            if list(ids) != ids0:
                raise RuntimeError(f"member 間で started 集合が違う: {race.race_id}")
            acc = acc + arr
    return ids0, acc / float(len(preds))


def _make_base(session, cfg, seed, shared, materialized):
    """凍結 run 条件 + 指定 seed の base booster(arm E の `_make_base` と同一構成)。"""
    from horseracing_training.predictor import LightGBMPredictor
    from horseracing_training.recipe import ModelRecipe

    rc = cfg["run_conditions"]
    recipe = ModelRecipe(
        objective=rc["objective"], calibration="none", calib_frac=0.0,
        weight_mask_rate=rc["weight_mask_rate"], weight_mask_seed=rc["weight_mask_seed"],
        params=(("n_estimators", int(rc["n_estimators"])),), seed=int(seed),
    )
    p = LightGBMPredictor(
        session, objective=recipe.objective, calibration="none", calib_frac=0.0,
        target_encode_cols=recipe.target_encode_cols, te_smoothing=recipe.te_smoothing,
        seed=recipe.seed, drop_features=recipe.drop_features,
        fit_weight_mask=recipe.weight_mask_spec(), params=recipe.resolved_params(),
        use_materialized=materialized is not None, materialized_path=materialized,
    )
    if shared is not None:
        p._data = p._scope_columns(shared)   # noqa: SLF001 - arm E と同じ scope 適用
    return p


def _winner_nll(score_races, raw_by_race, calibrator, clip=1e-15):
    """校正 → レース内正規化 → 勝ち馬の −log p。1 レース 1 標本。"""
    from horseracing_eval.dataset import population_masks
    from horseracing_training.calib_split import assemble_predictions

    vals = []
    for er in score_races:
        pop = population_masks(er)
        if not pop.eligible or pop.winner_horse_id is None:
            continue
        got = raw_by_race.get(er.context.race_id)
        if got is None:
            continue
        ids, raw = got
        arr = np.asarray(raw, dtype=float)
        s = np.asarray(calibrator.transform(arr), dtype=float) if calibrator is not None else arr
        pred = assemble_predictions(list(ids), s)
        w = pred.get(pop.winner_horse_id)
        if w is None:
            continue
        vals.append(-float(np.log(min(max(w.win, clip), 1.0 - clip))))
    return (sum(vals) / len(vals)) if vals else float("nan")


def run(args, cfg) -> None:
    from horseracing_eval.dataset import load_eval_races
    from horseracing_training.calibration import DEFAULT_CLIP, fit_calibrator
    from horseracing_training.calib_split import _started_all_outcomes, day_block_partition
    from horseracing_training.predictor import LightGBMPredictor

    rc, seeds, bundles = cfg["run_conditions"], cfg["seeds"], cfg["bundles"]
    n_oof = int(rc["n_oof_blocks"])
    start, end = _d(args.from_), _d(args.to)
    t_start = time.time()

    with _session() as s:
        races = load_eval_races(s, end_date=end)
        train = [er.context for er in races if er.context.race_date < start]
        score = [er for er in races if start <= er.context.race_date <= end]
        print(f"学習 {len(train)} レース(〜{start})/ 採点 {len(score)} レース({start}..{end})")
        if not train or not score:
            raise SystemExit("学習側か採点側が空")

        tmp = LightGBMPredictor(
            s, objective=rc["objective"], calibration="none",
            use_materialized=args.materialized_path is not None,
            materialized_path=args.materialized_path,
        )
        shared = tmp._ensure_data()   # noqa: SLF001 - 全 fit で 1 回だけ構築して共有する

        # --- 1) seed ごとの base booster -> 採点レースの raw -------------------------------
        raw_scored: dict[int, dict] = {}
        for i, seed in enumerate(seeds, 1):
            t0 = time.time()
            b = _make_base(s, cfg, seed, shared, args.materialized_path)
            b.fit(train)
            raw_scored[seed] = {er.context.race_id: b.raw_win_probs(er.context) for er in score}
            print(f"  base seed={seed} ({i}/{len(seeds)}) {time.time()-t0:.0f}s", flush=True)

        # --- 2) OOF ブロック: ブロック x seed の raw(校正サンプルの素)---------------------
        days = sorted({r.race_date for r in train})
        outcomes = _started_all_outcomes(s, [r.race_id for r in train])
        oof: dict[int, list] = {seed: [] for seed in seeds}   # seed -> [(ids, raw, winners)]
        blocks = list(day_block_partition(days, n_oof))
        for bi, (earlier_days, block_days) in enumerate(blocks, 1):
            eset, bset = set(earlier_days), set(block_days)
            earlier = [r for r in train if r.race_date in eset]
            block = [r for r in train if r.race_date in bset]
            if not earlier or not block:
                continue
            eligible = []
            for ctx in block:
                got = outcomes.get(ctx.race_id)
                if got is None:
                    continue
                n_rows, winners = got
                started = [h.horse_id for h in ctx.started_horses]
                if not started or n_rows < len(started) or not winners:
                    continue
                eligible.append((ctx, started, winners))
            if not eligible:
                continue
            for seed in seeds:
                t0 = time.time()
                b = _make_base(s, cfg, seed, shared, args.materialized_path)
                b.fit(earlier)
                for ctx, started, winners in eligible:
                    ids, raw = b.raw_win_probs(ctx)
                    if list(ids) != started:
                        raise RuntimeError(f"started 不一致 {ctx.race_id}")
                    oof[seed].append((list(ids), np.asarray(raw, dtype=float), winners))
                print(f"  oof block {bi}/{len(blocks)} seed={seed} {time.time()-t0:.0f}s",
                      flush=True)

    # --- 3) 校正器を fit(seed 単体 / バンドル)-------------------------------------------
    def _fit(rows_scores, rows_labels):
        return fit_calibrator(np.asarray(rows_scores, dtype=float),
                              np.asarray(rows_labels, dtype=int),
                              method="isotonic", clip=DEFAULT_CLIP)

    seed_nll: dict[int, float] = {}
    for seed in seeds:
        sc, lb = [], []
        for ids, raw, winners in oof[seed]:
            for hid, v in zip(ids, raw, strict=True):
                sc.append(float(v))
                lb.append(1 if hid in winners else 0)
        seed_nll[seed] = _winner_nll(score, raw_scored[seed], _fit(sc, lb))
        print(f"  seed {seed}: winner NLL = {seed_nll[seed]:.6f}", flush=True)

    bundle_nll: list[float] = []
    for bundle in bundles:
        sc, lb = [], []
        n_rows = len(oof[bundle[0]])
        for j in range(n_rows):
            ids, _, winners = oof[bundle[0]][j]
            acc = np.zeros(len(ids), dtype=float)
            for seed in bundle:
                ids_s, raw_s, _ = oof[seed][j]
                if ids_s != ids:
                    raise RuntimeError("member 間で OOF 行の並びが違う")
                acc += raw_s
            pbar = acc / float(len(bundle))
            for hid, v in zip(ids, pbar, strict=True):
                sc.append(float(v))
                lb.append(1 if hid in winners else 0)
        cal = _fit(sc, lb)
        ens_raw = {}
        for rid in raw_scored[bundle[0]]:
            ids0 = list(raw_scored[bundle[0]][rid][0])
            acc = np.zeros(len(ids0), dtype=float)
            for seed in bundle:
                ids_s, raw_s = raw_scored[seed][rid]
                if list(ids_s) != ids0:
                    raise RuntimeError(f"member 間で started 集合が違う: {rid}")
                acc += np.asarray(raw_s, dtype=float)
            ens_raw[rid] = (ids0, acc / float(len(bundle)))
        nll = _winner_nll(score, ens_raw, cal)
        bundle_nll.append(nll)
        print(f"  bundle {bundle}: winner NLL = {nll:.6f}", flush=True)

    # --- 4) 足切り判定に使う 2 数値だけを出す(出力規律)------------------------------------
    improvement = statistics.fmean(bundle_nll) - statistics.fmean(seed_nll.values())
    sd_seed = statistics.stdev(seed_nll.values())
    sd_bundle = statistics.stdev(bundle_nll)
    k = len(bundles[0])
    out = {
        "artifact_kind": "spike", "eligible_for_verdict": False,
        "spike_config_hash": (SPEC_DIR / "spike-config.hash.txt").read_text().strip(),
        "window": {"from": args.from_, "to": args.to},
        "n_train_races": len(train), "n_score_races": len(score),
        "elapsed_s": round(time.time() - t_start, 1),
        "primary": {
            "metric": "winner_nll_improvement",
            "value": improvement,
            "threshold": cfg["primary_cutoff"]["threshold"],
            "passes": improvement <= cfg["primary_cutoff"]["threshold"],
        },
        "secondary_reported_not_gating": {
            "sd_seed": sd_seed, "sd_bundle": sd_bundle,
            "ratio_bundle_over_seed": (sd_bundle / sd_seed) if sd_seed else None,
            "sqrt_k_prediction": 1.0 / (k ** 0.5),
            "note": "sd/√k との乖離が seed 間相関 ρ の効き(FR-026b)",
        },
        "detail_read_after_decision": {
            "seed_winner_nll": {str(s_): v for s_, v in seed_nll.items()},
            "bundle_winner_nll": bundle_nll,
        },
    }
    pathlib.Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(args.json_out).write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print("\n=== 足切り判定に使う数値 ===")
    print(f"primary   winner NLL 改善 = {improvement:+.6f}  "
          f"(足切り {cfg['primary_cutoff']['threshold']:+.6f}) -> "
          f"{'通過' if out['primary']['passes'] else '不通過'}")
    print(f"secondary sd 比 = {out['secondary_reported_not_gating']['ratio_bundle_over_seed']}"
          f"  (sd/√k の予測 = {1.0/(k**0.5):.4f}) ※足切りに使わない")
    print(f"\nwrote {args.json_out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="from_", default="2019-01-01")
    ap.add_argument("--to", default="2025-12-31")
    ap.add_argument("--cost-probe", action="store_true",
                    help="1 fit の所要時間だけ測る(効果量は出さない)")
    ap.add_argument("--materialized-path", default=None)
    ap.add_argument("--json", dest="json_out",
                    default=str(SPEC_DIR / "evidence" / "ensemble-spike.json"))
    args = ap.parse_args()

    cfg = load_frozen()
    print(f"凍結 spike-config hash 照合 OK / primary 足切り = "
          f"{cfg['primary_cutoff']['threshold']}")
    if args.cost_probe:
        cost_probe(args, cfg)
        return
    run(args, cfg)


if __name__ == "__main__":
    main()
