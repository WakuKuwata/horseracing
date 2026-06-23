"""Leak-safe target encoding for high-cardinality identifiers (US4, P2).

Three rules enforced here (codex review):

1. **Same label as training.** TE uses the started-all / DNF=0 win label — never Feature 004's
   finished-only ``fit_target_encoding`` (mixing populations breaks the feature's meaning).
2. **Consistent prior.** The smoothing prior is supplied by the caller (the model-fit win rate)
   and reused identically for the OOF training values, the final encoder, and the predict-time
   fallback — so OOF / final / predict agree on unseen/small categories.
3. **Out-of-fold for training rows.** A training row is never encoded with its own label: its
   value comes from the *other* race-level folds. The final encoder (fit on all model-fit rows)
   is applied only to held-out calibration rows and to predict-time races — never refit on them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .folds import chronological_race_folds

DEFAULT_SMOOTHING = 10.0
#: high-cardinality identifier columns worth target-encoding (low-card codes stay native).
DEFAULT_TE_COLUMNS: tuple[str, ...] = ("jockey_id", "trainer_id", "venue_code")


@dataclass(frozen=True)
class TargetEncoder:
    col: str
    prior: float
    mapping: dict
    smoothing: float

    def transform(self, values: pd.Series) -> np.ndarray:
        # cast away category dtype first: .map on a categorical returns a categorical, and the
        # subsequent fillna(prior) would reject the (non-category) prior value (pandas 3.x).
        mapped = pd.Series(values).astype(object).map(self.mapping)
        return mapped.fillna(self.prior).astype(float).to_numpy()


def apply_encoded_columns(
    base: pd.DataFrame, encoded: dict[str, np.ndarray], order: list[str]
) -> pd.DataFrame:
    """Return ``base`` with ``encoded`` columns replaced by fresh float columns.

    The source columns are category dtype; assigning a float array in place would raise on
    pandas 3.x ("new category"). Dropping then re-adding yields a clean float dtype. Columns
    are reordered to ``order`` so the model always sees a stable feature layout.
    """
    out = base.drop(columns=[c for c in encoded if c in base.columns])
    for col, vals in encoded.items():
        out[col] = np.asarray(vals, dtype=float)
    return out[order]


def fit_target_encoder(
    df: pd.DataFrame, col: str, *, label_col: str, prior: float,
    smoothing: float = DEFAULT_SMOOTHING,
) -> TargetEncoder:
    """Smoothed category means fit on the supplied rows only (caller passes model-fit rows)."""
    agg = df.groupby(col, observed=True)[label_col].agg(["sum", "count"])
    mapping = ((agg["sum"] + smoothing * prior) / (agg["count"] + smoothing)).to_dict()
    return TargetEncoder(col=col, prior=prior, mapping=mapping, smoothing=smoothing)


def oof_target_encode(
    df: pd.DataFrame,
    col: str,
    *,
    race_id_col: str,
    race_date_col: str,
    label_col: str,
    prior: float | None = None,
    n_splits: int = 5,
    smoothing: float = DEFAULT_SMOOTHING,
) -> pd.Series:
    """Out-of-fold smoothed encoding: each fold's rows use the OTHER folds' label means only.

    ``prior`` defaults to ``df[label_col].mean()`` but callers pass the model-fit prior so the
    OOF values match the final encoder's fallback exactly.
    """
    race_dates = dict(zip(df[race_id_col], df[race_date_col], strict=True))
    folds = chronological_race_folds(df[race_id_col].to_numpy(), race_dates, n_splits)
    base = float(df[label_col].mean()) if prior is None else prior
    out = pd.Series(np.full(len(df), base, dtype=float), index=df.index)

    for held in folds:
        held_mask = df[race_id_col].isin(held)
        rest = df[~held_mask]
        if rest.empty:
            continue
        enc = fit_target_encoder(rest, col, label_col=label_col, prior=base, smoothing=smoothing)
        out.loc[held_mask] = enc.transform(df.loc[held_mask, col])
    return out
