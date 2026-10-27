"""Independent direct-file manual packet audit; never imports the 165 implementation."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "independent_native_163",
    ROOT
    / "specs/163-offline-model-connection/evidence/independent_connection_audit.py",
)
NATIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NATIVE)
load, sha, digest, at, fields = (
    NATIVE.load,
    NATIVE.sha,
    NATIVE.digest,
    NATIVE.at,
    NATIVE.fields,
)


def audit(root):
    root = Path(root)
    session = root / "session"
    manifest = load(session / "manifest.json")
    assert set(manifest["files"]) | {"manifest.json"} == {
        str(p.relative_to(session))
        for p in session.rglob("*")
        if p.is_file() and p.relative_to(session).parts[0] != "settlements"
    }
    for name, checksum in manifest["files"].items():
        assert sha(session / name) == checksum, name
    plan, index, decision, status, seal, accepted, clock = [
        load(session / name)
        for name in (
            "plan.json",
            "index.json",
            "decision.json",
            "status.json",
            "seal.json",
            "accepted.json",
            "clock.json",
        )
    ]
    for name, checksum in plan["source_pins"].items():
        assert sha(ROOT / name) == checksum, name
    assert plan["origin"] == "fixture_rehearsal" and plan["manual_only"] is True
    assert plan["collection_default"] is plan["can_adopt_model"] is False
    assert status["state"] == "sealed" and status["origin"] == plan["origin"]
    assert (
        decision["evidence_admissible"]
        is accepted["evidence_admissible"]
        is status["evidence_admissible"]
        is False
    )
    assert decision["eligible_for_prospective"] is decision["can_adopt_model"] is False
    assert status["can_adopt_model"] is accepted["can_adopt_model"] is False
    assert decision["session_id"] == index["session_id"] == status["session_id"]
    assert digest(plan) == status["plan_sha256"]
    assert decision["input_bundle_sha256"] == digest(index)
    assert seal["decision_sha256"] == digest(decision) and accepted[
        "seal_sha256"
    ] == digest(seal)
    registration = load(session / "registration-ready.json")
    assert registration["plan_sha256"] == digest(plan)
    assert (
        at(plan["registered_at"])
        <= at(registration["available_by_at"])
        <= at(status["started_at"])
    )
    assert clock["simulated_clock"] is False
    observations = clock["observations"]
    assert len({r["epoch_id"] for r in observations}) == 1
    for before, after in zip(observations, observations[1:]):
        wall = (at(after["at"]) - at(before["at"])).total_seconds()
        monotonic = (after["monotonic_ns"] - before["monotonic_ns"]) / 1e9
        assert wall >= 0 and monotonic >= 0 and abs(wall - monotonic) <= 1
    stamps = {r["at"] for r in observations}
    values, ready = {}, {}
    assert set(index["events"]) == {
        "before",
        "quote",
        "features",
        "prediction_legacy",
        "prediction_mev",
        "after",
        "final",
    }
    for kind, ref in index["events"].items():
        body, receipt = load(session / ref["path"]), load(session / ref["receipt_path"])
        assert sha(session / ref["path"]) == ref["sha256"]
        assert sha(session / ref["receipt_path"]) == ref["receipt_sha256"]
        assert body == dict(
            schema_version=1,
            mode="manual_observation",
            origin=plan["origin"],
            kind=kind,
            race_id=plan["race_id"],
            session_id=index["session_id"],
            payload=body["payload"],
        )
        assert receipt == dict(
            mode="manual_observation",
            origin=plan["origin"],
            session_id=index["session_id"],
            kind=kind,
            source_sha256=ref["sha256"],
            available_by_at=receipt["available_by_at"],
        )
        assert receipt["available_by_at"] in stamps
        values[kind], ready[kind] = body["payload"], at(receipt["available_by_at"])
    d, post = at(decision["decision_at"]), at(plan["post_time"])
    assert d <= at(seal["observed_saved_at"]) <= at(accepted["acknowledged_at"])
    for label in (
        decision["decision_at"],
        seal["observed_saved_at"],
        accepted["acknowledged_at"],
    ):
        assert label in stamps
        assert (
            post - dt.timedelta(seconds=600)
            <= at(label)
            < post - dt.timedelta(seconds=540)
        )
    quote = values["quote"]
    assert (
        at(quote["fetch_started_at"]) <= at(quote["received_at"]) <= ready["quote"] <= d
    )
    assert quote["fetch_started_at"] in stamps and quote["received_at"] in stamps
    assert ready["before"] <= at(quote["fetch_started_at"])
    for label in (d, at(seal["observed_saved_at"]), at(accepted["acknowledged_at"])):
        assert 0 <= (label - at(quote["received_at"])).total_seconds() <= 1800
    raw = json.loads(quote["raw_text"])
    prices = {
        int(number): float(value[0])
        for number, value in raw["data"]["odds"]["1"].items()
    }
    expected = fields(decision["field"])
    assert {n for _, n in expected} == set(prices)
    for kind, anchor in (
        ("before", at(quote["fetch_started_at"])),
        ("after", ready["after"]),
        ("final", d),
    ):
        guards = values[kind]
        assert set(guards) == {"race", "entries", "results"}
        for query in guards.values():
            assert query["started_at"] in stamps and query["completed_at"] in stamps
            assert at(query["started_at"]) <= at(query["completed_at"]) <= anchor
            assert (anchor - at(query["started_at"])).total_seconds() <= 5
        race = guards["race"]["rows"]
        assert len(race) == 1 and race[0]["race_id"] == plan["race_id"]
        assert (
            race[0]["race_date"] == plan["race_date"]
            and at(race[0]["post_time"]) == post
        )
        assert guards["results"]["rows"] == []
        assert (
            fields(
                [r for r in guards["entries"]["rows"] if r["entry_status"] == "started"]
            )
            == expected
        )
    features = values["features"]
    assert set(features["rows"]) == {"legacy", "mev"}
    assert (
        at(quote["received_at"])
        <= at(features["generated_at"])
        <= at(features["observed_by_at"])
        <= ready["features"]
    )
    source = features["source_evidence"]
    assert source["read_only"] == "on" and source["isolation"] == "repeatable read"
    assert (
        at(source["snapshot_started_at"])
        <= at(source["acquisition_finished_at"])
        <= at(features["generated_at"])
    )
    predictions = {kind: values["prediction_" + kind] for kind in ("legacy", "mev")}
    for kind, p in predictions.items():
        assert p["feature_event_sha256"] == index["events"]["features"]["sha256"]
        assert (
            ready["features"]
            <= at(p["input_available_at"])
            <= at(p["started_at"])
            <= at(p["computed_at"])
            <= ready["prediction_" + kind]
            <= d
        )
        assert {p["input_available_at"], p["started_at"], p["computed_at"]} <= stamps
        assert p["model_sha256"] == plan["model_protocol"]["model_shas"][kind]
        assert fields(p["rows"]) == fields(features["rows"][kind]) == expected
        saved = {r["horse_id"]: r["features"] for r in features["rows"][kind]}
        assert all(r["features"] == saved[r["horse_id"]] for r in p["rows"])
        assert all(r["input_asof"] == features["observed_by_at"] for r in p["rows"])
    for row in predictions["legacy"]["rows"]:
        assert all(
            row["features"][key] is None
            for key in ("weight", "weight_diff", "prev_weight")
        )
    assert min(at(v["started_at"]) for v in values["after"].values()) >= max(
        at(p["computed_at"]) for p in predictions.values()
    )
    assert min(at(v["started_at"]) for v in values["final"].values()) >= max(
        ready["after"], ready["prediction_mev"], ready["prediction_legacy"]
    )
    direct_native = NATIVE.actual_native_predictions(
        dict(race_date=plan["race_date"], predictions=predictions)
    )
    maps = {
        kind: {r["horse_id"]: r["probability"] for r in p["rows"]}
        for kind, p in predictions.items()
    }
    qden = math.fsum(1 / value for value in prices.values())
    for arm, content in decision["arms"].items():
        beta = plan["model_protocol"]["coefficients"][arm]["coefficients"]
        logits = []
        for horse, number in sorted(expected, key=lambda item: (item[1], item[0])):
            component = dict(
                q=1 / prices[number] / qden,
                p_mev=min(1 - 1e-6, max(1e-6, maps["mev"][horse])),
                p_pred_preweight=maps["legacy"][horse],
            )
            logits.append(
                (
                    horse,
                    number,
                    math.fsum(beta[k] * math.log(component[k]) for k in beta),
                )
            )
        exps = [
            math.exp(score - max(s for _, _, s in logits)) for _, _, score in logits
        ]
        total = math.fsum(exps)
        calculated = [
            dict(
                horse_id=h,
                horse_number=n,
                probability=e / total,
                ev=e / total * prices[n],
            )
            for (h, n, _), e in zip(logits, exps, strict=True)
        ]
        for actual, expected_row in zip(content["rows"], calculated, strict=True):
            assert actual["horse_id"] == expected_row["horse_id"]
            assert math.isclose(
                actual["probability"],
                expected_row["probability"],
                rel_tol=0,
                abs_tol=1e-15,
            )
            assert math.isclose(
                actual["ev"], expected_row["ev"], rel_tol=0, abs_tol=1e-15
            )
        selected = sorted(
            [r for r in calculated if r["ev"] > 1],
            key=lambda r: (-r["ev"], r["horse_number"], r["horse_id"]),
        )[:10]
        assert content["tickets"] == [
            dict(horse_id=r["horse_id"], horse_number=r["horse_number"], stake_yen=100)
            for r in selected
        ]
        assert content["budget_yen"] == 1000
    historical = NATIVE.historical_replay(root)
    report = load(root / "report.json")
    assert (
        report["new_collection"]
        is report["database_access"]
        is report["evidence_admissible"]
        is report["can_adopt_model"]
        is False
    )
    return dict(
        status="independently_verified",
        origin=plan["origin"],
        new_collection=False,
        database_access=False,
        comparison_data_added=0,
        clocks_and_durable_receipts_verified=True,
        same_field_all_stages=True,
        decision_quote_age_seconds=(d - at(quote["received_at"])).total_seconds(),
        decision_window_verified=True,
        sealed_primary_admissible=False,
        actual_models=direct_native,
        independent_blend_ev_tickets=True,
        historical_official=historical,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.root)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")
