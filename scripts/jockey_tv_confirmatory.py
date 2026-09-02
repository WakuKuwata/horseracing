"""騎手の時変切片の confirmatory driver(feature 107 US1)。

薄い結線のみ: 判定・CI・seed 膨張・証拠・三値 verdict はすべて eval パッケージの公式機構
(`paired_eval` / `final_decision`(report 内)/ `assert_confirmatory` /
`assert_delta_provenance` / `evidence.write`)を使い、一切再実装しない(088/100/106 の教訓)。

候補手続き(W=730 / MIN_RIDES=30 / λ クランプ [10,500])は screening spike
`scripts/jockey_timevarying_spike.py` のクラスを importlib で**同一物として**読み込む —
research D3 は「コピー+定数一致テスト」だったが、コピーは手続きドリフトの余地を残すため
import による構造的同一性に強めた(意図=spike との一致、はそのまま)。driver 側は
カバレッジ計数の instrumentation だけを subclass で足す(手続き不変)。

fail-closed(contracts/confirmatory.md):
  1. assert_confirmatory(cfg, hash, eval_window)  — 契約版 v4 等値・hash・窓・sd_fold
  2. assert_delta_provenance(cfg, root=repo root) — δ の導出参照(root 明示必須・analyze M1)
  3. 窓 to < 2025-01-01(選択済み screening 窓の再利用を構造禁止)
  4. load_eval_races(start_date=FEATURE_POOL_START)(pre-2007 残存 71,549 件の混入防止)
  5. driver 定数 == gate-config 凍結値(== spike 定数は import により恒等)

smoke モード(analyze H3): 縮小 config を受け、採点窓が凍結窓 2022-2024 と互いに素で
あることを assert し、point/sample_ci/total_ci を verdict.json からも redact(構造のみ)・
evidence ファイルを書かない — 「効果数値を出さない」を規律でなく機構にする。

    cd training && nohup uv run python ../scripts/jockey_tv_confirmatory.py \
        --gate-config ../specs/107-jockey-tv-intercept/gate-config.json \
        --gate-config-hash <凍結 hash> \
        --out-dir ../specs/107-jockey-tv-intercept/evidence > ../out/jtv_confirmatory.log 2>&1 &
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import pathlib
import subprocess
import sys
import time

from horseracing_db.session import create_db_engine
from horseracing_db.validation import FEATURE_POOL_START
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.decision import assert_confirmatory
from horseracing_eval.delta_provenance import assert_delta_provenance
from horseracing_eval.evidence import write as write_evidence
from horseracing_eval.paired import paired_eval
from sqlalchemy.orm import Session

from horseracing_training.calib_split import CalibSplitFactory
from horseracing_training.predictor import LightGBMPredictor
from horseracing_training.recipe import ModelRecipe

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCREENING_WINDOW_START = datetime.date(2025, 1, 1)   # 選択に使った窓の開始(再利用禁止)
FROZEN_EVAL_FROM = datetime.date(2022, 1, 1)          # smoke の互いに素 assert 用


def _load_spike_module():
    """screening spike のクラス定義を同一物として読み込む(手続きドリフトの構造的排除)。"""
    path = REPO_ROOT / "scripts" / "jockey_timevarying_spike.py"
    spec = importlib.util.spec_from_file_location("jockey_timevarying_spike", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("jockey_timevarying_spike", mod)
    spec.loader.exec_module(mod)
    return mod


_spike = _load_spike_module()

#: 凍結定数の正本は gate-config。ここでは spike の値を晒し、main() で config と照合する
WINDOW_DAYS = _spike.WINDOW_DAYS
MIN_RIDES = _spike.MIN_RIDES
LAMBDA_CLAMP = (10.0, 500.0)   # spike の estimate_b 内クランプと同値(T003 で三点一致を固定)


def assert_frozen_constants(cfg: dict) -> None:
    """driver/spike の定数 == gate-config の凍結値(不一致は実行前に fail-closed)。"""
    arm = cfg["arms"]["candidate"]
    if arm["window_days"] != WINDOW_DAYS:
        raise SystemExit(f"window_days mismatch: cfg={arm['window_days']} code={WINDOW_DAYS}")
    if arm["min_rides"] != MIN_RIDES:
        raise SystemExit(f"min_rides mismatch: cfg={arm['min_rides']} code={MIN_RIDES}")
    if tuple(arm["lambda_clamp"]) != LAMBDA_CLAMP:
        raise SystemExit(f"lambda_clamp mismatch: cfg={arm['lambda_clamp']} code={LAMBDA_CLAMP}")


class InstrumentedPredictor(_spike.JockeyPooledPredictor):
    """spike の候補手続きそのもの + カバレッジ計数(FR-007)。手続きは一切変えない。"""

    def predict_race(self, race):
        jm = self._jockey_map()
        for hid in [h.horse_id for h in race.started_horses]:
            key = jm.get((race.race_id, hid), _spike.UNKNOWN)
            self.coverage_ = getattr(self, "coverage_", {"mounts": 0, "with_b": 0})
            self.coverage_["mounts"] += 1
            if key in self.b_:
                self.coverage_["with_b"] += 1
        return super().predict_race(race)


def make_candidate_factory(window_days: int):
    class _F(CalibSplitFactory):
        def fit(self, train_races, *, num_threads=None):
            if self._shared is None:
                tmp = LightGBMPredictor(self.session, objective=self.recipe.objective,
                                        calibration="none",
                                        use_materialized=self.use_materialized,
                                        materialized_path=self.materialized_path,
                                        skip_fingerprint_verify=self.pin_snapshot)
                self._shared = tmp._ensure_data()
            if self._pred is None:
                self._pred = InstrumentedPredictor(
                    self.session, self.recipe, shared_data=self._shared,
                    n_oof_blocks=self.n_oof_blocks, method="isotonic",
                    require_sufficient=False)
                self._pred.window_days = window_days
            self._pred.fit(train_races)
            return self._pred
    return _F


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return "unknown"


def _nk_counts(b_info_history: list[dict], pred) -> list[dict]:
    """fold 別診断に nk: 騎手 ID 件数を足す(FR-007a)。b_ は最終 fold のもの。"""
    out = [dict(x) for x in b_info_history]
    if out and getattr(pred, "b_", None):
        out[-1]["n_nk_jockeys_with_b"] = sum(1 for k in pred.b_ if str(k).startswith("nk:"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate-config", required=True)
    ap.add_argument("--gate-config-hash", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--smoke", action="store_true",
                    help="縮小 config での構造検証。効果数値を redact・evidence を書かない")
    ap.add_argument("--num-threads", type=int, default=4)
    args = ap.parse_args()

    with open(args.gate_config) as fh:
        cfg = json.load(fh)

    win = cfg["eval_window"]
    d_from = datetime.date.fromisoformat(win["from"])
    d_to = datetime.date.fromisoformat(win["to"])

    # --- fail-closed(すべて評価前) ---------------------------------------------------
    assert_confirmatory(cfg, expected_hash=args.gate_config_hash,
                        eval_window={"from": win["from"], "to": win["to"]})
    if cfg.get("min_effect_delta") is not None:
        assert_delta_provenance(cfg, root=REPO_ROOT)   # root 明示必須(analyze M1)
    if d_to >= SCREENING_WINDOW_START:
        raise SystemExit(f"eval window to={d_to} は screening 窓(2025+)と重なる — 選択済み"
                         "窓の再利用は禁止(FR-002)")
    if args.smoke and d_to >= FROZEN_EVAL_FROM:
        raise SystemExit(f"smoke 窓 to={d_to} が凍結採点窓(2022-2024)と重なる(analyze H3)")
    assert_frozen_constants(cfg)

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = "smoke-" if args.smoke else ""
    verdict_path = out_dir / f"{tag}verdict.json"
    if verdict_path.exists():
        raise SystemExit(f"{verdict_path} は既に存在する(append-only・再実行は別ディレクトリへ)")

    a_arm = cfg["arms"]["active"]
    engine = create_db_engine(DB)
    with Session(engine) as session:
        races = load_eval_races(session, start_date=FEATURE_POOL_START, end_date=d_to)

        def recipe(label: str) -> ModelRecipe:
            return ModelRecipe(objective=a_arm["objective"], calibration="none",
                               calib_frac=0.0, seed=a_arm["seed"],
                               params=(("n_estimators", a_arm["n_estimators"]),),
                               label=label)

        mat = dict(use_materialized=True,
                   materialized_path=str(REPO_ROOT / "artifacts" / "features.parquet"),
                   pin_snapshot=True)
        cand = make_candidate_factory(WINDOW_DAYS)(
            session, recipe("jtv-confirmatory:candidate"),
            n_oof_blocks=a_arm["n_oof_blocks"], method="isotonic",
            require_sufficient=False, **mat)
        act = CalibSplitFactory(
            session, recipe("jtv-confirmatory:active"),
            n_oof_blocks=a_arm["n_oof_blocks"], method="isotonic",
            require_sufficient=False, **mat)

        t0 = time.time()
        report = paired_eval(
            cand, act, races,
            gate_config=cfg,
            first_valid_year=int(cfg["first_valid_year"]),
            valid_from=d_from,
            bootstrap_seed=int(cfg["bootstrap"]["seed"]),
            bootstrap_b=int(cfg["bootstrap"]["b"]),
            num_threads=args.num_threads,
            snapshot={"git_sha": _git_sha(), "driver": "jockey_tv_confirmatory",
                      "arms": cfg["arms"], "smoke": bool(args.smoke)},
            subgroups=True,
        )
        elapsed = time.time() - t0

        # --- 縮退検査(INV-J3): 効果数値を出す前に ----------------------------------------
        rows = report.evidence.rows if report.evidence is not None else ()
        if not rows or all(r.diff == 0.0 for r in rows):
            raise SystemExit("両アームの予測が完全一致 = b が効いていない縮退(097 型)。"
                             "verdict は書かない — これは配線故障であって結果ではない")

        # --- preflight 監査(FR-007)---------------------------------------------------------
        pred = cand._pred
        cov = getattr(pred, "coverage_", {"mounts": 0, "with_b": 0})
        preflight = {
            "by_fold": _nk_counts(getattr(pred, "b_info_history_", []), pred),
            "eval_mount_coverage": {
                **cov,
                "fraction": (cov["with_b"] / cov["mounts"]) if cov["mounts"] else None,
            },
            "n_eval_races": report.n_races,
            "elapsed_seconds": round(elapsed, 1),
        }
        with open(out_dir / f"{tag}preflight.json", "w") as fh:
            json.dump(preflight, fh, indent=2, ensure_ascii=False)

        verdict = report.to_dict()
        if args.smoke:
            # analyze H3: smoke は構造のみ — 効果数値を機構的に redact
            for k in ("periods", "bootstrap_ci", "total_ci", "subgroups", "evidence",
                      "uniform_baseline_winner_nll", "bootstrap_sensitivity",
                      "decision_reason"):
                if k in verdict:
                    verdict[k] = "REDACTED_SMOKE"
            verdict["gate"] = "REDACTED_SMOKE"
            verdict["decision"] = "SMOKE_STRUCTURE_ONLY"
            print(f"SMOKE OK: 構造完走 {elapsed:.0f}s / races={report.n_races} "
                  f"(効果数値は redact)")
        else:
            write_evidence(report.evidence, out_dir / "paired-evidence.json")
            ci = report.bootstrap_ci
            g = report.gate
            print(f"n_races={report.n_races} n_days={ci['n_days']} elapsed={elapsed:.0f}s")
            print(f"diff={ci['point']:+.6f} sample CI[{ci['ci_low']:+.6f},{ci['ci_high']:+.6f}]")
            tci = verdict.get("total_ci") or {}
            if tci:
                print(f"total CI[{tci.get('ci_low')}, {tci.get('ci_high')}] (seed 膨張込み)")
            print(f"gate: primary={g.primary} stat={g.stat_guard} recent={g.recent_guard} "
                  f"top_ni={g.top_noninferior} calib={g.calibration} -> ADOPTED={g.adopted}")
            print(f"DECISION={report.decision} cause={report.decision_reason.get('cause')} "
                  f"contract={report.evaluation_contract_version} "
                  f"hash={report.gate_config_hash[:12]}")

        with open(verdict_path, "w") as fh:
            json.dump(verdict, fh, indent=2, ensure_ascii=False, default=str)
        print(f"wrote {verdict_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
