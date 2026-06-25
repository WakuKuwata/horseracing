"""T020 (US5): versioned OpenAPI contract + docs + consistent ErrorBody (SC-006)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

_EXPECTED_PATHS = {
    "/api/v1/health",
    "/api/v1/races",
    "/api/v1/races/{race_id}",
    "/api/v1/races/{race_id}/predictions",
    "/api/v1/races/{race_id}/odds",
    "/api/v1/races/{race_id}/recommendations",
}


def test_openapi_lists_all_versioned_paths(client):
    spec = client.get("/openapi.json").json()
    paths = set(spec["paths"].keys())
    assert _EXPECTED_PATHS <= paths
    # every path is versioned under /api/v1
    assert all(p.startswith("/api/v1") for p in paths)
    # response schemas present
    schemas = spec["components"]["schemas"]
    for name in ("RaceDetail", "PredictionResponse", "OddsResponse", "RecommendationResponse"):
        assert name in schemas


def test_docs_available(client):
    assert client.get("/docs").status_code == 200


def test_error_body_shape_consistent(client):
    # 422 (bad race_id), 404 (missing), 422 (query validation) all share {status, code, detail}
    for resp in (
        client.get("/api/v1/races/bad"),
        client.get("/api/v1/races/200806019999"),
        client.get("/api/v1/races", params={"page_size": 99999}),
    ):
        body = resp.json()
        assert set(body) == {"status", "code", "detail"}
        assert body["status"] == resp.status_code
