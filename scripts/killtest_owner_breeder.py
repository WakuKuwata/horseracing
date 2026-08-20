"""Kill-test: netkeiba 由来馬に馬主・生産者が無いことは、いくらの損失か。

JRA-VAN 期の馬は馬主・生産者が 100% 埋まっているが、netkeiba 由来の馬は **0%** である。2026 の
出走馬 9,286 頭のうち 4,031 頭(43%)がそちらなので、`asof_owner_win_rate` /
`asof_owner_place_rate` / `asof_breeder_win_rate` の充足率は 94% から 60% に落ちている。この 3 列
は稼働モデルの split の 4.3% を占める。

原因は取得ではなく解析にある: netkeiba の馬ページには馬主も生産者も載っており、しかも parser が
生年月日のために**既に走査しているテーブル**(`table.db_prof_table`)の中にある。2 行読んでいない
だけで、新規リクエストは要らない。

**測ってから払う。** 過去分を埋めるには約 4,000 頭の再取得(1req/分で約 3 日)が要るので、その前に
「この 3 列が現行モデルにとっていくらの価値か」を再取得ゼロで出す。091 の体重 kill-test と同じ形:
稼働モデルを固定し(再学習なし)、上流を書き換えてから特徴を組み直し、レース単位 winner NLL を
開催日クラスタ bootstrap で比べる。

    arm A (real)      : 馬主・生産者あり = 修正後にあるべき状態
    arm B (all NULL)  : 全馬 NULL = **上限**。ストレス版であって実運用の状態ではない
    arm C (43% NULL)  : 実際の欠損率で馬単位に NULL = **実現見込み**

    diff = A - arm : 負なら「ある方が良い」= 修正の価値

**arm B をそのまま実現値として読んではいけない。** 学習時の充足は 93% で、100% 欠損はモデルが
見たことのない入力分布である。LightGBM は全行を欠損分岐に送るので「特徴が無い」より悪い挙動に
なりうる。実運用で欠けているのは 43% なので、判断は arm C で行う。

arm C の欠損は**馬単位**に与える。本番でも欠けるのは「netkeiba 由来の馬」という単位であり、行単位
にランダムに散らすと、同じ馬の過去走だけが部分的に欠ける非現実的な状態になる。またこの 3 列は
「同じ馬主の"他の"馬の実績」なので、馬を落とせばその馬主の集計からも消える — 本番で netkeiba 馬が
馬主集計に寄与していないのと同じ挙動になる。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pickle
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from horseracing_features.builder import build_feature_matrix
from horseracing_training.cond_logit import race_softmax
from horseracing_training.target_encoding import apply_encoded_columns
from sqlalchemy import text
from sqlalchemy.orm import Session

from horseracing_db.session import create_db_engine

#: リポジトリ直下に固定する。相対パスだと実行ディレクトリ次第で落ち先が変わり、実際に一度
#: training/out/ に書かれた。証跡は追跡される場所に置く(root の out/ は git 管理外)。
OUT = Path(__file__).resolve().parent.parent / "evidence"
#: 稼働モデルの置き場は DB が正本。相対パスを書くと実行ディレクトリ次第で落ちるし、
#: 行が artifact より長生きする事故もあるので、毎回 DB から引いて実在を確かめる。
CLIP = 1e-6
END = dt.date(2026, 8, 16)
FROM = dt.date(2019, 1, 1)          # 判定窓と揃える(検出力のため全窓を使う)
CURRENT_REGIME = dt.date(2025, 1, 1)  # 供給元切替後だけの読みも併記する
#: 2026 の出走馬 9,286 頭のうち netkeiba 由来 4,031 頭 = 43.4%
MASK_PCT = 43
COLS = ("asof_owner_win_rate", "asof_owner_place_rate", "asof_breeder_win_rate")


def _load_model(engine):
    with engine.connect() as c:
        mv, uri = c.execute(text(
            "SELECT model_version, weights_uri FROM model_versions "
            "WHERE adoption_status = 'active'")).one()
    root = Path(uri).parent
    if not (root / "model.txt").exists():
        raise SystemExit(f"active model '{mv}' の artifact が見つからない: {root}")
    print(f"active = {mv}  ({root})")
    booster = lgb.Booster(model_file=str(root / "model.txt"))
    prep = pickle.load(open(root / "preprocessor.pkl", "rb"))
    calib = pickle.load(open(root / "calibrator.pkl", "rb"))
    return mv, booster, prep, calib


def _predict(rows: pd.DataFrame, sizes: list[int], booster, prep, calib) -> np.ndarray:
    cols, cats = list(prep["feature_cols"]), list(prep.get("categorical_cols", []))
    encoders = prep.get("encoders", {})
    rows = rows.copy()
    for c in cats:
        if c in rows.columns:
            rows[c] = rows[c].astype("category")
    for c in [c for c in cols if c not in cats and c not in encoders]:
        rows[c] = pd.to_numeric(rows[c], errors="coerce")
    X = rows[cols].copy()
    if encoders:
        X = apply_encoded_columns(X, {c: e.transform(rows[c]) for c, e in encoders.items()}, cols)
    p = np.clip(np.asarray(calib.transform(race_softmax(
        np.asarray(booster.predict(X), dtype=float), sizes)), dtype=float), CLIP, 1 - CLIP)
    idx = np.repeat(np.arange(len(sizes)), sizes)
    return p / np.bincount(idx, weights=p, minlength=len(sizes))[idx]


def _winner_nll_by_day(feats, p, winners, days):
    by_day: dict[str, list[float]] = {}
    for rid, g in feats.assign(_p=p).groupby("race_id", sort=True):
        w = winners.get(rid)
        if w is None:
            continue
        hit = g.loc[g["horse_id"] == w, "_p"]
        if hit.empty:
            continue
        by_day.setdefault(str(days[rid])[:10], []).append(
            -float(np.log(max(float(hit.iloc[0]), CLIP))))
    return by_day


def _cluster_ci(diff_by_day, b=2000, seed=20260820):
    days = sorted(diff_by_day)
    if not days:
        return None
    per = np.array([np.mean(diff_by_day[d]) for d in days])
    wts = np.array([len(diff_by_day[d]) for d in days], dtype=float)
    point = float(np.sum(per * wts) / wts.sum())
    rng = np.random.default_rng(seed)
    boots = [
        float(np.sum(per[pick] * wts[pick]) / wts[pick].sum())
        for pick in (rng.integers(0, len(days), len(days)) for _ in range(b))
    ]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"point": point, "ci_low": float(lo), "ci_high": float(hi),
            "n_days": len(days), "n_races": int(wts.sum())}


def main() -> int:
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
    OUT.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine()
    mv, booster, prep, calib = _load_model(engine)

    with Session(engine) as s:
        winners = {r.race_id: r.horse_id for r in s.execute(text(
            "SELECT race_id, horse_id FROM race_results "
            "WHERE finish_order = 1 AND result_status = 'finished'"))}
        days = {r.race_id: r.race_date for r in s.execute(
            text("SELECT race_id, race_date FROM races"))}

        per_arm = {}
        try:
            for arm in ("A_real", "B_masked_all", "C_masked_43pct"):
                s.rollback()   # 前のアームの書き換えを捨ててから始める
                if arm == "C_masked_43pct":
                    # 馬単位・決定論(seed 固定)。実欠損率 43% に合わせる。
                    n = s.execute(text("""
                        UPDATE horses SET owner_name = NULL, breeder_name = NULL
                        WHERE (owner_name IS NOT NULL OR breeder_name IS NOT NULL)
                          AND (abs(hashtext(horse_id)) % 100) < :pct
                    """), {"pct": MASK_PCT}).rowcount
                    print(f"  arm C: {n:,} 頭を NULL 化({MASK_PCT}%・馬単位・未 commit)")
                elif arm == "B_masked_all":
                    # 上流を書き換えて commit しない。出力列を手で潰すと自分の算術を試すだけになるので、
                    # 実際の導出コードを走らせる(091/grade kill-test と同じ規律)。
                    n = s.execute(text(
                        "UPDATE horses SET owner_name = NULL, breeder_name = NULL "
                        "WHERE owner_name IS NOT NULL OR breeder_name IS NOT NULL")).rowcount
                    print(f"  arm B: {n:,} 頭の馬主・生産者を NULL 化(全馬・未 commit)")
                feats = build_feature_matrix(s, end_date=END)
                feats = feats[feats["race_id"].map(days).between(FROM, END)]
                feats = feats[feats["race_id"].isin(winners)].sort_values(
                    ["race_id", "horse_id"]).reset_index(drop=True)
                cov = {c: float(feats[c].notna().mean()) for c in COLS if c in feats.columns}
                sizes = feats.groupby("race_id", sort=True).size().tolist()
                p = _predict(feats, sizes, booster, prep, calib)
                per_arm[arm] = {"by_day": _winner_nll_by_day(feats, p, winners, days),
                                "coverage": cov, "n_rows": len(feats)}
                print(f"  {arm}: rows={len(feats):,}  充足={ {k: round(v,3) for k,v in cov.items()} }")
        finally:
            s.rollback()   # arm B の書き換えを必ず捨てる

    a = per_arm["A_real"]["by_day"]
    out: dict = {}
    print("\n=== diff = A(あり) − arm。負なら「ある方が良い」= 修正の価値 ===")
    for arm in ("B_masked_all", "C_masked_43pct"):
        z = per_arm[arm]["by_day"]
        shared = sorted(set(a) & set(z))
        diff = {d: [x - y for x, y in zip(a[d], z[d])] for d in shared}
        full = _cluster_ci(diff)
        recent = _cluster_ci({d: v for d, v in diff.items()
                              if dt.date.fromisoformat(d) >= CURRENT_REGIME})
        out[arm] = {"full": full, "recent": recent}
        tag = "上限(全馬 NULL)" if arm == "B_masked_all" else f"実現見込み({MASK_PCT}% NULL)"
        print(f"\n  --- {tag} ---")
        for label, r in (("全窓 2019-2026", full), ("切替後 2025-2026", recent)):
            if r:
                print(f"    {label:<20} {r['point']:+.6f}  "
                      f"CI[{r['ci_low']:+.6f}, {r['ci_high']:+.6f}]  n_races={r['n_races']:,}")
    (OUT / "owner-breeder-killtest.json").write_text(json.dumps(
        {"arms": out, "coverage": {k: v["coverage"] for k, v in per_arm.items()},
         "model": mv, "window": [str(FROM), str(END)], "mask_pct": MASK_PCT}, indent=2))
    print(f"\n  wrote {OUT/'owner-breeder-killtest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
