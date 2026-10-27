"""Current-snapshot, file-auditable feature building for a manually invoked observation.

No connection is constructed until explicitly enabled with a caller-supplied session factory.
The pure builder consumes frozen dataframes, preserves unknowns, and never loads a mutable DB.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
for package in ("features", "training", "serving", "eval", "probability", "db"):
    source = str(ROOT / package / "src")
    if source not in sys.path:
        sys.path.insert(0, source)

from sqlalchemy import select, text  # noqa: E402
from horseracing_db.models.ingestion import IdMapping  # noqa: E402
from horseracing_features.builder import assemble_feature_matrix  # noqa: E402
from horseracing_features.loader import load_frames  # noqa: E402
from horseracing_features.materialize import _hash_frame, source_fingerprint  # noqa: E402
from horseracing_training import market_ev  # noqa: E402
from scripts.roi_explore.model_connection_163 import contract as c, models  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
MODE = "manual_observation"


def _encoded(value):
    return models._encode(value)


def _rows(frame, columns):
    return [
        dict(
            horse_id=str(row.horse_id),
            horse_number=int(row.horse_number),
            features={name: _encoded(getattr(row, name)) for name in columns},
        )
        for row in frame.sort_values("horse_id", kind="stable").itertuples(index=False)
    ]


def _identity(frame, expected_field, *, label):
    c.require(
        {"race_id", "horse_id", "horse_number"} <= set(frame.columns),
        label + "_identity_columns_missing",
    )
    observed = []
    for row in frame.itertuples():
        number = row.horse_number
        c.require(
            isinstance(row.horse_id, str) and row.horse_id, label + "_horse_id_invalid"
        )
        c.require(
            not isinstance(number, bool)
            and str(number) not in ("True", "False")
            and c.number(str(number), 1, 99) == int(c.number(str(number), 1, 99)),
            label + "_horse_number_invalid",
        )
        observed.append(dict(horse_id=row.horse_id, horse_number=int(number)))
    c.require(
        c.field(observed) == c.field(expected_field)
        and len(observed) == len(expected_field),
        label + "_field_mismatch",
    )


def _strict_history(history, race_id, day, expected_field):
    c.require(
        isinstance(history, pd.DataFrame)
        and {"race_id", "horse_id", "race_date", "entry_status"}
        <= set(history.columns),
        "history_columns_missing",
    )
    h = history[["race_id", "horse_id", "race_date", "entry_status"]].copy()
    c.require(
        h.entry_status.isin(["started", "cancelled", "excluded"]).all(),
        "history_unknown_entry_status",
    )
    h["race_date"] = pd.to_datetime(h.race_date, errors="raise")
    c.require(h.race_date.notna().all(), "history_date_missing")
    # Snapshot can include future/nonstarter records, but source evidence fingerprints the whole
    # query result. The correction history is explicitly the strictly prior STARTED population.
    h = h.loc[
        h.horse_id.isin([r["horse_id"] for r in expected_field])
        & h.entry_status.eq("started")
        & h.race_date.ge(pd.Timestamp("2007-01-01"))
        & h.race_date.lt(pd.Timestamp(day))
    ]
    c.require(
        not h.duplicated(["race_id", "horse_id"]).any()
        and not h.duplicated(["horse_id", "race_date"]).any(),
        "history_duplicate_started_day",
    )
    c.require(not h.race_id.eq(race_id).any(), "history_target_race")
    h = h.sort_values(["horse_id", "race_date", "race_id"], kind="stable")
    return [
        dict(
            race_id=str(r.race_id),
            horse_id=str(r.horse_id),
            race_date=r.race_date.date().isoformat(),
            entry_status="started",
        )
        for r in h.itertuples()
    ]


def build_from_snapshot(
    legacy_frame,
    mev_raw_frame,
    started_history,
    quote,
    *,
    race_id,
    race_date,
    identity_map=None,
):
    """Use full frozen builder inputs, replacing ONLY the target's market vector explicitly.

    ``legacy_frame`` is the actual target output of the ordinary fixed-schema builder from the
    same DB snapshot. ``mev_raw_frame`` covers the 2007+ started pool through the target day;
    other races of that day remain for the scheduled-card first/last-race indicators. No own
    target outcome may exist. Old-model current market inputs are absent from its 138 columns.
    """
    day = dt.date.fromisoformat(race_date)
    c.require(
        day.year == 2026
        and isinstance(race_id, str)
        and len(race_id) == 12
        and race_id.isdigit(),
        "unsupported_feature_target",
    )
    c.require(
        isinstance(quote, dict) and isinstance(quote.get("rows"), list), "quote_missing"
    )
    expected = [{k: r[k] for k in ("horse_id", "horse_number")} for r in quote["rows"]]
    c.field(expected)
    old_columns, mev_columns = (
        models.input_columns("legacy"),
        models.input_columns("mev"),
    )
    c.require(
        isinstance(legacy_frame, pd.DataFrame)
        and set(old_columns) <= set(legacy_frame.columns),
        "legacy_full_matrix_missing",
    )
    legacy = legacy_frame.loc[legacy_frame.race_id.eq(race_id)].copy()
    _identity(legacy, expected, label="legacy")
    if "race_date" in legacy:
        c.require(
            pd.to_datetime(legacy.race_date).eq(pd.Timestamp(day)).all(),
            "legacy_target_date_mismatch",
        )
    legacy[list(c.WEIGHTS)] = None
    c.require(
        not ({"odds", "popularity", "q"} & set(old_columns)),
        "legacy_market_input_forbidden",
    )
    history = _strict_history(started_history, race_id, day, expected)
    identity_map = [] if identity_map is None else identity_map
    c.require(isinstance(identity_map, list), "identity_map_invalid")
    for mapping in identity_map:
        c.require(
            isinstance(mapping, dict)
            and set(mapping) == {"source_id", "canonical_id"}
            and isinstance(mapping["source_id"], str)
            and mapping["source_id"].isdigit()
            and mapping["canonical_id"] in {r["horse_id"] for r in expected},
            "identity_map_invalid",
        )
    c.require(
        len({r["source_id"] for r in identity_map}) == len(identity_map)
        and len({r["canonical_id"] for r in identity_map}) == len(identity_map),
        "identity_map_ambiguous",
    )
    raw = mev_raw_frame.copy()
    required = {
        "race_id",
        "horse_id",
        "horse_number",
        "race_date",
        "track_type",
        "result_status",
        "finish_order",
        "odds",
        "popularity",
        "last_3f",
        "margin_sec",
    }
    c.require(required <= set(raw.columns), "mev_raw_columns_missing")
    raw["race_date"] = pd.to_datetime(raw.race_date, errors="raise")
    c.require(
        raw.race_date.notna().all()
        and raw.race_date.between(pd.Timestamp("2007-01-01"), pd.Timestamp(day)).all(),
        "mev_snapshot_outside_date_cutoff",
    )
    c.require(
        not raw.duplicated(["race_id", "horse_id"]).any(), "mev_duplicate_source_row"
    )
    target = raw.race_id.eq(race_id)
    c.require(
        target.any() and raw.loc[target, "race_date"].eq(pd.Timestamp(day)).all(),
        "mev_target_date_mismatch",
    )
    _identity(raw.loc[target], expected, label="mev")
    c.require(
        raw.loc[target, "track_type"].isin(["芝", "ダ"]).all(),
        "unsupported_target_track",
    )
    c.require(
        raw.loc[target, "result_status"].isna().all()
        and raw.loc[target, "finish_order"].isna().all()
        and raw.loc[target, "last_3f"].isna().all()
        and raw.loc[target, "margin_sec"].isna().all(),
        "target_outcome_already_present",
    )
    # Started-only raw query must not contain a duplicate same-day start for a target horse.
    same_day = raw.loc[
        raw.race_date.eq(pd.Timestamp(day))
        & raw.horse_id.isin([r["horse_id"] for r in expected])
    ]
    c.require(
        not same_day.horse_id.duplicated().any(),
        "target_horse_multiple_same_day_starts",
    )
    original_source_sha = _hash_frame(raw, list(raw.columns))
    price = {r["horse_id"]: r for r in quote["rows"]}
    for index, row in raw.loc[target].iterrows():
        q = price[row.horse_id]
        c.number(q["odds"], 1)
        c.require(
            type(q["popularity"]) is int and q["popularity"] > 0,
            "quote_popularity_invalid",
        )
        raw.at[index, "odds"] = float(q["odds"])
        raw.at[index, "popularity"] = q["popularity"]
        if "horse_row_updated_at" in raw:
            raw.at[index, "horse_row_updated_at"] = pd.Timestamp(quote["received_at"])
    built = market_ev.build_features(raw)
    selected = built.loc[built.race_id.eq(race_id)]
    _identity(selected, expected, label="built_mev")
    c.require(set(mev_columns) <= set(selected.columns), "mev_full_matrix_missing")
    old_rows, mev_rows = _rows(legacy, old_columns), _rows(selected, mev_columns)
    # Validate the consumed matrix and the explicit old correction history without inference.
    old_frame, _ = models._input_frame(old_rows, race_id, race_date, "legacy")
    models._history(old_frame, history, race_date)
    models._input_frame(mev_rows, race_id, race_date, "mev")
    expected_market = c.market_features(quote["rows"])
    for row in mev_rows:
        for name, value in expected_market[row["horse_id"]].items():
            if name in row["features"]:
                actual = row["features"][name]
                c.require(
                    actual is not None and abs(float(actual) - float(value)) <= 1e-12,
                    "mev_market_derived_mismatch",
                )
    result = dict(
        rows=dict(legacy=old_rows, mev=mev_rows),
        legacy_history=history,
        source_evidence=dict(
            mev_raw_pool_rows=len(raw),
            legacy_matrix_rows=len(legacy_frame),
            history_query_rows=len(started_history),
            mev_raw_source_sha256=original_source_sha,
            mev_consumed_source_sha256=_hash_frame(raw, list(raw.columns)),
            legacy_input_sha256=c.digest(old_rows),
            mev_input_sha256=c.digest(mev_rows),
            legacy_history_sha256=c.digest(history),
            quote_sha256=quote["raw_sha256"],
            identity_map=identity_map,
            target_outcomes_absent=True,
            old_weight_mask=list(c.WEIGHTS),
            same_day_outcomes_excluded_by="strict-date product builder; target own outcomes rejected",
            source_feature_definition="unchanged production assemble_feature_matrix and market_ev.build_features",
        ),
    )
    c.canonical(result)
    return result


class DatabaseFeatureReader:
    """Future manual enabled path; default does not touch a factory, environment or database."""

    def __init__(self, *, enabled=False, session_factory=None):
        self.enabled = enabled is True
        self.session_factory = session_factory

    def capture(self, race_id, race_date, quote, clock):
        c.require(self.enabled, "new_collection_disabled")
        c.require(callable(self.session_factory), "feature_session_factory_required")
        day = dt.date.fromisoformat(race_date)
        began = clock.now()
        with self.session_factory() as session:
            session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            read_only = session.execute(text("SHOW transaction_read_only")).scalar_one()
            isolation = session.execute(text("SHOW transaction_isolation")).scalar_one()
            c.require(
                read_only == "on" and isolation == "repeatable read",
                "feature_snapshot_not_read_only",
            )
            snapshot_id = str(
                session.execute(
                    text("SELECT txid_current_snapshot()::text")
                ).scalar_one()
            )
            snapshot_observed = clock.now()
            target_results = session.execute(
                text("SELECT count(*) FROM race_results WHERE race_id=:r"),
                {"r": race_id},
            ).scalar_one()
            c.require(
                type(target_results) is int and target_results == 0,
                "target_results_already_present",
            )
            frames = load_frames(session, end_date=day)
            raw = market_ev.load_rows(
                session.connection(), through=race_date, since="2007-01-01"
            )
            mapping_rows = [
                dict(row)
                for row in session.execute(
                    select(
                        IdMapping.source_id,
                        IdMapping.canonical_id,
                        IdMapping.mapping_status,
                    ).where(
                        IdMapping.entity_type == "horse",
                        IdMapping.source == "netkeiba",
                        IdMapping.canonical_id.in_(
                            [r["horse_id"] for r in quote["rows"]]
                        ),
                    )
                )
                .mappings()
                .all()
            ]
            mapped = [
                row
                for row in mapping_rows
                if row["mapping_status"] == "mapped"
                and isinstance(row["source_id"], str)
                and row["source_id"].isdigit()
                and row["canonical_id"] in {r["horse_id"] for r in quote["rows"]}
            ]
            source_counts = {
                r["source_id"]: sum(s["source_id"] == r["source_id"] for s in mapped)
                for r in mapped
            }
            target_counts = {
                r["canonical_id"]: sum(
                    s["canonical_id"] == r["canonical_id"] for s in mapped
                )
                for r in mapped
            }
            identity_map = [
                dict(source_id=r["source_id"], canonical_id=r["canonical_id"])
                for r in mapped
                if source_counts[r["source_id"]] == 1
                and target_counts[r["canonical_id"]] == 1
            ]
            acquired = clock.now()
            c.require(
                not frames.race_results.race_id.eq(race_id).any(),
                "target_results_in_snapshot",
            )
            matrix = assemble_feature_matrix(
                frames,
                end_date=day,
                start_date=day,
                wanted=frozenset(models.input_columns("legacy")),
                target_race_ids=frozenset([race_id]),
                use_materialized=False,
            )
            history = frames.race_horses[["race_id", "horse_id", "entry_status"]].merge(
                frames.races[["race_id", "race_date"]],
                on="race_id",
                validate="many_to_one",
            )
            result = build_from_snapshot(
                matrix,
                raw,
                history,
                quote,
                race_id=race_id,
                race_date=race_date,
                identity_map=identity_map,
            )
            finished = clock.now()
            result["source_evidence"].update(
                snapshot_started_at=began,
                snapshot_observed_at=snapshot_observed,
                acquisition_finished_at=acquired,
                feature_build_finished_at=finished,
                snapshot_id=snapshot_id,
                read_only=read_only,
                isolation=isolation,
                source_pool_sha256=source_fingerprint(frames, through=day),
                source_pool_rows={
                    name: len(getattr(frames, name))
                    for name in ("races", "race_horses", "race_results", "horses")
                },
                source_queries=[
                    "horseracing_features.loader.load_frames(end_date)",
                    market_ev.ROWS_SQL,
                    "SELECT count(*) FROM race_results WHERE race_id=:r",
                    "SELECT source_id, canonical_id, mapping_status FROM id_mappings WHERE entity_type='horse' AND source='netkeiba' AND canonical_id IN target_horses",
                ],
                identity_mapping_rows=mapping_rows,
                identity_mapping_available_at=acquired,
                identity_mapping_complete=len(identity_map) == len(quote["rows"]),
            )
            # Never commit a feature update; closing the read-only snapshot releases it.
            session.rollback()
        result["observed_by_at"] = finished
        result["generated_at"] = finished
        result["source_times"] = dict(
            legacy={name: acquired for name in models.input_columns("legacy")},
            mev={
                name: quote["received_at"]
                if name
                in c.market_features(quote["rows"])[quote["rows"][0]["horse_id"]]
                else acquired
                for name in models.input_columns("mev")
            },
        )
        result["row_available_at"] = {
            kind: {r["horse_id"]: finished for r in result["rows"][kind]}
            for kind in ("legacy", "mev")
        }
        result["history_available_at"] = acquired
        result["captured_at"] = clock.now()
        c.canonical(result)
        return result
