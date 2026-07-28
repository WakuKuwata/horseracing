"""Feature 086 live capture must fail before HTTP on a window-less artifact."""

from __future__ import annotations

import datetime
import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest
from horseracing_probability.chaos_artifact import ChaosArtifactPrimaryHorizonError

from horseracing_live import chaos_capture
from horseracing_live.chaos_capture import (
    FrozenEntry,
    capture_chaos,
    load_current_chaos_artifact,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ARTIFACT_DIR = _REPO_ROOT / "artifacts" / "chaos_bands"
_LEGACY_DIGEST = "f190e65cb9bb2d59d27982c8721f8f8e65e6c31e5b53d65d367b7ca569b72782"
_RACE_ID = "202607260101"
_RACE_DATE = datetime.date(2026, 7, 26)


class _SpyFetcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def get(self, url: str, *, use_cache: bool = True) -> str:
        self.calls.append((url, use_cache))
        raise AssertionError("window-less artifact must be rejected before HTTP")


class _Session:
    def get(self, _model, race_id: str):
        assert race_id == _RACE_ID
        return SimpleNamespace(
            race_id=race_id,
            race_date=_RACE_DATE,
            post_time=datetime.datetime(2026, 7, 26, 6, 0, tzinfo=datetime.UTC),
        )


def test_windowless_current_artifact_rejects_capture_before_http(
    tmp_path,
    monkeypatch,
) -> None:
    manifest_path = tmp_path / "approved.json"
    manifest_path.write_text(
        json.dumps(
            {
                "approved": [
                    {
                        "digest": _LEGACY_DIGEST,
                        "status": "active",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    load_legacy_current = partial(
        load_current_chaos_artifact,
        manifest_path=manifest_path,
        artifact_dir=_ARTIFACT_DIR,
    )

    with pytest.raises(ChaosArtifactPrimaryHorizonError):
        load_legacy_current(_RACE_DATE)

    monkeypatch.setattr(
        chaos_capture,
        "_capture_entries_complete",
        lambda _session, _race_id: True,
    )
    monkeypatch.setattr(
        chaos_capture,
        "_started_entries",
        lambda _session, _race_id: [
            FrozenEntry(horse_id=f"H{number:02d}", horse_number=number)
            for number in range(1, 5)
        ],
    )
    fetcher = _SpyFetcher()
    report = capture_chaos(
        _Session(),
        race_id=_RACE_ID,
        fetcher=fetcher,
        artifact=None,
        capture_trigger="explicit_command",
        capture_policy_version="capture_policy_v1",
        deadline=float("inf"),
        clock=lambda: datetime.datetime(
            2026,
            7,
            26,
            5,
            30,
            tzinfo=datetime.UTC,
        ),
        pending_check=lambda _session, _race_id: (True, "pending"),
        artifact_loader=load_legacy_current,
    )

    assert (report.status, report.reason) == ("rejected", "artifact_unavailable")
    assert fetcher.calls == []
