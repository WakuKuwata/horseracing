"""094 容量確認 — 凍結した gate-config で arm E の rounds 300 vs 900 を確認する。

**確認は探索と disjoint な窓で行う。** 設定(900)はスクリーニング窓 2025-2026 で選んだので、
ここでは 2019-2024 だけを採点する。窓が重なっていたら選択リークになる。

paired-eval の CLI は recipe spec 文字列で arm を組み立てるので、容量(`ModelRecipe.params`)を
渡す口が無い。ここは凍結 config の `arms` ブロックから直接 2 アームを組む専用ドライバである。
確認モードの検査(hash・窓・contract version)は CLI と同じ `assert_confirmatory` を通す。

使い方:
    cd training && uv run python ../scripts/capacity_confirm.py \
        --gate-config ../specs/094-booster-capacity/gate-config.json \
        --gate-config-hash $(cat ../specs/094-booster-capacity/gate-config.hash.txt) \
        --json ../out/094-capacity-verdict.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import time

from horseracing_db.session import create_db_engine
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.decision import assert_confirmatory
from horseracing_eval.paired import paired_eval
from sqlalchemy.orm import Session

from horseracing_training.calib_split import CalibSplitFactory
from horseracing_training.recipe import ModelRecipe

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
VERDICT_KIND = "full_walk_forward"


def _arm(session, cfg: dict, rounds: int) -> CalibSplitFactory:
    a = cfg["arms"]
    recipe = ModelRecipe(
        objective=a["objective"], calibration="none", calib_frac=0.0, seed=int(a["seed"]),
        params=(("n_estimators", int(rounds)),),
        label=f"094:armE:rounds={rounds}",
    )
    return CalibSplitFactory(session, recipe, n_oof_blocks=int(a["n_oof_blocks"]),
                             method="isotonic", require_sufficient=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate-config", required=True)
    ap.add_argument("--gate-config-hash", required=True)
    ap.add_argument("--json", dest="json_out", required=True)
    args = ap.parse_args()

    cfg = json.loads(open(args.gate_config).read())
    win = cfg["eval_window"]
    # CLI と同じ fail-closed(hash 欠落・窓欠落・窓不一致・contract version)
    assert_confirmatory(cfg, expected_hash=args.gate_config_hash,
                        eval_window={"from": win["from"], "to": win["to"]})
    print(f"confirmatory OK  window={win['from']}..{win['to']}  hash={args.gate_config_hash[:12]}")

    d_from = datetime.date.fromisoformat(win["from"])
    d_to = datetime.date.fromisoformat(win["to"])
    det = cfg.get("determinism") or {}
    boot = cfg.get("bootstrap") or {}

    engine = create_db_engine(DB)
    with Session(engine) as session:
        # 学習側は年単位(< 検証年)、採点側は valid_from で日付ちょうどに絞る
        races = load_eval_races(session, end_date=d_to)
        cand = _arm(session, cfg, cfg["arms"]["candidate_n_estimators"])
        act = _arm(session, cfg, cfg["arms"]["active_n_estimators"])
        t0 = time.time()
        rep = paired_eval(
            cand, act, races,
            gate_config=cfg,
            first_valid_year=d_from.year,
            valid_from=d_from,
            bootstrap_seed=int(boot.get("seed", 20260818)),
            bootstrap_b=int(boot.get("b", 2000)),
            num_threads=int(det.get("num_threads", 1)),
            subgroups=True,
            snapshot={"driver": "capacity_confirm", "gate_config_hash": args.gate_config_hash,
                      "candidate_rounds": cfg["arms"]["candidate_n_estimators"],
                      "active_rounds": cfg["arms"]["active_n_estimators"]},
        )
        print(f"  完了 {time.time()-t0:.0f}s")

    d = rep.to_dict()
    d["artifact_kind"] = VERDICT_KIND
    d["eligible_for_verdict"] = True
    with open(args.json_out, "w") as fh:
        json.dump(d, fh, indent=2, default=str)

    ci = rep.bootstrap_ci
    g = rep.gate
    print(f"\n  n_races={rep.n_races:,}  n_days={ci['n_days']}")
    print(f"  winner NLL diff = {ci['point']:+.6f}  CI[{ci['ci_low']:+.6f}, {ci['ci_high']:+.6f}]")
    print(f"  gate: primary={g.primary} stat={g.stat_guard} recent={g.recent_guard} "
          f"top={g.top_noninferior} calib={g.calibration} -> adopted={g.adopted}")
    print(f"  DECISION = {rep.decision}  ({rep.decision_reason.get('cause')})")
    print(f"  wrote {args.json_out}")
    print("\n  注: この窓には現行レジームのデータが無い。昇格を検討する際は "
          "gate-config の current_regime_evidence の限界を併記すること。")


if __name__ == "__main__":
    main()
