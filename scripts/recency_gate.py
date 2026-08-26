"""Feature 101 US1: 時間重みあり／なしを凍結済み条件で paired 判定する。

この driver は ``specs/101-recency-weighting/gate-config.json`` を正本として読み、隣の
``gate-config.hash.txt`` と canonical hash が一致したときだけ学習を始める。アームの差は候補の
recency 設定だけに限定し、評価は将来レース当たりの平均 winner NLL という estimand を守るため
無重みで行う。

実行例::

    uv run --project training python scripts/recency_gate.py \
        --database-url postgresql+psycopg://... \
        --json specs/101-recency-weighting/evidence/recency-gate.json \
        --evidence specs/101-recency-weighting/evidence/recency-per-race.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from dataclasses import fields
from pathlib import Path

from horseracing_db.session import create_db_engine
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.decision import (
    EVALUATION_CONTRACT_VERSION,
    ConfirmatoryContractError,
    assert_confirmatory,
    gate_config_hash,
)
from horseracing_eval.evidence import EvidenceContractError, write as write_evidence
from horseracing_eval.paired import paired_eval
from sqlalchemy.orm import Session

from horseracing_training.cli import _factory_from_spec
from horseracing_training.recipe import ModelRecipe

REPO = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO / "specs/101-recency-weighting/gate-config.json"
HASH_PATH = REPO / "specs/101-recency-weighting/gate-config.hash.txt"
DEFAULT_JSON_PATH = REPO / "specs/101-recency-weighting/evidence/recency-gate.json"
ARTIFACT_KIND = "full_walk_forward"
ARM_SPEC = "pl_topk:oof_isotonic"

# ``weight_scope`` は時間重みの適用範囲を宣言する監査キーで、半減期と不可分である。
# この 2 キー以外に差があれば、recency の効果ではないものを測ってしまうので開始前に止める。
RECENCY_META_KEYS = frozenset({"recency_half_life_days", "weight_scope"})


def _date(value: str) -> dt.date:
    """CLI の日付を早期に検証し、DB 接続後の失敗を避ける。"""
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"ISO 日付ではありません: {value!r}") from exc


def _positive_int(value: str) -> int:
    """スレッド数 0 以下を LightGBM まで運ばない。"""
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("1 以上を指定してください")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Feature 101 US1 の凍結済み recency paired gate を実行する",
    )
    parser.add_argument(
        "--from",
        dest="date_from",
        type=_date,
        help="評価開始日（既定: gate-config。確認実行では凍結値との一致が必須）",
    )
    parser.add_argument(
        "--to",
        dest="date_to",
        type=_date,
        help="評価終了日（既定: gate-config。確認実行では凍結値との一致が必須）",
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        type=Path,
        default=DEFAULT_JSON_PATH,
        help=f"verdict JSON（既定: {DEFAULT_JSON_PATH}）",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        help="per-race 証拠の保存先（append-only。既存パスは拒否）",
    )
    parser.add_argument(
        "--use-materialized",
        action="store_true",
        help="live DB ではなく --materialized-path の学習行列を使う",
    )
    parser.add_argument(
        "--materialized-path",
        type=Path,
        help="両アームが共有して読む materialized parquet",
    )
    parser.add_argument("--database-url", help="DATABASE_URL 環境変数を上書きする")
    parser.add_argument(
        "--num-threads",
        type=_positive_int,
        default=1,
        help="各 fit の LightGBM スレッド数（既定: 1）",
    )
    return parser


def _load_frozen_config() -> tuple[dict, str]:
    """config と記録済み hash を読み、凍結後の変更を最初に拒否する。"""
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        expected_hash = HASH_PATH.read_text().strip()
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"凍結 gate-config を読めません: {exc}") from exc
    if not expected_hash:
        raise SystemExit(f"凍結 hash が空です: {HASH_PATH}")

    actual_hash = gate_config_hash(cfg)
    if actual_hash != expected_hash:
        raise SystemExit(
            "gate-config hash mismatch: 凍結後に config が変わっています。"
            f" expected={expected_hash} actual={actual_hash}。判定は実行しません。"
        )
    return cfg, expected_hash


def _resolve_window(
    args: argparse.Namespace,
    cfg: dict,
    expected_hash: str,
) -> tuple[dt.date, dt.date]:
    """省略時は凍結窓を使い、明示値も凍結窓から動かせないようにする。"""
    window = cfg["eval_window"]
    date_from = args.date_from or _date(window["from"])
    date_to = args.date_to or _date(window["to"])
    if date_from > date_to:
        raise SystemExit(f"評価窓が逆転しています: {date_from}..{date_to}")
    try:
        assert_confirmatory(
            cfg,
            expected_hash=expected_hash,
            eval_window={"from": date_from.isoformat(), "to": date_to.isoformat()},
        )
    except ConfirmatoryContractError as exc:
        raise SystemExit(f"凍結済み確認契約を満たしません: {exc}") from exc
    return date_from, date_to


def _assert_cli_inputs(args: argparse.Namespace) -> None:
    """数時間の fit を始める前に、出力と materialized 入力の曖昧さを除く。"""
    if args.use_materialized and args.materialized_path is None:
        raise SystemExit("--use-materialized には --materialized-path が必須です")
    if args.materialized_path is not None and not args.use_materialized:
        raise SystemExit("--materialized-path を使うには --use-materialized を指定してください")
    if args.evidence is not None and args.evidence.exists():
        raise SystemExit(
            f"{args.evidence} は既に存在します。per-race 証拠は append-only なので上書きしません。"
        )
    if args.evidence is not None and args.evidence.resolve() == args.json_out.resolve():
        raise SystemExit("--json と --evidence には別のパスを指定してください")


def _require_recency_recipe_fields() -> None:
    """別担当の recipe 配線が未反映なら、属性エラーより先に原因を説明して止める。"""
    available = {field.name for field in fields(ModelRecipe)}
    missing = RECENCY_META_KEYS - available
    if missing:
        raise SystemExit(
            "ModelRecipe の recency 配線がまだ利用できません。"
            f"不足フィールド={sorted(missing)}。別担当の feature 101 実装を反映してから再実行してください。"
        )


def _arm_overrides(arms: dict) -> dict:
    """凍結した共通 pin を、両アームへ同じ形で明示的に渡す。"""
    required = (
        "n_estimators",
        "n_oof_blocks",
        "weight_mask_rate",
        "weight_mask_seed",
    )
    if missing := [key for key in required if key not in arms]:
        raise SystemExit(f"gate-config arms に凍結 pin が不足しています: {missing}")

    # 現行 recipe の seed=42 は既存本番アームの一部。古い config は arms.seed を持たないため、
    # ModelRecipe の既定値を明示 override に昇格し、候補／基準で同じ値を必ず運ぶ。
    model_seed = int(arms.get("seed", ModelRecipe().seed))
    return {
        "n_estimators": int(arms["n_estimators"]),
        "n_oof_blocks": int(arms["n_oof_blocks"]),
        "weight_mask_rate": float(arms["weight_mask_rate"]),
        "weight_mask_seed": int(arms["weight_mask_seed"]),
        "seed": model_seed,
    }


def _build_arms(session: Session, cfg: dict, args: argparse.Namespace):
    """factory の正規経路で、recency 以外が同一の 2 アームを作る。"""
    _require_recency_recipe_fields()
    arms = cfg["arms"]
    if arms.get("objective") != "pl_topk" or arms.get("arm") != "oof_isotonic":
        raise SystemExit(
            "凍結 arms は pl_topk:oof_isotonic でなければなりません: "
            f"objective={arms.get('objective')!r}, arm={arms.get('arm')!r}"
        )
    if arms.get("active_recency") is not None:
        raise SystemExit("基準アームの active_recency は null でなければなりません")

    common = _arm_overrides(arms)
    half_life = arms.get("candidate_recency_half_life_days")
    if half_life is None:
        raise SystemExit("候補アームの candidate_recency_half_life_days が凍結されていません")
    scope = arms.get("weight_scope")
    if scope is None:
        raise SystemExit("候補アームの weight_scope が凍結されていません")

    factory_kwargs = {
        "use_materialized": args.use_materialized,
        "materialized_path": (
            str(args.materialized_path) if args.materialized_path is not None else None
        ),
        # materialized を選んだ目的は入力 snapshot の固定なので、DB 更新を理由に拒否しない。
        "pin_snapshot": args.use_materialized,
    }
    candidate = _factory_from_spec(
        session,
        ARM_SPEC,
        arm_overrides={
            **common,
            "recency_half_life_days": float(half_life),
            "weight_scope": scope,
        },
        **factory_kwargs,
    )
    active = _factory_from_spec(
        session,
        ARM_SPEC,
        arm_overrides={
            **common,
            "recency_half_life_days": None,
            # 適用範囲の宣言も凍結 pin の一部なので両アームで揃える。基準は半減期が None のため
            # 実際の時間重みを持たず、実効差は recency_half_life_days だけになる。
            "weight_scope": scope,
        },
        **factory_kwargs,
    )
    _assert_arm_structure(candidate, active, arms, common)
    return candidate, active


def _assert_arm_structure(candidate, active, arms: dict, common: dict) -> None:
    """実効 recipe が凍結 pin と一致し、recency 以外に差が無いことを検査する。"""
    candidate_meta = dict(candidate.recipe_meta)
    active_meta = dict(active.recipe_meta)

    def problems(meta: dict, *, role: str) -> list[str]:
        params = dict(meta.get("params") or ())
        expected_recency = (
            float(arms["candidate_recency_half_life_days"]) if role == "candidate" else None
        )
        expected_scope = arms["weight_scope"]
        checks = {
            "objective": (meta.get("objective"), arms["objective"]),
            "arm": (meta.get("arm"), arms["arm"]),
            "n_estimators": (params.get("n_estimators"), common["n_estimators"]),
            "n_oof_blocks": (meta.get("n_oof_blocks"), common["n_oof_blocks"]),
            "weight_mask_rate": (meta.get("weight_mask_rate"), common["weight_mask_rate"]),
            "weight_mask_seed": (meta.get("weight_mask_seed"), common["weight_mask_seed"]),
            "seed": (meta.get("seed"), common["seed"]),
            "recency_half_life_days": (
                meta.get("recency_half_life_days"),
                expected_recency,
            ),
            "weight_scope": (meta.get("weight_scope"), expected_scope),
        }
        return [
            f"{key}={actual!r} != frozen {expected!r}"
            for key, (actual, expected) in checks.items()
            if actual != expected
        ]

    mismatches = [
        *(f"candidate: {item}" for item in problems(candidate_meta, role="candidate")),
        *(f"active: {item}" for item in problems(active_meta, role="active")),
    ]
    if mismatches:
        raise SystemExit(
            "実効 recipe が凍結 arms と一致しません。recency の factory 配線漏れを含むため、"
            f"学習を開始しません: {'; '.join(mismatches)}"
        )

    missing_value = object()
    diff_keys = {
        key
        for key in set(candidate_meta) | set(active_meta)
        if candidate_meta.get(key, missing_value) != active_meta.get(key, missing_value)
    }
    unrelated = diff_keys - RECENCY_META_KEYS
    if unrelated:
        raise SystemExit(
            "両アームに recency 以外の差があります。交絡した比較は実行しません: "
            f"差分キー={sorted(diff_keys)}"
        )
    if diff_keys != {"recency_half_life_days"}:
        raise SystemExit(
            "候補と基準の実効差は recency_half_life_days だけでなければなりません。"
            f"実際の差分キー={sorted(diff_keys)}。factory の override 配線を確認してください。"
        )
    if candidate.recipe_hash == active.recipe_hash:
        raise SystemExit("両アームの recipe_hash が同一です。recency がモデル同一性に入りません")


def _assert_nonzero_per_race_diff(report) -> int:
    """per-race 差の全ゼロを「効果なし」ではなく配線故障として拒否する。

    理論上の不採用は、候補が僅かに悪い／改善幅が閾値へ届かない／CI が広い、という数値として
    現れる。一方、別レシピを多数のレースで再学習したのに全行が *厳密に* 0.0 になるのは、
    recency が booster に届かず両アームが同一モデルになった兆候である。097 では arm E の共有行列が
    drop_features を迂回し、全窓 0.000000 の run 1 を完走してから無効と判明した。この driver で
    全ゼロを「差が無いので REJECT」と保存すると、数時間の故障 run が正式な科学的結論へ化ける。
    したがって verdict／証拠を一切書く前に停止し、配線を直した新しい run だけを判定対象にする。
    """
    evidence = report.evidence
    if evidence is None or not evidence.rows:
        raise SystemExit("per-race evidence が空です。比較を実行できていないため verdict を書きません")
    nonzero = sum(1 for row in evidence.rows if float(row.diff) != 0.0)
    if nonzero == 0:
        raise SystemExit(
            "全 per-race diff が 0.0 です。両アームが同一モデルになった配線故障であり、"
            "『効果なし』という判定結果ではありません。verdict は書きません。"
        )
    return nonzero


def _canonical_verdict(report, cfg: dict) -> dict:
    """評価可能性を確認後、正本の単一式から三値を機械的に作る。"""
    if report.subgroups is None or "subgroup_guard" not in report.subgroups:
        raise SystemExit("subgroup_guard が計算されていません。正本式を評価できません")

    subgroup_guard = bool(report.subgroups["subgroup_guard"])
    gate_adopted = bool(report.gate.adopted)
    formula_result = gate_adopted and subgroup_guard
    n_days = int(report.bootstrap_ci["n_days"])
    min_days = int(cfg["eval_window"]["min_eval_days"])

    if n_days < min_days:
        status = "NO_DECISION"
        cause = f"insufficient_eval_days({n_days}<{min_days})"
    elif formula_result:
        status = "ADOPT"
        cause = "gate.adopted AND subgroup_guard"
    else:
        status = "REJECT"
        failed = [
            name
            for name, passed in (
                ("gate.adopted", gate_adopted),
                ("subgroup_guard", subgroup_guard),
            )
            if not passed
        ]
        cause = "canonical_formula_false: " + ", ".join(failed)

    # harness の三値は監査用に残すが、088 で既に乖離した前例があるため上の式を上書きさせない。
    return {
        "status": status,
        "adopt": status == "ADOPT",
        "formula": "gate.adopted AND subgroup_guard",
        "formula_result": formula_result,
        "reason": {
            "cause": cause,
            "n_days": n_days,
            "min_eval_days": min_days,
            "gate_adopted": gate_adopted,
            "subgroup_guard": subgroup_guard,
        },
        "harness": {
            "decision": report.decision,
            "decision_reason": report.decision_reason,
            "disagrees_with_canonical": report.decision != status,
            "authority": "reference_only; canonical formula above is authoritative",
        },
    }


def _artifact(
    report,
    *,
    cfg: dict,
    gate_hash: str,
    date_from: dt.date,
    date_to: dt.date,
    args: argparse.Namespace,
    nonzero_races: int,
    elapsed_s: float,
) -> dict:
    """巨大な per-race 行を分離し、レビュー可能な verdict 正本を組み立てる。"""
    gate_reasons = dict(report.gate.reasons)
    sub_gates = gate_reasons.pop("sub_gates", {})
    return {
        "artifact_kind": ARTIFACT_KIND,
        "eligible_for_verdict": True,
        "evaluation_contract_version": EVALUATION_CONTRACT_VERSION,
        "gate_config_hash": gate_hash,
        "window": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "evaluation_weighting": "unweighted",
        "evaluation_weighting_note": (
            "paired_eval はレースを時間重み付けせず評価する。訓練だけに recency を適用し、"
            "将来レース当たり平均 winner NLL という estimand を維持する。"
        ),
        "n_races": report.n_races,
        "n_eligible": report.n_eligible,
        "primary": {
            "point": report.bootstrap_ci["point"],
            "sample_ci": report.bootstrap_ci,
            "total_ci": report.total_ci,
        },
        "gate": {
            "adopted": report.gate.adopted,
            "sub_gates": sub_gates,
            "reasons": gate_reasons,
        },
        "subgroups": report.subgroups,
        "verdict": _canonical_verdict(report, cfg),
        "arms": {
            "candidate": {
                "recipe_hash": report.candidate_recipe_hash,
                "recipe_meta": report.candidate_recipe_meta,
            },
            "active": {
                "recipe_hash": report.active_recipe_hash,
                "recipe_meta": report.active_recipe_meta,
            },
        },
        "structure_checks": {
            "pre_run_only_recency_diff": True,
            "post_run_nonzero_diff_races": nonzero_races,
        },
        "per_race_evidence": {
            "path": str(args.evidence) if args.evidence is not None else None,
            "append_only": True,
            "written": args.evidence is not None,
        },
        "run": {
            "elapsed_s": round(elapsed_s, 3),
            "num_threads": args.num_threads,
            "use_materialized": args.use_materialized,
            "materialized_path": (
                str(args.materialized_path) if args.materialized_path is not None else None
            ),
        },
    }


def main() -> int:
    args = _parser().parse_args()
    _assert_cli_inputs(args)
    cfg, expected_hash = _load_frozen_config()
    date_from, date_to = _resolve_window(args, cfg, expected_hash)
    bootstrap = cfg["bootstrap"]

    print(
        f"contract {EVALUATION_CONTRACT_VERSION} OK  hash={expected_hash[:12]}  "
        f"window={date_from}..{date_to}",
        flush=True,
    )
    print("evaluation weighting: unweighted (paired_eval の既定 estimand)", flush=True)

    engine = create_db_engine(args.database_url)
    started = time.monotonic()
    with Session(engine) as session:
        # --from は評価開始であり学習履歴の下限ではない。全 prior history を残したまま、
        # valid_from と first_valid_year だけで採点窓を切る。
        eval_races = load_eval_races(session, start_date=None, end_date=date_to)
        candidate, active = _build_arms(session, cfg, args)
        print(
            f"arms OK  candidate={candidate.recipe_hash[:12]} active={active.recipe_hash[:12]}",
            flush=True,
        )
        report = paired_eval(
            candidate,
            active,
            eval_races,
            gate_config=cfg,
            subgroups=True,
            first_valid_year=date_from.year,
            valid_from=date_from,
            bootstrap_seed=int(bootstrap["seed"]),
            bootstrap_b=int(bootstrap["b"]),
            num_threads=args.num_threads,
            snapshot={
                "driver": "recency_gate",
                "gate_config_hash": expected_hash,
                "evaluation_weighting": "unweighted",
                "use_materialized": args.use_materialized,
                "materialized_path": (
                    str(args.materialized_path) if args.materialized_path is not None else None
                ),
            },
        )

    nonzero_races = _assert_nonzero_per_race_diff(report)
    artifact = _artifact(
        report,
        cfg=cfg,
        gate_hash=expected_hash,
        date_from=date_from,
        date_to=date_to,
        args=args,
        nonzero_races=nonzero_races,
        elapsed_s=time.monotonic() - started,
    )

    if args.evidence is not None:
        try:
            write_evidence(report.evidence, args.evidence)
        except EvidenceContractError as exc:
            raise SystemExit(f"per-race 証拠を書けません: {exc}") from exc
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, default=str) + "\n"
    )

    primary = artifact["primary"]
    verdict = artifact["verdict"]
    print(
        f"primary={primary['point']:+.6f} "
        f"sample CI[{primary['sample_ci']['ci_low']:+.6f},"
        f"{primary['sample_ci']['ci_high']:+.6f}] "
        f"total CI[{primary['total_ci']['ci_low']:+.6f},"
        f"{primary['total_ci']['ci_high']:+.6f}]",
        flush=True,
    )
    print(
        f"VERDICT={verdict['status']} ({verdict['reason']['cause']})  wrote {args.json_out}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
