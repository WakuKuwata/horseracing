"""047(モデル vs 市場のセグメント別 gap)を、再学習なしで現行世代の OOF 束から出す。

なぜ
----
「なぜ精度候補が全部閉じるのか」の考察で、私は「モデルが公開情報の天井に近い」と言ったが、
それは根拠のない推論だった。市場がモデルに勝つことは「市場がより多くの情報を持つ」ことしか
示さない。その差(lgbm-042 時点で winner LogLoss +0.015 = δ の 4 倍)が「非公開情報だから届かない」
のか「公開情報の使い方が悪い」のかは分解できていない。

047 はこの gap をセグメント別に描く計器だが、walk-forward で fold ごとに再学習するため数時間かかり、
最後に回したのは lgbm-042 世代。108 で現行 active(lgbm-094-cap900)の全史 OOF 束が手に入ったので、
**束を返すだけの predictor を 047 に差し込む**ことで再学習なしに現行世代の地図を描く。
047 のセグメント論理(軸・帯・集計)は 1 バイトも変えない(二重実装を作らない)。

    cd training && uv run python ../scripts/segment_edge_from_bundle.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
from dataclasses import dataclass

from horseracing_db.session import create_db_engine, create_session_factory
from horseracing_eval.segment_edge import evaluate_segment_edge

REPO = pathlib.Path(__file__).resolve().parent.parent
BUNDLE = (REPO / "artifacts/oof/"
          "8bdde26857f62c5571ef45b02954836dacae7e4a2ea9174c12eb0c9209fb691f/bundle.json")


@dataclass(frozen=True)
class _Pred:
    win: float


class BundlePredictor:
    """047 が期待する predictor 契約(`fit` + `predict_race`)を束で満たす。

    `fit` は no-op: 束の予測は生成時点で既に strict-past OOF なので、047 の fold ループが
    何を train に渡そうと再学習する必要がない。束に無いレースは空を返し、047 側が
    「preds に無い馬はスキップ」で自然に除外する。
    """

    def __init__(self, bundle_path: pathlib.Path) -> None:
        self._preds = json.loads(bundle_path.read_text())["predictions"]
        self.n_hits = 0
        self.n_misses = 0

    def fit(self, _train_contexts) -> None:
        return None

    def predict_race(self, context):
        horses = self._preds.get(context.race_id)
        if horses is None:
            self.n_misses += 1
            return {}
        self.n_hits += 1
        return {hid: _Pred(win=float(v["win"])) for hid, v in horses.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="2019-01-01")
    ap.add_argument("--to", dest="date_to", default="2026-08-30")
    ap.add_argument("--json", dest="json_out", default="../out/segment_edge_current.json")
    args = ap.parse_args()
    d_from = dt.date.fromisoformat(args.date_from)
    d_to = dt.date.fromisoformat(args.date_to)

    pred = BundlePredictor(BUNDLE)
    engine = create_db_engine(None)
    with create_session_factory(engine)() as session:
        report = evaluate_segment_edge(
            session, predictor=pred, first_valid_year=d_from.year,
            start_date=d_from, end_date=d_to,
        )

    print(f"segment-edge (047 logic, bundle-backed) model=lgbm-094-cap900 "
          f"window={d_from}..{d_to}  horses={report.n_horses:,}  "
          f"races hit/miss={pred.n_hits}/{pred.n_misses}")
    print("SECONDARY — 診断。採否には使わない。gap = LL(p) − LL(q)、正 = 市場優位\n")
    rows = []
    cur_axis = None
    for r in report.rows:
        if r.axis != cur_axis:
            cur_axis = r.axis
            print(f"[{r.axis}]")
        print(f"  {r.segment:<14} n={r.n:>7,}  win={r.win_rate:.4f}  "
              f"LL(p)={r.logloss_p:.4f}  LL(q)={r.logloss_q:.4f}  "
              f"gap={r.gap:+.4f}  p̄={r.mean_p:.4f} q̄={r.mean_q:.4f}")
        rows.append({"axis": r.axis, "segment": r.segment, "n": r.n, "win_rate": r.win_rate,
                     "logloss_p": r.logloss_p, "logloss_q": r.logloss_q, "gap": r.gap,
                     "mean_p": r.mean_p, "mean_q": r.mean_q})
    out = {"secondary": True, "can_adopt": False, "model": "lgbm-094-cap900",
           "bundle": str(BUNDLE.relative_to(REPO)), "window": [str(d_from), str(d_to)],
           "n_horses": report.n_horses, "rows": rows}
    pathlib.Path(args.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"\n  wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
