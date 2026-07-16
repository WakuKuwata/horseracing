"""Feature 074 US1: generate a recipe-faithful OOF prediction bundle.

Reuses the eval fold machinery (``expanding_folds`` + a per-fold fresh fit) — the same primitive
``foldfit.predict_over_folds`` uses (codex C1: the saved booster is never applied to past races).
Each expanding fold is fit on its outer-train rows only (strict-past) from the recipe-faithful
factory built from the lgbm-063 legacy attestation, and predicts its valid races. The resulting
per-race OOF predictions are serialized into a content-addressed bundle (``oof_bundle``) — NOT a
DB PredictionRun, so the API / serving / model-selector are never polluted (FR-005/FR-017).

Determinism (research D8): ``num_threads=1`` by default for byte-reproducible OOF (FR-006). The
attestation records lgbm-063's declared num_threads; a difference is an explicit fallback the
caller records in the manifest.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from horseracing_eval.dataset import load_eval_races
from horseracing_eval.hashing import stable_hash
from horseracing_eval.splits import expanding_folds
from horseracing_probability import oof_bundle
from horseracing_probability.oof_bundle import race_set_hash
from sqlalchemy.orm import Session

from .legacy_attest import attestation_from_model_dir, recipe_from_attestation
from .recipe import RecipeFactory


def code_sha() -> str:
    """Best-effort git SHA of the working tree (``unknown`` outside a repo)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip() or "unknown"
    except (subprocess.SubprocessError, OSError):  # pragma: no cover - defensive
        return "unknown"


def generate_oof_bundle(
    session: Session,
    *,
    active_dir: Path | str | None = None,
    out_root: Path | str,
    date_from=None,
    date_to=None,
    first_valid_year: int = 2008,
    num_threads: int = 1,
    attestation: dict | None = None,
    factory: RecipeFactory | None = None,
    attestation_digest: str | None = None,
) -> tuple[Path, dict]:
    """Generate and publish the recipe-faithful OOF bundle for the attested base model.

    Returns ``(bundle_path, payload)``. Idempotent: re-generating identical content re-publishes
    the same content-addressed artifact.

    Normal use builds the factory from the base model's legacy attestation (``active_dir`` or an
    explicit ``attestation``). Tests may inject a pre-built ``factory`` + ``attestation_digest`` to
    exercise the OOF *mechanism* (strict-past / determinism / result-invariance) with a fast recipe,
    independent of the base model's exact feature version.
    """
    if factory is None:
        att = attestation or attestation_from_model_dir(active_dir, code_sha=code_sha())
        recipe = recipe_from_attestation(att)
        factory = RecipeFactory(session=session, recipe=recipe)
        attestation_digest = att["attestation_digest"]
    elif attestation_digest is None:
        attestation_digest = "injected-factory"

    eval_races = load_eval_races(session, start_date=date_from, end_date=date_to)

    predictions: dict[str, dict[str, dict[str, float]]] = {}
    per_fold: list[dict] = []
    fold_boundaries: list[int] = []
    for fold in expanding_folds(eval_races, first_valid_year):
        # strict-past: fit on outer-train rows only (all with race_date < the fold's valid year).
        predictor = factory.fit([er.context for er in fold.train], num_threads=num_threads)
        train_ids = [er.context.race_id for er in fold.train]
        valid_ids = [er.context.race_id for er in fold.valid]
        train_through = max((er.context.race_date for er in fold.train), default=None)
        train_hash = race_set_hash(train_ids)
        for er in fold.valid:
            race_preds = predictor.predict_race(er.context)
            predictions[er.context.race_id] = {
                hid: {"win": float(p.win), "top2": float(p.top2), "top3": float(p.top3)}
                for hid, p in race_preds.items()
            }
        fold_boundaries.append(int(fold.valid_year))
        per_fold.append({
            "valid_year": int(fold.valid_year),
            "train_race_set_hash": train_hash,
            "valid_race_set_hash": race_set_hash(valid_ids),
            "train_through": str(train_through) if train_through is not None else "none",
            # per-fold model identity: same recipe, distinct train set => distinct fresh fit.
            "model_digest": stable_hash({"recipe": factory.recipe_hash, "train": train_hash}),
        })

    payload = oof_bundle.build_payload(
        predictions=predictions,
        fold_boundaries=fold_boundaries,
        per_fold=per_fold,
        attestation_digest=attestation_digest,
    )
    path = oof_bundle.write_bundle(out_root, payload)
    return path, payload
