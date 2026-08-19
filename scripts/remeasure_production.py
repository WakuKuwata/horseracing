"""095 現行本番の再測定 — evaluation contract v4 で「今日の 2 つの昇格が何を買ったか」を測る。

**これは採用判定ではない。** 本番は既に稼働しており、この実行では何も昇格しない。目的は 1 つ、
*いま本番について話すときに使える数値*を作ること。今日までに手元にある数値はすべて旧契約下のもの:

    arm E     -0.01284   contract v2   窓 2008-2026   (recent guard は壊れた版、seed ノイズ未計上)
    容量 900  -0.00670   contract v3   窓 2019-2024   (現行レジームを含まない)

契約も窓も違うので足せない。両者を合わせた効果は一度も測られていない。ここで 1 つの窓・1 つの
契約で測り直す。

``capacity_confirm`` との違いは**アームの種類が混在する**こと。候補はいま動いているもの
(arm E = 全履歴 booster + strict-past OOF isotonic, rounds 900)、現行は今日より前のもの
(train 内 holdout isotonic 0.3, rounds 300)。前者は CalibSplitFactory、後者は RecipeFactory で、
paired-eval の recipe spec 文字列ではこの組み合わせに容量を渡す口が無い。

両アームとも live DB から特徴を読む。CalibSplitFactory が materialized 非対応なので、片方だけ
parquet にすると入力源の違う比較になってしまう。DB は実行中も動く(ops が取込を続ける)が、各
アームは初回 fit で行列を 1 度だけ作るため、ずれは 2 アームの開始時刻の差(数分)に限られ、実測
された drift の規模は 5e-5 — 期待効果 -0.019 に対して無視できる。記録は残す。
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
from horseracing_training.recipe import ModelRecipe, RecipeFactory
from horseracing_training.win_model import DEFAULT_PARAMS

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
ARTIFACT_KIND = "remeasurement"   # verdict loader は full_walk_forward しか受けない = 昇格に使えない


def _params(rounds: int) -> tuple[tuple[str, int], ...] | None:
    """rounds が既定値そのものなら ``params=None`` にする。

    ``params`` は None のとき recipe_hash から外れる。既定値を明示的に渡すと*モデルは同一なのに*
    hash だけが変わり、実在した過去のレシピと別物に見えてしまう。現行アームは既定のまま学習された
    ので、その同一性を保つ。"""
    return None if rounds == int(DEFAULT_PARAMS["n_estimators"]) else (("n_estimators", int(rounds)),)


def _recipe(a: dict, rounds: int, calibration: str, calib_frac: float, label: str) -> ModelRecipe:
    return ModelRecipe(
        objective=a["objective"], calibration=calibration, calib_frac=calib_frac,
        seed=int(a["seed"]), params=_params(rounds),
        weight_mask_rate=a.get("weight_mask_rate"), weight_mask_seed=a.get("weight_mask_seed"),
        label=label,
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
    with Session(engine) as session:
        races = load_eval_races(session, end_date=d_to)
        cand_rounds = int(a["candidate_n_estimators"])
        act_rounds = int(a["active_n_estimators"])

        cand = CalibSplitFactory(
            session,
            _recipe(a, cand_rounds, "none", 0.0, f"095:armE:rounds={cand_rounds}"),
            n_oof_blocks=int(a["candidate_n_oof_blocks"]), method="isotonic",
            require_sufficient=True,
        )
        act = RecipeFactory(
            session,
            _recipe(a, act_rounds, "isotonic", float(a["active_calib_frac"]),
                    f"095:holdout:rounds={act_rounds}"),
        )
        print(f"  候補 = {cand.recipe_hash[:12]}  現行 = {act.recipe_hash[:12]}")

        t0 = time.time()
        rep = paired_eval(
            cand, act, races,
            gate_config=cfg,
            first_valid_year=d_from.year, valid_from=d_from,
            bootstrap_seed=int(boot.get("seed", 20260818)),
            bootstrap_b=int(boot.get("b", 2000)),
            num_threads=int(det.get("num_threads", 1)),
            subgroups=True,
            snapshot={
                "driver": "remeasure_production",
                "gate_config_hash": args.gate_config_hash,
                "purpose": "remeasurement_not_adoption",
                "candidate": {"arm": "oof_isotonic", "rounds": cand_rounds,
                              "n_oof_blocks": int(a["candidate_n_oof_blocks"])},
                "active": {"arm": "holdout_isotonic", "rounds": act_rounds,
                           "calib_frac": float(a["active_calib_frac"])},
                "feature_source": "live_db_both_arms",
            },
        )
        print(f"  完了 {time.time()-t0:.0f}s")

    d = rep.to_dict()
    d["artifact_kind"] = ARTIFACT_KIND
    d["eligible_for_verdict"] = False   # 昇格には使えない — 再測定であって判定ではない
    with open(args.json_out, "w") as fh:
        json.dump(d, fh, indent=2, default=str)

    ci, tot = rep.bootstrap_ci, (rep.total_ci or {})
    print(f"\n  n_races={rep.n_races:,}  n_days={ci['n_days']}")
    print(f"  winner NLL diff = {ci['point']:+.6f}")
    print(f"    標本のみ    CI[{ci['ci_low']:+.6f}, {ci['ci_high']:+.6f}]")
    if tot:
        print(f"    +再学習分散 CI[{tot.get('ci_low'):+.6f}, {tot.get('ci_high'):+.6f}]  (v4 判定区間)")
    print(f"  wrote {args.json_out}")


if __name__ == "__main__":
    main()
