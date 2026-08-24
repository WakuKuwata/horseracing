"""margin-aware 教師信号の kill-test spike(GO/NO-GO・採用ゲートではない)。

背景: PL top-3 はハナ差の 2 着と 10 馬身差の 2 着に同じ教師信号を与える。実測(≤2023、
着順ステージ 1..3 の次着との時計差)では中央値 0.1〜0.2 秒・**22〜25% が 0.0 秒**(0.1s 解像度
未満の接戦)= 教師信号の約 1/4 が実質コイントスの順位に満額の重みを持つ。着差でステージ損失を
減衰すれば、順位ノイズから能力差を分離できるはず、という仮説を安く測る。

事前登録(実行前に固定・spike 窓を見て調整しない):
  変調関数   g(m) = clip(m / M0, GMIN, 1.0),  M0 = 0.2s,  GMIN = 0.25
             (≤2023 の分布から選択: M0≈p50-p75、gap=0 のステージは重み 1/4。増幅はしない)
  margin_j   = ステージ j の的中馬と次の完走馬の finish_time 差(秒)。次馬なし/時計欠損 → 1.0
  V1         = ステージ 2,3 のみ変調(088 で予約された形。勝者ステージは不変)
  V2         = ステージ 1,2,3 すべて変調(勝者ラベルのノイズも減衰する対照)
  spike 窓   = valid 年 2024 / 2025 / 2026(expanding train、2008〜)
  構成       = active レシピ準拠: pl_topk + isotonic(holdout 0.3, race_count_v1)+
               TE(jockey/trainer)+ weight mask 0.5/20260810 + seed 42。
               ただし rounds=300(spike の宣言事項: 本ゲートは 900 で再確認する)
  PRIMARY    = レース単位 winner NLL の paired 差(variant − baseline)、3 窓 pooled
  GO 規則    = pooled 差 ≤ −0.002(採用 δ と同値)の variant があれば GO →
               本実装+事前登録ゲートへ。それ以外は NO-GO(軸を数値つきで閉じる)
  構造 assert= 両アームの予測が同一なら故障(097 の教訓: 差 0.000000 は結果でなく故障)

リーク境界: margin は結果由来だが **ラベル側のみ**(finish_rank と同じ契約)。特徴列には
一切入らない(このスクリプトは特徴行列を変更しない)。

usage:
  uv run --project training python scripts/margin_teacher_spike.py --selftest
  DATABASE_URL=... nohup uv run --project training python scripts/margin_teacher_spike.py \
      --out out/margin_spike.json &
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent

M0 = 0.2
GMIN = 0.25
SPIKE_ROUNDS = 300
SEED = 42
VALID_YEARS = (2024, 2025, 2026)
GO_DELTA = -0.002


# --- margin-aware objective(production pl_topk_objective の拡張コピー) ---------------------
# stage_scales=None で production と勾配一致(--selftest が固定)。本体は触らない(spike 規律)。

def margin_pl_topk_objective(group_sizes, ranks, stage_scales=None):
    from horseracing_training.cond_logit import STAGE_WEIGHTS, _HESS_FLOOR, _NEG_SENTINEL

    ranks = np.asarray(ranks)
    gsize = np.asarray(group_sizes, dtype=np.int64)
    n = int(gsize.sum())
    n_groups = len(gsize)
    group_id = np.repeat(np.arange(n_groups), gsize)
    group_start = np.concatenate(([0], np.cumsum(gsize)[:-1])).astype(np.intp)

    k = len(STAGE_WEIGHTS)
    if stage_scales is None:
        stage_scales = np.ones((n_groups, k), dtype=float)
    stage_scales = np.asarray(stage_scales, dtype=float)
    assert stage_scales.shape == (n_groups, k), stage_scales.shape

    counts = [
        np.bincount(group_id, weights=(ranks == j), minlength=n_groups) for j in range(1, k + 1)
    ]
    fire_group = []
    prev = np.ones(n_groups, dtype=bool)
    for j in range(1, k + 1):
        remaining_before = gsize.astype(float) - (j - 1)
        fj = prev & (counts[j - 1] == 1) & (remaining_before >= 2)
        fire_group.append(fj)
        prev = fj

    stages = []
    for j in range(1, k + 1):
        w = STAGE_WEIGHTS[j - 1]
        placed_before = (ranks >= 1) & (ranks <= j - 1)
        target = (ranks == j).astype(float)
        fire_row = fire_group[j - 1][group_id]
        remaining_row = fire_row & ~placed_before
        # ここが唯一の変更点: レースごとのステージ減衰を重みに掛ける(行に展開)
        w_row = w * stage_scales[group_id, j - 1]
        stages.append(
            (w_row, placed_before, target, fire_row.astype(float), remaining_row.astype(float))
        )

    def fobj(preds, dataset):
        preds = np.asarray(preds, dtype=float)
        grad = np.zeros(n, dtype=float)
        hess = np.zeros(n, dtype=float)
        for w_row, placed_before, target, fire_row, remaining_row in stages:
            masked = np.where(placed_before, _NEG_SENTINEL, preds)
            seg_max = np.maximum.reduceat(masked, group_start)
            e = np.exp(masked - seg_max[group_id])
            seg_sum = np.add.reduceat(e, group_start)
            p = e / seg_sum[group_id]
            grad += w_row * (p - target) * fire_row
            hess += w_row * np.maximum(p * (1.0 - p), _HESS_FLOOR) * remaining_row
        hess = np.maximum(hess, _HESS_FLOOR)
        w_arr = dataset.get_weight()
        if w_arr is not None:
            w_arr = np.asarray(w_arr, dtype=float)
            grad *= w_arr
            hess *= w_arr
        return grad, hess

    return fobj


def selftest() -> None:
    """all-ones の scale が production 勾配とビット一致すること + 変調がステージ限定であること。"""
    from horseracing_training.cond_logit import pl_topk_objective

    rng = np.random.default_rng(7)
    gsizes = [8, 5, 12, 3]
    n = sum(gsizes)
    ranks = np.zeros(n, dtype=int)
    pos = 0
    for g in gsizes:
        order = rng.permutation(g)
        for j in range(min(3, g)):
            ranks[pos + order[j]] = j + 1
        pos += g
    preds = rng.normal(size=n)

    class _DS:
        def get_label(self):
            return (ranks == 1).astype(float)

        def get_weight(self):
            return None

    base = pl_topk_objective(gsizes, ranks)
    ours = margin_pl_topk_objective(gsizes, ranks, stage_scales=None)
    g0, h0 = base(preds, _DS())
    g1, h1 = ours(preds, _DS())
    assert np.array_equal(g0, g1) and np.array_equal(h0, h1), "all-ones が production と不一致"

    # ステージ 2 だけ 0.5 に減衰 → 勝者(rank==1)行の勾配のうちステージ 1 成分は不変。
    # 対照として全ステージ 0.5 なら勾配は厳密に半分。
    sc_half_all = np.full((len(gsizes), 3), 0.5)
    g_half, h_half = margin_pl_topk_objective(gsizes, ranks, stage_scales=sc_half_all)(preds, _DS())
    assert np.allclose(g_half, 0.5 * g0) and np.allclose(h_half, 0.5 * h0), "一様 0.5 が半分でない"

    sc_s2 = np.ones((len(gsizes), 3))
    sc_s2[:, 1] = 0.0  # ステージ 2 を消す
    g_s2, _ = margin_pl_topk_objective(gsizes, ranks, stage_scales=sc_s2)(preds, _DS())
    assert not np.array_equal(g_s2, g0), "ステージ 2 の変調が効いていない"
    print("selftest OK: all-ones=production 一致 / 一様 0.5=厳密半分 / ステージ限定変調が有効")


# --- margin 取得(DB→per-race stage scale) ---------------------------------------------------

def load_stage_scales(engine) -> dict[str, tuple[float, float, float]]:
    """race_id -> (s1, s2, s3)。次の完走馬との時計差から g(m)。欠損は 1.0(中立)。"""
    # NOTE spike run 1 のバグ修正: WHERE finish_order<=3 は window の前に効くので、3 着の
    # 「次の完走馬」(4 着)が消えて gap_next=NULL → ステージ 3 が全レース中立(s3 平均 0.998 で
    # 発覚)。lead は CTE で全完走馬に対して計算し、外側で 1..3 に絞る(分布事前調査と同じ形)。
    sql = """
        WITH gaps AS (
            SELECT rr.race_id, rr.finish_order,
                   extract(epoch FROM lead(rr.finish_time)
                       OVER (PARTITION BY rr.race_id ORDER BY rr.finish_order) - rr.finish_time
                   ) AS gap_next
            FROM race_results rr
            WHERE rr.result_status = 'finished' AND rr.finish_time IS NOT NULL
        )
        SELECT race_id, finish_order, gap_next FROM gaps WHERE finish_order <= 3
    """
    df = pd.read_sql(sql, engine)
    out: dict[str, list[float]] = {}
    for rid, fo, gap in zip(df["race_id"], df["finish_order"], df["gap_next"]):
        s = out.setdefault(rid, [1.0, 1.0, 1.0])
        if gap is not None and np.isfinite(gap):
            s[int(fo) - 1] = float(np.clip(max(gap, 0.0) / M0, GMIN, 1.0))
    return {rid: tuple(v) for rid, v in out.items()}


# --- fit/predict(production の意味論を両アーム共通で複製) -----------------------------------

def fit_and_predict(data, train_ids, valid_ids, *, variant, scales, mask_spec):
    """variant: None=baseline / 'v1'(stage2,3) / 'v2'(全ステージ)。

    LightGBMPredictor.fit を複製する(custom objective を差し込む注入点が無いため)。
    複製の系統誤差は両アームで共通なので paired 差からは消える(spike の設計)。
    """
    import lightgbm as lgb
    from horseracing_features.weight_mask import apply_weight_mask
    from horseracing_training.calibration import fit_calibrator, split_train_by_time
    from horseracing_training.cond_logit import group_sizes_from_race_ids, race_softmax
    from horseracing_training.dataset import RANK_LABEL, WIN_LABEL
    from horseracing_training.target_encoding import (
        apply_encoded_columns,
        fit_target_encoder,
        oof_target_encode,
    )
    from horseracing_training.win_model import DEFAULT_PARAMS

    df = data.frame
    train_df = df[df["race_id"].isin(train_ids)].reset_index(drop=True)
    train_df = apply_weight_mask(train_df, spec=mask_spec)

    race_dates = dict(zip(train_df["race_id"], train_df["race_date"], strict=True))
    model_mask, calib_mask = split_train_by_time(
        train_df["race_id"].to_numpy(), race_dates, calib_frac=0.3
    )
    model_df = train_df[model_mask].reset_index(drop=True)
    calib_df = train_df[calib_mask].reset_index(drop=True)
    y_model = model_df[WIN_LABEL].to_numpy()

    te_cols = ("jockey_id", "trainer_id")
    cat_for_model = [c for c in data.categorical_cols if c not in te_cols]
    prior = float(y_model.mean())
    encoders = {}
    model_enc, calib_enc = {}, {}
    for col in te_cols:
        encoders[col] = fit_target_encoder(model_df, col, label_col=WIN_LABEL, prior=prior)
        model_enc[col] = oof_target_encode(
            model_df, col, race_id_col="race_id", race_date_col="race_date",
            label_col=WIN_LABEL, prior=prior,
        ).to_numpy()
        calib_enc[col] = encoders[col].transform(calib_df[col])
    model_X = apply_encoded_columns(
        model_df[data.feature_cols].copy(), model_enc, data.feature_cols)
    calib_X = apply_encoded_columns(
        calib_df[data.feature_cols].copy(), calib_enc, data.feature_cols)

    # 行をレース連続に並べる(production _fit_softmax と同じ stable sort)
    gids = model_df["race_id"].to_numpy()
    order = np.argsort(gids, kind="stable")
    Xs = model_X.iloc[order].reset_index(drop=True)
    ys = y_model[order]
    gsorted = gids[order]
    gsizes = group_sizes_from_race_ids(gsorted)
    ranks = model_df[RANK_LABEL].to_numpy()[order]

    stage_scales = None
    if variant is not None:
        uniq = gsorted[np.concatenate(([0], np.cumsum(gsizes)[:-1]))]
        sc = np.array([scales.get(r, (1.0, 1.0, 1.0)) for r in uniq], dtype=float)
        if variant == "v1":
            sc[:, 0] = 1.0  # 勝者ステージは不変(088 の予約形)
        stage_scales = sc

    obj = margin_pl_topk_objective(gsizes, ranks, stage_scales=stage_scales)
    params = {k: v for k, v in DEFAULT_PARAMS.items() if k != "objective"}
    params.pop("n_estimators", None)
    params.update(objective=obj, seed=SEED, deterministic=True, num_threads=1,
                  force_row_wise=True, verbose=-1)
    dtrain = lgb.Dataset(Xs, label=ys.astype(float), categorical_feature=cat_for_model,
                         free_raw_data=False)
    booster = lgb.train(params, dtrain, num_boost_round=SPIKE_ROUNDS)

    # isotonic を calib holdout の race-softmax 確率で fit(production と同じ入力空間)
    def _race_probs(X, rids):
        o = np.argsort(rids, kind="stable")
        raw = booster.predict(X.iloc[o], raw_score=True)
        p_sorted = race_softmax(raw, group_sizes_from_race_ids(rids[o]))
        out = np.empty(len(X))
        out[o] = p_sorted
        return out

    calib_raw = _race_probs(calib_X, calib_df["race_id"].to_numpy())
    calibrator = fit_calibrator(calib_raw, calib_df[WIN_LABEL].to_numpy(), method="isotonic")

    # valid 予測: raw → calibrate → クリップ → レース内再正規化(INV-T1)
    valid_df = df[df["race_id"].isin(valid_ids)].reset_index(drop=True)
    v_enc = {col: encoders[col].transform(valid_df[col]) for col in te_cols}
    valid_X = apply_encoded_columns(
        valid_df[data.feature_cols].copy(), v_enc, data.feature_cols)
    raw_v = _race_probs(valid_X, valid_df["race_id"].to_numpy())
    cal_v = np.clip(np.asarray(calibrator.transform(raw_v), dtype=float), 1e-6, 1 - 1e-6)
    out = valid_df[["race_id", "horse_id", WIN_LABEL, "race_date"]].copy()
    out["p_raw"] = raw_v
    out["p"] = cal_v
    out["p"] = out["p"] / out.groupby("race_id")["p"].transform("sum")
    out["p_raw"] = out["p_raw"] / out.groupby("race_id")["p_raw"].transform("sum")
    return out


def winner_nll_by_race(pred: pd.DataFrame, col: str) -> pd.Series:
    """1 勝者レースのみ: race_id -> -log p(勝者)。"""
    w = pred[pred["win"] == 1]
    ok = w.groupby("race_id").size()
    single = set(ok[ok == 1].index)
    w = w[w["race_id"].isin(single)]
    return pd.Series(-np.log(w[col].to_numpy()), index=w["race_id"].to_numpy())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--out", default="out/margin_spike.json")
    ap.add_argument("--years", default=",".join(map(str, VALID_YEARS)))
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0

    from horseracing_db.session import create_db_engine
    from horseracing_features.weight_mask import MaskSpec
    from horseracing_training.dataset import build_training_matrix
    from sqlalchemy.orm import Session

    engine = create_db_engine()
    years = tuple(int(y) for y in args.years.split(","))
    mask_spec = MaskSpec(rate=0.5, seed=20260810, unit="race")

    with Session(engine) as session:
        print("building matrix (materialized, pinned)...", flush=True)
        data = build_training_matrix(
            session, representation="raw", use_materialized=True,
            materialized_path=str(REPO / "artifacts" / "features.parquet"),
            skip_fingerprint_verify=True,  # 評価はスナップショットを固定する(091 D16)
        )
    scales = load_stage_scales(engine)
    df = data.frame
    df["year"] = df["race_id"].str[:4].astype(int)
    sc_arr = np.array(list(scales.values()))
    print(f"matrix rows={len(df):,}  margin races={len(scales):,}  "
          f"scale means s1/s2/s3 = {sc_arr.mean(axis=0).round(3)}", flush=True)

    report = {"preregistered": {
        "M0": M0, "GMIN": GMIN, "rounds": SPIKE_ROUNDS, "seed": SEED,
        "go_rule": f"pooled paired winner-NLL diff <= {GO_DELTA}",
        "variants": {"v1": "stages 2,3", "v2": "stages 1,2,3"},
        "caveat": "spike (rounds=300, holdout isotonic); adoption needs the full v4 gate at 900",
    }, "folds": {}, "pooled": {}}
    diffs = {"v1": [], "v2": []}
    diffs_raw = {"v1": [], "v2": []}

    for y in years:
        train_ids = set(df.loc[df["year"] < y, "race_id"])
        valid_ids = set(df.loc[df["year"] == y, "race_id"])
        print(f"fold {y}: train={len(train_ids):,} races valid={len(valid_ids):,}", flush=True)
        preds = {}
        for name, variant in (("base", None), ("v1", "v1"), ("v2", "v2")):
            preds[name] = fit_and_predict(
                data, train_ids, valid_ids, variant=variant, scales=scales,
                mask_spec=mask_spec)
            print(f"  fitted {name}", flush=True)
        nll = {n: winner_nll_by_race(p, "p") for n, p in preds.items()}
        nll_raw = {n: winner_nll_by_race(p, "p_raw") for n, p in preds.items()}
        fold = {}
        for v in ("v1", "v2"):
            common = nll["base"].index.intersection(nll[v].index)
            d = (nll[v].loc[common] - nll["base"].loc[common])
            draw = (nll_raw[v].loc[common] - nll_raw["base"].loc[common])
            nz = int((d.abs() > 1e-15).sum())
            assert nz > 0, f"fold {y} {v}: 全レースで差が 0 = アームが同一(故障)"
            diffs[v].append(d)
            diffs_raw[v].append(draw)
            fold[v] = {"n_races": int(len(d)), "mean_diff": float(d.mean()),
                       "mean_diff_raw": float(draw.mean()), "nonzero": nz}
            print(f"  {v}: n={len(d)} diff={d.mean():+.6f} (raw {draw.mean():+.6f})", flush=True)
        report["folds"][y] = fold

    for v in ("v1", "v2"):
        pooled = pd.concat(diffs[v])
        pooled_raw = pd.concat(diffs_raw[v])
        report["pooled"][v] = {
            "n_races": int(len(pooled)),
            "mean_diff": float(pooled.mean()),
            "mean_diff_raw": float(pooled_raw.mean()),
            "go": bool(pooled.mean() <= GO_DELTA),
        }
        print(f"POOLED {v}: n={len(pooled)} diff={pooled.mean():+.6f} "
              f"(raw {pooled_raw.mean():+.6f}) GO={pooled.mean() <= GO_DELTA}", flush=True)

    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"written {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
