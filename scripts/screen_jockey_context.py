"""騎手 identity × 文脈の交互作用(非加法)の上限を分割オラクルで測る — screening。

なぜこれを測るのか
------------------
107 は「騎手の**加法的**切片」を confirmatory まで持って行き REJECT で閉じた
(独立窓 −0.0019・CI ゼロ跨ぎ)。だが 107 の spec は閉鎖範囲を明示的に限定している:

    「TE 置換全体・時変効果・**他特徴との交互作用**は kill できていない」

実測された構造的制約はこうである: production の `jockey_id` は OOF target encoding で
**勝率スカラー 1 次元**に潰されており、同じエンコード値の 2 人をモデルは原理的に区別できない
(feature_infos で確認済み)。107 が試したのは「全文脈で一律に上乗せする切片」— つまり
「この騎手は総じて強い/弱い」という 1 次元の補正だった。**「この騎手はこの条件で強い」**
という非加法の形は一度も測っていない。

測り方(107/102 と同じ計器を再利用し、二重実装を作らない)
--------------------------------------------------------
`screen_architecture.py` の分割オラクルをそのまま import する。fit 側の開催日で係数を推定し、
**held-out 側で評価**するので in-sample の過学習 envelope が入らない(102 で確立した形)。
帰無は条件付きパラメトリック(各レースの勝者を Categorical(p_r) から引き直す)。

**base = 加法的騎手切片 / full = 加法 + 交互作用**として**増分**を測る。107 が閉じた加法形の
上に何が乗るかだけを見るためで、ゼロ基準で測ると加法効果を再測定するだけになる。

データ
------
108 で生成した**全史 OOF 束**(19 fold・64,542 レース・2.6 時間)を再利用する。107 の screening が
使った 5,671 レース(2025-2026)の 11 倍で、交互作用のようにセルが細かく割れる仮説には
この規模が要る。束は arm E の手続きで strict-past に作られた校正後予測なので、
オラクルの offset として正しい。

事前登録した打ち切り規則(実行前に固定)
--------------------------------------
**分割オラクルは実現可能効果を約 2 倍過大評価する**(107 実測: オラクル −0.0039 →
本番手続き −0.0020)。採用の実効バーは δ=0.00352 なので、オラクル増分が
**−0.00704 より良くなければ本番手続きでバーに届く見込みはない**。

    増分 ≤ −0.00704  → 生存(confirmatory の spec を書く価値がある)
    それ以外          → **この軸を閉じる**(spec を書かない)
    増分 ≥ 帰無最小値 → 情報なし(交互作用そのものが無い)

**SCREENING ONLY — can_adopt=false。** 採否は別窓の confirmatory でしか決められない。

    cd training && uv run python ../scripts/screen_jockey_context.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

REPO = pathlib.Path(__file__).resolve().parent.parent
DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
BUNDLE = (REPO / "artifacts/oof/"
          "8bdde26857f62c5571ef45b02954836dacae7e4a2ea9174c12eb0c9209fb691f/bundle.json")

MIN_RIDES = 30          # fit 側でこれ未満の騎手は係数を持たない(107 と同じ)
KILL_THRESHOLD = -0.00704   # = δ 0.00352 × オラクルの過大評価係数 2(事前登録)


def _oracle_module():
    """分割オラクルの実装は 1 つだけ(screen_architecture.py)。ここでは import して使う。"""
    path = REPO / "scripts" / "screen_architecture.py"
    spec = importlib.util.spec_from_file_location("screen_architecture", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("screen_architecture", mod)
    spec.loader.exec_module(mod)
    return mod


def load_bundle_frame() -> pd.DataFrame:
    """108 の OOF 束 + DB の文脈列 → オラクルが食う 1 行 1 頭の表。"""
    payload = json.loads(BUNDLE.read_text())
    rows = [
        {"race_id": rid, "horse_id": hid, "p": float(v["win"])}
        for rid, horses in payload["predictions"].items()
        for hid, v in horses.items()
    ]
    d = pd.DataFrame(rows)

    with create_engine(DB).connect() as c:
        ctx = pd.read_sql(text(
            "SELECT r.race_id, r.race_date, r.track_type, r.distance, r.venue_code, "
            "       r.going, r.race_class, "
            "       rh.horse_id, rh.jockey_id, "
            "       (rr.finish_order = 1 AND rr.result_status = 'finished') AS is_win "
            "FROM races r "
            "JOIN race_horses rh ON rh.race_id = r.race_id AND rh.entry_status = 'started' "
            "LEFT JOIN race_results rr ON rr.race_id = rh.race_id AND rr.horse_id = rh.horse_id"
        ), c)
    d = d.merge(ctx, on=["race_id", "horse_id"], how="inner")

    # 適格: 勝者がちょうど 1 頭・騎手が判明・p>0(log offset は 0 を復活できない)
    d["is_win"] = d["is_win"].fillna(False).astype(int)
    wins = d.groupby("race_id")["is_win"].transform("sum")
    d = d[(wins == 1) & d["jockey_id"].notna() & (d["p"] > 0)].copy()

    d["lp"] = np.log(d["p"])
    d["field_size"] = d.groupby("race_id")["horse_id"].transform("size")
    return d.sort_values(["race_date", "race_id"], kind="stable").reset_index(drop=True)


def context_labels(d: pd.DataFrame) -> dict[str, pd.Series]:
    """文脈の候補。いずれもレース属性(結果を読まない)。"""
    dist = pd.cut(d["distance"], [0, 1400, 1800, 2200, 10_000],
                  labels=["sprint", "mile", "middle", "long"])
    field = pd.cut(d["field_size"], [0, 8, 13, 100], labels=["small", "mid", "large"])
    return {
        "surface": d["track_type"].astype(str),
        "distance_band": dist.astype(str),
        "venue": d["venue_code"].astype(str),
        "going": d["going"].fillna("unknown").astype(str),
        "field_size_band": field.astype(str),
    }


def jockey_codes(d: pd.DataFrame, in_fit: np.ndarray) -> tuple[np.ndarray, int]:
    """fit 側で MIN_RIDES 以上の騎手だけに係数を与える(残りは共通の 'その他' 列に潰す)。"""
    j = d["jockey_id"].astype(str)
    counts = j[in_fit].value_counts()
    keep = set(counts[counts >= MIN_RIDES].index)
    codes, uniq = pd.factorize(j.where(j.isin(keep), "__other__"))
    return codes, len(uniq)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", dest="json_out", default="../out/screen_jockey_context.json")
    ap.add_argument("--n-null", type=int, default=6)
    args = ap.parse_args()

    mod = _oracle_module()
    print("*** SCREENING ONLY — can_adopt=false ***")
    print(f"事前登録: 増分 ≤ {KILL_THRESHOLD:+.5f} で生存 / それ以外はこの軸を閉じる")
    print("(分割オラクルは実現可能効果を約 2 倍過大評価する実績 → δ 0.00352 × 2)\n")

    d = load_bundle_frame()
    in_fit, in_inner = mod.walk_forward_masks(d)
    print(f"レース {d['race_id'].nunique():,} / 行 {len(d):,} / "
          f"fit 側 {in_fit.sum():,} 行・held-out {(~in_fit).sum():,} 行")

    codes, n_j = jockey_codes(d, in_fit)
    X_add = mod.onehot(pd.Series(codes))
    print(f"騎手 {n_j} 列(fit 側 {MIN_RIDES} 騎乗以上 + その他)\n")

    results = {}
    # 参考: 加法形そのもの(107 のオラクルを 11 倍のデータで再現)
    real, nmin, nmed, k, lam = mod.oracle(d, in_fit, in_inner, X_add, None, args.n_null)
    results["additive_only"] = {"increment": real, "null_min": nmin, "null_median": nmed,
                                "n_params": k, "ridge": lam, "note": "107 の再現(参考値)"}
    print(f"{'加法のみ(参考)':<22} {real:+.6f}  帰無最小 {nmin:+.6f}  列 {k}")

    for name, labels in context_labels(d).items():
        inter = mod.onehot(pd.Series(codes).astype(str) + "|" + labels.to_numpy())
        X_full = mod.hstack(X_add, inter)
        real, nmin, nmed, k, lam = mod.oracle(d, in_fit, in_inner, X_full, X_add, args.n_null)
        results[name] = {"increment": real, "null_min": nmin, "null_median": nmed,
                         "n_params": k, "ridge": lam}
        mark = "**生存**" if real <= KILL_THRESHOLD else (
            "情報なし" if real >= nmin else "届かず")
        print(f"{'× ' + name:<22} {real:+.6f}  帰無最小 {nmin:+.6f}  列 {k}  {mark}")

    survivors = [k for k, v in results.items()
                 if k != "additive_only" and v["increment"] <= KILL_THRESHOLD]
    verdict = "survives" if survivors else "axis_closed"
    print(f"\n=== 判定(事前登録規則) === {verdict}"
          + (f" — {survivors}" if survivors else " — この軸を閉じる"))

    out = {"screening_only": True, "can_adopt": False,
           "bundle": str(BUNDLE.relative_to(REPO)),
           "n_races": int(d["race_id"].nunique()), "n_rows": int(len(d)),
           "min_rides": MIN_RIDES, "kill_threshold": KILL_THRESHOLD,
           "kill_threshold_rationale": "δ=0.00352 × オラクルの過大評価係数 2(107 実測)",
           "results": results, "survivors": survivors, "verdict": verdict}
    pathlib.Path(args.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"  wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
