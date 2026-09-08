"""Pure 129 inference: frozen 125/118 residual terms and six-head averaging.

No fitting or I/O. History without ``entry_status`` is an explicitly started-only
input contract (as with the frozen research matrix). Missing terms contribute a
zero exponent offset; this does not make the horse's normalized probability fixed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus
from horseracing_eval.predictor import Prediction
from horseracing_training.predictor import assemble_predictions

KEYS = ["race_id", "horse_id", "race_date"]
INPUT_TERMS = ["gap_log", "prior_gap_log", "female_sin", "female_cos"]
JOINT_TERMS = [*INPUT_TERMS, "centered_logp"]
HISTORY_START = pd.Timestamp("2007-01-01")
HEAD_SUM_ATOL = 1e-8


def _keyed(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or not set(KEYS).issubset(frame.columns):
        raise ValueError("Missing correction identity columns")
    if frame.columns.duplicated().any() or frame[KEYS].isna().any().any():
        raise ValueError("Missing or ambiguous correction identity")
    if any(not isinstance(v, str) or not v for c in KEYS[:2] for v in frame[c]):
        raise ValueError("Correction IDs must be nonempty strings")
    if frame.duplicated(KEYS[:2]).any():
        raise ValueError("Duplicate race/horse correction identity")
    out = frame.copy()
    days = pd.to_datetime(out.race_date, errors="raise")
    if days.dt.tz is not None or not (days == days.dt.normalize()).all():
        raise ValueError("Race dates must be timezone-free calendar days")
    out["race_date"] = days
    if (out.groupby("race_id", sort=False).race_date.nunique() > 1).any():
        raise ValueError("One race has conflicting dates")
    return out


def build_correction_inputs(target_rows: pd.DataFrame, started_history: pd.DataFrame) -> pd.DataFrame:
    """Return keys and four terms in the exact target row/index order.

    Prior gap is D1-D2, where D2<D1<target day are distinct started dates
    since 2007 under the same horse ID. Results, future/same-day records, and
    cancelled/excluded starts never determine it; no cross-ID repair occurs.
    Current gap is the supplied existing history feature, not reconstructed.
    """
    target = _keyed(target_rows)
    history = _keyed(started_history)
    if not {"days_since_last", "sex"}.issubset(target.columns):
        raise ValueError("Missing current gap or sex")
    if (target.race_date < HISTORY_START).any():
        raise ValueError("Target precedes registered history scope")
    if "entry_status" in target and not target.entry_status.eq(EntryStatus.STARTED).all():
        raise ValueError("Targets must all be started")
    if "entry_status" in history:
        if not history.entry_status.isin(EntryStatus.ALL).all():
            raise ValueError("Unknown history entry status")
        history = history[history.entry_status == EntryStatus.STARTED]
    history = history[history.race_date >= HISTORY_START]
    common = target[KEYS].merge(history[KEYS], on=KEYS[:2], suffixes=("_target", "_history"))
    if not common.race_date_target.eq(common.race_date_history).all():
        raise ValueError("Target/history identity has conflicting dates")
    gap = target.days_since_last.to_numpy(dtype=float, na_value=np.nan)
    observed = gap[np.isfinite(gap)]
    if np.isinf(gap).any() or (observed <= 0).any() or (observed % 1 != 0).any():
        raise ValueError("Current gaps must be positive integral strict-past days")
    sex = target.sex.astype(object)
    if not set(sex.dropna()).issubset({"牡", "牝", "セ"}):
        raise ValueError("Unknown original sex category")
    dates = {horse: np.unique(group.race_date.to_numpy(dtype="datetime64[D]"))
             for horse, group in history.groupby("horse_id", sort=False)}
    prior = np.full(len(target), np.nan)
    for i, (horse, day) in enumerate(zip(target.horse_id, target.race_date, strict=True)):
        previous = dates.get(horse)
        if previous is None:
            continue
        pos = int(np.searchsorted(previous, np.datetime64(day.date()), side="left"))
        if pos >= 2:
            prior[i] = float((previous[pos - 1] - previous[pos - 2]) / np.timedelta64(1, "D"))
    theta = 2 * np.pi * ((target.race_date.dt.dayofyear.to_numpy() - 1)
                        / np.where(target.race_date.dt.is_leap_year, 366., 365.))
    female = np.where(sex.isna(), np.nan, (sex == "牝").to_numpy(dtype=float))
    out = target[KEYS].copy()
    out["gap_log"] = np.log1p(gap)
    out["prior_gap_log"] = np.log1p(prior)
    out["female_sin"] = female * np.sin(theta)
    out["female_cos"] = female * np.cos(theta)
    return out


def _heads(predictions: Mapping[str, Prediction], ids: list[str]) -> np.ndarray:
    if not ids or len(set(ids)) != len(ids) or list(predictions) != ids:
        raise ValueError("Prediction horse identity/order differs")
    try:
        x = np.asarray([[predictions[h].win, predictions[h].top2, predictions[h].top3]
                        for h in ids], dtype=np.float64)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Invalid prediction heads") from exc
    if (not np.isfinite(x).all() or (x < -1e-10).any() or (x > 1 + 1e-10).any()
            or (np.diff(x, axis=1) < -1e-10).any() or (x[:, 0] <= 0).any()
            or not np.allclose(x.sum(axis=0), [min(k, len(ids)) for k in (1, 2, 3)],
                               atol=HEAD_SUM_ATOL, rtol=0)):
        raise ValueError("Invalid probability heads: finite/domain/order/sums")
    return x


def correct_member_predictions(
    started_ids: Sequence[str], base_p, inputs: pd.DataFrame,
    terms: Sequence[str], coefficients: Sequence[float],
) -> dict[str, Prediction]:
    """Tilt already calibrated/once-clipped race probabilities; assemble at eps=0."""
    ids = list(started_ids)
    rows = _keyed(inputs)
    if (not ids or len(ids) != len(set(ids)) or rows.horse_id.tolist() != ids
            or rows.race_id.nunique() != 1):
        raise ValueError("One race and exact started horse row order required")
    terms = list(terms)
    if terms not in (["gap_log"], JOINT_TERMS):
        raise ValueError("Unregistered correction term order")
    p = np.asarray(base_p, dtype=float)
    beta = np.asarray(coefficients)
    if (p.shape != (len(ids),) or not np.isfinite(p).all() or (p <= 0).any()
            or (p > 1).any() or (len(ids) > 1 and (p >= 1).any())
            or not np.isclose(p.sum(), 1., atol=1e-8, rtol=0)):
        raise ValueError("Base probabilities must be finite positive and normalized")
    if beta.shape != (len(terms),) or beta.dtype.kind not in "fiu" or not np.isfinite(beta).all():
        raise ValueError("Invalid frozen coefficients")
    if "centered_logp" in terms and 1. + beta[-1] <= 0:
        raise ValueError("Nonpositive effective temperature exponent")
    needed = [c for c in terms if c != "centered_logp"]
    if not set(needed).issubset(rows.columns):
        raise ValueError("Missing correction terms")
    raw = rows[needed].to_numpy(dtype=float, na_value=np.nan)
    if np.isinf(raw).any():
        raise ValueError("Infinite correction term")
    h = np.nan_to_num(raw, nan=0.)
    if "centered_logp" in terms:
        lp = np.log(p)
        h = np.column_stack((h, lp - lp.mean()))
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        z = h @ beta
        raw_q = p * np.exp(z - z.max())
        q = raw_q / raw_q.sum()
    if (not np.isfinite(z).all() or not np.isfinite(q).all() or (q <= 0).any()
            or (len(ids) > 1 and (q >= 1).any())
            or not np.isclose(q.sum(), 1., atol=1e-12, rtol=0)):
        raise ValueError("Corrected probability domain failed; no clipping repair")
    result = assemble_predictions(ids, q, eps=0.)
    values = _heads(result, ids)
    if not np.allclose(values[:, 0], q, atol=1e-12, rtol=0):
        raise ValueError("Assembly changed corrected win probabilities")
    return result


def average_member_predictions(members: Sequence[Mapping[str, Prediction]]) -> dict[str, Prediction]:
    """Average all three heads in fixed member/horse order, with no postprocess."""
    if len(members) != 6:
        raise ValueError("Exactly six complete members required")
    ids = list(members[0])
    x = np.stack([_heads(member, ids) for member in members])
    mean = x.mean(axis=0, dtype=np.float64)
    result = {h: Prediction(*map(float, mean[i])) for i, h in enumerate(ids)}
    _heads(result, ids)
    if np.max(-np.log(mean[:, 0]) - (-np.log(x[:, :, 0])).mean(axis=0)) > 1e-12:
        raise ValueError("Mixture Jensen invariant failed")
    return result
