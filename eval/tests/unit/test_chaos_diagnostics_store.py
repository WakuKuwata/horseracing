from __future__ import annotations

import datetime

from horseracing_eval.diagnostics_store import (
    KIND_CHAOS_BANDS,
    save_chaos_bands_run,
)


class _RecordingSession:
    def __init__(self) -> None:
        self.added = []
        self.flush_count = 0

    def add(self, value) -> None:
        self.added.append(value)

    def flush(self) -> None:
        self.flush_count += 1


def test_save_chaos_bands_run_transcribes_payload_verbatim() -> None:
    session = _RecordingSession()
    report = {
        "header": {
            "role": "SECONDARY",
            "discovery_data": True,
        },
        "band_summary": [{"band": "t3_calm", "n": 12}],
    }

    run = save_chaos_bands_run(
        session,
        report,
        date_from=datetime.date(2024, 1, 1),
        date_to=datetime.date(2026, 12, 31),
        logic_version="chaosbands-v1",
    )

    assert run.kind == KIND_CHAOS_BANDS
    assert run.date_from == datetime.date(2024, 1, 1)
    assert run.date_to == datetime.date(2026, 12, 31)
    assert run.logic_version == "chaosbands-v1"
    assert run.payload is report
    assert session.added == [run]
    assert session.flush_count == 1
