"""arm E の OOF isotonic は、実際に配信される入力分布で校正されているか(DIAGNOSTIC ONLY)。

**can_adopt=false。** ここで出る数値で昇格も棄却も判断しない。目的は 1 つ、
「11 時間級の事前登録測定を回す価値があるか」を数分で見極めること。

問い(2026-08-25 の arm E 監査で判明した構造):
    内部 OOF booster は 091 の体重マスク(rate 0.5)込みで学習される。ところが校正サンプルを
    作る予測は `predict_weight_mask` が未設定のまま行われるので、**isotonic は 100% 体重ありの
    スコア分布で fit されている**。一方ライブ予測はレースの約 97% が体重欠損。arm A(従来の
    holdout isotonic)は分割前にマスクを掛けるのでこのずれが無く、091 はそれを「校正器の定義域を
    runtime に合わせる」機構の本体と位置づけていた。

なぜ booster を再学習せずに答えが出るか:
    問いは booster ではなく **校正器の fit 時 regime** だけにある。active の booster は固定した
    まま、同じ行・同じラベルから
        isotonic_full : 体重ありスコアで fit(= 現行 arm E がやっていること)
        isotonic_srv  : 体重欠損スコアで fit(= 修正案)
    の 2 本を作り、**どちらも serving regime(体重欠損)の後半窓**に当てて比べる。両者の違いは
    fit 時 regime ただ 1 つなので帰属が完全に閉じる。LightGBM の学習は一度も走らない。

正直な限界(これがあるので確認測定の代わりにはならない):
    * booster はこの窓を学習済み = スコアは in-sample 楽観。ただし 2 本の isotonic は同じ
      スコアを共有するので、**両者の差**にはこの楽観がほぼ相殺で効く。水準は読まない。
    * 比較用の 2 本は in-sample スコアで fit している(出荷版は strict-past OOF)。したがって
      「出荷版 vs 修正版」の絶対差ではなく「fit 時 regime を変えると何が起きるか」を測る。
    * serving regime は rate=1.0(全レース体重欠損)。本番は約 97%。091 と同じストレス版。

判定規則(実行前に固定する):
    serving 窓での winner NLL 改善(isotonic_srv − isotonic_full)が
      <= -0.002 → 校正の定義域ずれは実在のコスト。事前登録して確認測定を回す
      >= -0.0005 → ずれは実務上無害。arm E の当該構造は問題なしとして閉じる
      その間     → 曖昧。ECE と分布シフトを併せて判断する
    (再学習ノイズ SD 0.0018 は booster 側の話でここでは効かない=校正器のみが動くため、
     しきい値は特徴量の採否より小さく取ってよい)
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib

import numpy as np
import pandas as pd
from horseracing_db.session import create_db_engine
from horseracing_eval.dataset import load_eval_races, population_masks
from horseracing_eval.metrics import ece_equal_mass, winner_nll
from horseracing_features.weight_mask import WEIGHT_MASK_COLUMNS
from sqlalchemy.orm import Session

from horseracing_training.calibration import DEFAULT_CLIP, fit_calibrator
from horseracing_training.dataset import build_training_matrix
from horseracing_training.target_encoding import apply_encoded_columns

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"


class _Artifact:
    """active モデルの artifact を直接読む(booster / calibrator / preprocessor)。

    `horseracing_serving` は import しない。serving は training に依存するので、ここから
    serving を読むと逆依存になる(`arm_e_register._parity_check` と同じ理由)。読むファイルは
    serving が読むものと同一で、スコアの組み立ても serving/predictor.py の順序に合わせる。
    """

    def __init__(self, art_dir: str, metadata: dict) -> None:
        import pickle

        import lightgbm as lgb

        d = pathlib.Path(art_dir)
        self.booster = lgb.Booster(model_file=str(d / "model.txt"))
        with (d / "calibrator.pkl").open("rb") as fh:
            self.calibrator = pickle.load(fh)  # noqa: S301 — 自分たちの artifact
        with (d / "preprocessor.pkl").open("rb") as fh:
            prep = pickle.load(fh)  # noqa: S301
        self.feature_cols = list(prep["feature_cols"])
        self.categorical_cols = list(prep["categorical_cols"])
        self.encoders = dict(prep.get("encoders") or {})
        self.objective = prep.get("objective", "binary")
        # 学習時の表現でビルドする(098: 表現は特徴層の一部でモデル同一性に含まれる)
        self.race_class_representation = prep.get("race_class_representation", "raw")
        self.metadata = metadata

    def raw_predict(self, X: pd.DataFrame) -> np.ndarray:
        """serving.ServingModel.raw_predict と同じ後処理(pl_topk はレース内 softmax)。"""
        raw = np.asarray(self.booster.predict(X[self.feature_cols]), dtype=float)
        if self.objective in ("cond_logit", "pl_topk"):
            from horseracing_training.cond_logit import race_softmax

            return race_softmax(raw, [len(raw)]) if len(raw) else raw
        return raw


def _load_active(session) -> tuple[_Artifact, str]:
    from horseracing_db.models import ModelVersion
    from sqlalchemy import select

    row = session.execute(
        select(ModelVersion).where(ModelVersion.adoption_status == "active")
    ).scalars().one()
    art_dir = str(pathlib.Path(row.weights_uri).parent)
    metadata = json.loads((pathlib.Path(art_dir) / "metadata.json").read_text())
    return _Artifact(art_dir, metadata), row.model_version


def _design(model, rows: pd.DataFrame) -> pd.DataFrame:
    """serving/predictor.py と同じ順序で 1 レース分の X を組む(dtype 強制 -> TE -> 列順)。"""
    rows = rows.copy()
    for col in model.categorical_cols:
        if col in rows.columns:
            rows[col] = rows[col].astype("category")
    numeric = [
        c for c in model.feature_cols
        if c not in model.categorical_cols and c not in model.encoders
    ]
    for col in numeric:
        rows[col] = pd.to_numeric(rows[col], errors="coerce")
    base = rows[model.feature_cols].copy()
    if not model.encoders:
        return base
    encoded = {c: enc.transform(rows[c]) for c, enc in model.encoders.items()}
    return apply_encoded_columns(base, encoded, model.feature_cols)


def _renorm(p: np.ndarray) -> np.ndarray:
    """assemble_predictions と同じ clip -> レース内再正規化(Σ=1, 憲法 IV)。"""
    q = np.clip(np.asarray(p, dtype=float), DEFAULT_CLIP, 1.0 - DEFAULT_CLIP)
    s = q.sum()
    return q / s if s > 0 else np.full(len(q), 1.0 / len(q))


def collect(session, model, races, *, verbose_every: int = 2000) -> dict:
    """レースごとに体重あり/欠損の 2 通りで raw スコアを取る(booster は固定)。"""
    frame = build_training_matrix(
        session, representation=model.race_class_representation
    ).frame
    by_race = {rid: g for rid, g in frame.groupby("race_id", sort=False)}

    out: dict[str, list] = {
        "race_id": [], "race_date": [], "raw_full": [], "raw_srv": [],
        "label": [], "winner_idx": [], "field": [],
    }
    skipped = {"no_rows": 0, "coverage": 0, "no_single_winner": 0, "misaligned": 0}
    for n, er in enumerate(races, 1):
        if verbose_every and n % verbose_every == 0:
            print(f"    {n}/{len(races)} races", flush=True)
        pop = population_masks(er)
        if not pop.complete_results:
            skipped["coverage"] += 1
            continue
        started = list(pop.started_horse_ids)
        wins = [h for h in started if pop.started_win.get(h)]
        if len(wins) != 1:
            skipped["no_single_winner"] += 1
            continue
        g = by_race.get(er.context.race_id)
        if g is None:
            skipped["no_rows"] += 1
            continue
        rows = g.set_index("horse_id").reindex(started)
        if rows[model.feature_cols].isna().all(axis=1).any():
            skipped["misaligned"] += 1
            continue
        rows = rows.reset_index()

        srv_rows = rows.copy()
        srv_rows.loc[:, list(WEIGHT_MASK_COLUMNS)] = float("nan")

        raw_full = np.asarray(model.raw_predict(_design(model, rows)), dtype=float)
        raw_srv = np.asarray(model.raw_predict(_design(model, srv_rows)), dtype=float)
        if not (np.isfinite(raw_full).all() and np.isfinite(raw_srv).all()):
            skipped["misaligned"] += 1
            continue

        out["race_id"].append(er.context.race_id)
        out["race_date"].append(er.context.race_date)
        out["raw_full"].append(raw_full)
        out["raw_srv"].append(raw_srv)
        out["label"].append(np.asarray([int(pop.started_win.get(h, 0)) for h in started]))
        out["winner_idx"].append(started.index(wins[0]))
        out["field"].append(len(started))
    out["skipped"] = skipped
    return out


def _score(data: dict, idx: list[int], calibrator, *, regime: str) -> dict:
    """serving 経路と同じ順序(calibrator -> clip -> 再正規化)で採点する。"""
    key = "raw_srv" if regime == "serving" else "raw_full"
    winner_probs, probs, labels = [], [], []
    for i in idx:
        p = _renorm(calibrator.transform(data[key][i]))
        winner_probs.append(float(p[data["winner_idx"][i]]))
        probs.extend(p.tolist())
        labels.extend(data["label"][i].tolist())
    nll, excluded = winner_nll(winner_probs)
    ece = ece_equal_mass(probs, labels)
    return {
        "winner_nll": nll, "n_races": len(idx), "n_excluded": excluded,
        "ece": ece.get("ece"), "n_rows": len(probs),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_", default="2024-01-01")
    ap.add_argument("--to", dest="to", default="2026-08-16")
    ap.add_argument("--fit-frac", type=float, default=0.5,
                    help="前半(校正器の学習)に使う開催日の割合。後半が採点窓")
    ap.add_argument("--json", dest="json_out", default="out/armE-calibration-domain.json")
    args = ap.parse_args()

    d_from = datetime.date.fromisoformat(args.from_)
    d_to = datetime.date.fromisoformat(args.to)

    engine = create_db_engine(DB)
    with Session(engine) as session:
        model, model_version = _load_active(session)
        proto = (model.metadata.get("calibration_protocol") or {}).get("protocol")
        print(f"active = {model_version}  objective={model.objective}  protocol={proto}")
        if proto != "strict_past_oof_isotonic_v1":
            raise SystemExit(
                f"active is not an arm E model (protocol={proto!r}); nothing to diagnose"
            )

        races = [
            er for er in load_eval_races(session, end_date=d_to)
            if d_from <= er.context.race_date <= d_to
        ]
        print(f"window {d_from}..{d_to}: {len(races)} races — scoring under both regimes")
        data = collect(session, model, races)

    n = len(data["race_id"])
    if n == 0:
        raise SystemExit(f"no usable races (skipped={data['skipped']})")
    days = sorted({d for d in data["race_date"]})
    cut = days[int(len(days) * args.fit_frac)]
    fit_idx = [i for i in range(n) if data["race_date"][i] < cut]
    ev_idx = [i for i in range(n) if data["race_date"][i] >= cut]
    print(f"usable {n} races (skipped={data['skipped']}) — "
          f"fit {len(fit_idx)} (<{cut}) / eval {len(ev_idx)} (>={cut})")
    if not fit_idx or not ev_idx:
        raise SystemExit("degenerate split")

    y = np.concatenate([data["label"][i] for i in fit_idx])
    cal = {}
    for name, key in (("isotonic_full", "raw_full"), ("isotonic_srv", "raw_srv")):
        x = np.concatenate([data[key][i] for i in fit_idx])
        cal[name] = fit_calibrator(x, y, method="isotonic", clip=DEFAULT_CLIP)
        print(f"  {name}: fitted on {len(x):,} rows, degenerate={cal[name].identity}")

    # --- 入力分布のずれそのもの(校正器がどれだけ別の分布を見せられているか) ---
    a = np.concatenate([data["raw_full"][i] for i in fit_idx])
    b = np.concatenate([data["raw_srv"][i] for i in fit_idx])
    qs = [0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99]
    shift = {
        "quantiles": {str(q): [float(np.quantile(a, q)), float(np.quantile(b, q))] for q in qs},
        "mean_abs_delta": float(np.mean(np.abs(a - b))),
        "mean_delta": float(np.mean(b - a)),
        "corr": float(np.corrcoef(a, b)[0, 1]),
    }

    # --- 帰結: どちらの校正器も serving regime の後半窓に当てる ---
    res = {
        name: _score(data, ev_idx, c, regime="serving") for name, c in cal.items()
    }
    res["shipped_isotonic"] = _score(data, ev_idx, model.calibrator, regime="serving")
    res["isotonic_full@full_info"] = _score(data, ev_idx, cal["isotonic_full"], regime="full")

    diff = res["isotonic_srv"]["winner_nll"] - res["isotonic_full"]["winner_nll"]
    verdict = ("material" if diff <= -0.002
               else "immaterial" if diff >= -0.0005 else "ambiguous")

    payload = {
        "artifact_kind": "diagnostic", "can_adopt": False,
        "model_version": model_version, "protocol": proto,
        "window": {"from": str(d_from), "to": str(d_to), "fit_before": str(cut)},
        "n_races_usable": n, "skipped": data["skipped"],
        "input_domain_shift": shift, "scores": res,
        "winner_nll_diff_srv_minus_full": diff, "verdict": verdict,
    }
    p = pathlib.Path(args.json_out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, default=str))

    print("\n  入力分布のずれ(校正器が見るスコア)")
    print(f"    mean|Δ| = {shift['mean_abs_delta']:.6f}   mean Δ = {shift['mean_delta']:+.6f}"
          f"   corr = {shift['corr']:.5f}")
    for q in qs:
        f_, s_ = shift["quantiles"][str(q)]
        print(f"    q{q:<5} 体重あり {f_:.5f}  →  体重欠損 {s_:.5f}   ({s_ - f_:+.5f})")
    print("\n  serving regime の採点窓での帰結")
    for name in ("isotonic_full", "isotonic_srv", "shipped_isotonic", "isotonic_full@full_info"):
        r = res[name]
        print(f"    {name:<26} winner NLL {r['winner_nll']:.6f}   ECE {r['ece']:.6f}"
              f"   n={r['n_races']:,}")
    print(f"\n  fit 時 regime の効果 (srv − full) = {diff:+.6f}  → {verdict}")
    print(f"  wrote {args.json_out}")


if __name__ == "__main__":
    main()
