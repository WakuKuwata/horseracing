"""ACTIVE の切り替え(単一 active 不変条件 + 記録される override + rollback)。

なぜ要るか: active を書き込むのは `save_model_version` だけで、そこは v3 verdict を要求する
(d90231a)。`set-model-label` は adoption_status を触らない設計。つまり「confirmatory verdict が
まだ無い候補を、判断として昇格させる」経路が存在せず、生 SQL で回避されるしかなかった。
**回避されるくらいなら、記録される override 経路を用意する方が安全**(039 は機械ゲート False を
手動昇格し、060 は `user_override=True` を adoption.reasons に記録した前例)。

このモジュールがやること:
  1. 昇格前の実在確認(artifact ファイル・feature_hash が serving の exact/compat 経路に乗るか)
  2. 単一 active 不変条件(同一トランザクションで現行を candidate に降格)
  3. 何を根拠に昇格したかを両方の行に記録(override なら理由の文字列を必須にする)
  4. rollback コマンドを出力(戻すのが 1 コマンドであることを保証する)

判定そのものはしない。v3 verdict があれば `adoption.evaluate_promotion` に通し、無ければ
override として扱う。**override は隠さず metrics_summary に残す。**
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from horseracing_db.enums import AdoptionStatus
from horseracing_db.models import ModelVersion
from sqlalchemy import select
from sqlalchemy.orm import Session

from .adoption import AdoptionDecision, evaluate_promotion


class PromoteError(RuntimeError):
    """昇格を実行できない(fail-closed)。"""


@dataclass
class PromotePlan:
    model_version: str
    previous_active: str | None
    basis: str                      # "v3_verdict" | "override"
    override_reason: str | None
    verdict_summary: dict | None
    problems: list[str] = field(default_factory=list)
    rollback_command: str = ""

    @property
    def ok(self) -> bool:
        return not self.problems


def _artifact_problems(row: ModelVersion, *, current_fv: str) -> list[str]:
    """serving が実際に要求するものを、serving を import せずに構造的に確認する。

    完全な等価ではない(serving は preprocessor の中身も照合する)。ここで落とせるのは
    「ファイルが無い」「feature schema が合わない」という、切り替えた瞬間に本番の予測が
    止まる類の事故([[model-artifact-outlived-by-row]] の実例)。
    """
    from horseracing_features.registry import (
        is_feature_version_servable,
        model_input_features,
    )

    from .artifacts import feature_hash

    problems: list[str] = []
    for label, uri in (("weights_uri", row.weights_uri), ("calibrator_uri", row.calibrator_uri)):
        if not uri:
            problems.append(f"{label} が空")
            continue
        # 絶対パスかどうかは存在確認とは独立に見る。相対 URI は「この cwd では解決できた」だけで
        # 通ってしまい、ops predict(cwd=serving)で落ちる([[weights-uri-relative-path-ops-bug]])。
        if not Path(uri).is_absolute():
            problems.append(f"{label} が相対パス(ops predict は cwd=serving で動く): {uri}")
        if not Path(uri).exists():
            problems.append(f"{label} のファイルが実在しない: {uri}")

    meta_path = Path(row.weights_uri).parent / "metadata.json" if row.weights_uri else None
    if meta_path is None or not meta_path.exists():
        problems.append("metadata.json が無い")
        return problems
    meta = json.loads(meta_path.read_text())
    trained_fv = meta.get("feature_version")
    trained_hash = meta.get("feature_hash")
    current_hash = feature_hash(model_input_features())
    if trained_hash == current_hash:
        return problems  # serving の exact 経路
    if is_feature_version_servable(trained_fv, trained_hash, current_fv):
        return problems  # serving の compat 経路(pin 済み)
    problems.append(
        f"feature schema が serving に乗らない: trained {trained_fv}/{(trained_hash or '')[:12]} "
        f"vs current {current_fv}/{current_hash[:12]}(exact でも compat pin でもない)"
    )
    return problems


def plan_promotion(
    session: Session,
    *,
    model_version: str,
    override_reason: str | None = None,
    verdict: dict | None = None,
    current_fv: str,
) -> PromotePlan:
    """昇格計画を組み、実行可能かを判定する。書き込みはしない。"""
    row = session.get(ModelVersion, model_version)
    if row is None:
        raise PromoteError(f"model_versions に {model_version!r} が無い")

    current = session.execute(
        select(ModelVersion.model_version).where(
            ModelVersion.adoption_status == AdoptionStatus.ACTIVE
        )
    ).scalars().all()
    if len(current) > 1:
        raise PromoteError(f"active が複数ある(単一 active 不変条件の破れ): {current}")
    previous = current[0] if current else None

    # v3 verdict があるなら正規の経路で判定する。legacy 側は「この候補は既に登録済み」なので
    # adopted=True 相当として渡し、v3 側の条件だけを見る。
    decision = evaluate_promotion(
        legacy=AdoptionDecision(adopted=True, reasons={"source": "promote-model"}),
        verdict=verdict,
    )
    if decision.promotable:
        basis, reason = "v3_verdict", None
    else:
        if not override_reason:
            raise PromoteError(
                "v3 verdict が昇格要件を満たさないため override が必要: "
                f"{decision.reasons.get('cause')}。--override-reason に理由を書くこと"
                "(理由は metrics_summary に残る)"
            )
        basis, reason = "override", override_reason

    plan = PromotePlan(
        model_version=model_version,
        previous_active=previous,
        basis=basis,
        override_reason=reason,
        verdict_summary=decision.reasons.get("v3_verdict"),
        problems=_artifact_problems(row, current_fv=current_fv),
    )
    if previous:
        plan.rollback_command = (
            "uv run python -m horseracing_training promote-model "
            f"--model-version {previous} --override-reason 'rollback of {model_version}' --apply"
        )
    return plan


def merged_promotion_record(prior: dict | None, record: dict) -> dict:
    """昇格時の記録を、登録時の記録の上に**置き換えて**組み立てる。

    以前はここが単純なマージだったので、登録時に `save_model_version` が書いた
    `reasons: {"cause": "register_as_candidate_requested"}` が生き残り、昇格後の行に
    `basis: "v3_verdict"` と並んで載っていた。昇格そのものは正しく行われていても、
    監査欄としては 2 つの矛盾する根拠が並ぶ。

    登録時の判定は昇格の根拠ではないので、別キー `registration` に退避する。再昇格しても
    入れ子にならないよう、既に退避済みならそれをそのまま保つ。
    """
    prior = dict(prior or {})
    promotion_keys = set(record) | {"promotable", "status"}
    registration = prior.get("registration") or {
        k: v for k, v in prior.items() if k not in promotion_keys
    }
    out = {**record, "promotable": True, "status": "active"}
    if registration:
        out["registration"] = registration
    return out


def apply_promotion(
    session: Session, plan: PromotePlan, *, at: str, git_sha: str | None = None
) -> dict:
    """単一トランザクションで active を切り替え、両方の行に根拠を記録する。"""
    if not plan.ok:
        raise PromoteError(f"昇格前確認が通っていない: {plan.problems}")

    record = {
        "promoted_at": at,
        "basis": plan.basis,
        "override_reason": plan.override_reason,
        "v3_verdict": plan.verdict_summary,
        "previous_active": plan.previous_active,
        "git_sha": git_sha,
        "rollback_command": plan.rollback_command,
    }
    target = session.get(ModelVersion, plan.model_version)
    summary = dict(target.metrics_summary or {})
    summary["promotion"] = merged_promotion_record(summary.get("promotion"), record)
    target.metrics_summary = summary
    target.adoption_status = str(AdoptionStatus.ACTIVE)

    if plan.previous_active and plan.previous_active != plan.model_version:
        prev = session.get(ModelVersion, plan.previous_active)
        prev_summary = dict(prev.metrics_summary or {})
        prev_summary["promotion"] = {
            **(prev_summary.get("promotion") or {}),
            "demoted_at": at,
            "superseded_by": plan.model_version,
            "status": "candidate",
            "promotable": False,
        }
        prev.metrics_summary = prev_summary
        prev.adoption_status = str(AdoptionStatus.CANDIDATE)

    session.commit()
    return record
