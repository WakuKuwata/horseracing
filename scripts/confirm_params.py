"""凍結した gate-config の 2 アーム(params だけが違う)を確認モードで回す。

`remeasure_production.py` は rounds だけを差し替える形だったので、任意の LightGBM パラメータを
渡せるように一般化したもの。paired-eval の CLI は recipe spec 文字列で arm を組み立てるため、
`ModelRecipe.params` を渡す口が無い。

確認モードの検査(hash・窓・contract version・seed 分散の宣言)は CLI と同じ `assert_confirmatory`
を通す。成果物は `artifact_kind="full_walk_forward"` で出るので、通れば昇格の根拠になる。
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


def _arm(session, a: dict, params: dict, label: str) -> CalibSplitFactory:
    return CalibSplitFactory(
        session,
        ModelRecipe(
            objective=a["objective"], calibration="none", calib_frac=0.0, seed=int(a["seed"]),
            # dict の順序で hash が変わらないようキーで並べる
            params=tuple(sorted((k, v) for k, v in params.items())),
            weight_mask_rate=a.get("weight_mask_rate"),
            weight_mask_seed=a.get("weight_mask_seed"),
            label=label,
        ),
        n_oof_blocks=int(a["n_oof_blocks"]), method="isotonic", require_sufficient=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate-config", required=True)
    ap.add_argument("--gate-config-hash", required=True)
    ap.add_argument("--json", dest="json_out", required=True)
    args = ap.parse_args()

    cfg = json.loads(open(args.gate_config).read())
    win = cfg["eval_window"]
    assert_confirmatory(cfg, expected_hash=args.gate_config_hash,
                        eval_window={"from": win["from"], "to": win["to"]})
    a = cfg["arms"]
    print(f"contract {cfg['evaluation_contract_version']} OK  "
          f"window={win['from']}..{win['to']}  hash={args.gate_config_hash[:12]}")

    d_from = datetime.date.fromisoformat(win["from"])
    d_to = datetime.date.fromisoformat(win["to"])
    det, boot = cfg.get("determinism") or {}, cfg.get("bootstrap") or {}

    engine = create_db_engine(DB)
    with Session(engine) as s:
        races = load_eval_races(s, end_date=d_to)
        cand = _arm(s, a, a["candidate_params"], "096:candidate")
        act = _arm(s, a, a["active_params"], "096:active")
        print(f"  候補 {a['candidate_params']} = {cand.recipe_hash[:12]}")
        print(f"  現行 {a['active_params']} = {act.recipe_hash[:12]}")
        assert cand.recipe_hash != act.recipe_hash, "2 アームが同一レシピになっている"

        t0 = time.time()
        rep = paired_eval(
            cand, act, races, gate_config=cfg,
            first_valid_year=d_from.year, valid_from=d_from,
            bootstrap_seed=int(boot.get("seed", 20260820)),
            bootstrap_b=int(boot.get("b", 2000)),
            num_threads=int(det.get("num_threads", 1)), subgroups=True,
            snapshot={"driver": "confirm_params", "gate_config_hash": args.gate_config_hash,
                      "candidate_params": a["candidate_params"],
                      "active_params": a["active_params"]},
        )
        print(f"  完了 {time.time()-t0:.0f}s")

    d = rep.to_dict()
    d["artifact_kind"] = "full_walk_forward"
    d["eligible_for_verdict"] = True
    with open(args.json_out, "w") as fh:
        json.dump(d, fh, indent=2, default=str)

    ci, tot, g = rep.bootstrap_ci, (rep.total_ci or {}), rep.gate
    print(f"\n  n_races={rep.n_races:,}  n_days={ci['n_days']}")
    print(f"  winner NLL diff = {ci['point']:+.6f}")
    print(f"    標本のみ    CI[{ci['ci_low']:+.6f}, {ci['ci_high']:+.6f}]")
    if tot:
        print(f"    +再学習分散 CI[{tot.get('ci_low'):+.6f}, {tot.get('ci_high'):+.6f}]  (v4 判定区間)")
    print(f"  gate: primary={g.primary} stat={g.stat_guard} recent={g.recent_guard} "
          f"top={g.top_noninferior} calib={g.calibration} -> adopted={g.adopted}")
    print(f"  DECISION = {rep.decision}  ({rep.decision_reason.get('cause')})")
    print(f"  wrote {args.json_out}")


if __name__ == "__main__":
    main()
