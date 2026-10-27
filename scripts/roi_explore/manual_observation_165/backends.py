"""Closed backend identities. Rehearsal never constructs an enabled production adapter."""

from __future__ import annotations

from scripts.roi_explore.model_connection_163 import contract as c
from scripts.roi_explore.model_connection_163.quotes import make_local_fixture
from scripts.roi_explore.model_connection_163.run import input_rows
from .adapters import ObservationAdapters
from .features import DatabaseFeatureReader


class LiveBackend:
    origin = "manual_live"

    def __init__(self):
        # Lazy: constructing these objects does not create an engine or transport.
        self.adapters = ObservationAdapters(enabled=True)

        def session():
            from sqlalchemy.orm import Session

            return Session(self.adapters.engine())

        self.reader = DatabaseFeatureReader(enabled=True, session_factory=session)

    def guard(self, stage, race_id, clock):
        return self.adapters.observe_guard(stage, race_id, clock)

    def quote(self, race_id, clock):
        return self.adapters.fetch_quote(race_id, clock)

    def features(self, race_id, race_date, quote, clock):
        return self.reader.capture(race_id, race_date, quote, clock)

    def close(self):
        self.adapters.close()


class FixtureBackend:
    origin = "fixture_rehearsal"

    def __init__(self, plan, *, scenario="normal"):
        self.plan, self.scenario = plan, scenario
        self.field = [dict(horse_id=f"fixture-{i}", horse_number=i) for i in (1, 2, 3)]
        self.calls = []

    def guard(self, stage, race_id, clock):
        self.calls.append("guard:" + stage)
        entries = [dict(**r, entry_status="started") for r in self.field]
        if self.scenario == "field_changed" and stage == "decision":
            entries[-1]["entry_status"] = "cancelled"
        results = (
            [dict(race_id=race_id)]
            if self.scenario == "result_appeared" and stage == "after"
            else []
        )
        queries = {}
        for kind, rows in [
            (
                "race",
                [
                    dict(
                        race_id=race_id,
                        race_date=self.plan["race_date"],
                        post_time=self.plan["post_time"],
                    )
                ],
            ),
            ("entries", entries),
            ("results", results),
        ]:
            queries[kind] = dict(
                started_at=clock.now(), completed_at=clock.now(), rows=rows
            )
        return queries

    def quote(self, race_id, clock):
        self.calls.append("quote")
        started = clock.now()
        q = make_local_fixture(
            race_id=race_id,
            rows=[
                dict(**r, odds=str(o), popularity=i)
                for i, (r, o) in enumerate(zip(self.field, (2, 4, 8), strict=True), 1)
            ],
            fetch_started_at=started,
            received_at=clock.now(),
        )
        return dict(
            raw_text=q["raw_text"],
            source_url=q["source_url"],
            fetch_started_at=started,
            received_at=clock.now(),
        )

    def features(self, race_id, race_date, quote, clock):
        self.calls.append("features")
        started = clock.now()
        if self.scenario == "feature_failure":
            raise ValueError("fixture_feature_failure")
        rows = input_rows(c.make_protocol(), quote)
        observed = clock.now()
        return dict(
            rows=rows,
            legacy_history=[],
            generated_at=observed,
            observed_by_at=observed,
            captured_at=observed,
            history_available_at=observed,
            row_available_at={
                kind: {r["horse_id"]: observed for r in self.field} for kind in rows
            },
            source_times={
                kind: {
                    name: (
                        quote["received_at"]
                        if kind == "mev"
                        and name
                        in c.market_features(quote["rows"])[self.field[0]["horse_id"]]
                        else observed
                    )
                    for name in rows[kind][0]["features"]
                }
                for kind in rows
            },
            source_evidence=dict(
                origin="synthetic_fixture",
                read_only="on",
                isolation="repeatable read",
                snapshot_started_at=started,
                acquisition_finished_at=observed,
                feature_build_finished_at=observed,
                identity_map=[],
            ),
        )

    def close(self):
        self.calls.append("close")
