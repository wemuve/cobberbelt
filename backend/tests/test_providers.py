"""Provider adapter tests.

These use httpx.MockTransport with responses shaped like the providers' published
API documentation. They verify OUR request construction and response handling.
They do NOT prove the live integration works; the live suite (pytest -m live) does,
and only when real credentials are present.
"""

from __future__ import annotations

import json
import os
from datetime import date

import httpx
import pytest
from pydantic import SecretStr

from copperbelt.config import get_settings
from copperbelt.errors import ProviderUnavailable, ValidationFailed
from copperbelt.providers.planet import PlanetProvider
from copperbelt.providers.pvgis import PVGISProvider

AOI = {"type": "Polygon", "coordinates": [[[27.4, -11.7], [27.5, -11.7], [27.5, -11.6], [27.4, -11.6], [27.4, -11.7]]]}


def test_unconfigured_providers_report_honestly(owner):
    data = owner.get("/api/v1/providers").json()
    by_id = {p["id"]: p for p in data["providers"]}
    assert by_id["planet"]["status"] == "unconfigured" and "CBI_PLANET_API_KEY" in by_id["planet"]["missing_configuration"]
    assert by_id["llm"]["status"] == "unconfigured"
    assert by_id["embeddings"]["status"] == "unconfigured"
    assert by_id["pvgis"]["status"] == "configured_unverified"  # no key needed, but never checked yet
    assert {s["status"] for s in data["unconnected_sources"]} == {"blocked"}


def test_planet_calls_refused_without_key(owner):
    r = owner.post("/api/v1/satellite/search", json={"geometry": AOI, "date_from": "2026-01-01", "date_to": "2026-02-01"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "provider_unconfigured"


def test_provider_check_records_unreachable_not_success(owner):
    p = PVGISProvider()
    p.transport = httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectError("no route", request=req)))
    result = p.check()
    assert result.status == "unreachable"
    assert p.status()["status"] == "unreachable"


def _planet(monkeypatch, handler) -> PlanetProvider:  # noqa: ANN001
    monkeypatch.setattr(get_settings(), "planet_api_key", SecretStr("test-key-not-real"))
    p = PlanetProvider()
    p.transport = httpx.MockTransport(handler)
    return p


def test_planet_search_request_and_response_mapping(monkeypatch, owner):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers.get("authorization"), json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "id": "20260115_081500_00_2479",
                        "geometry": AOI,
                        "_permissions": ["assets.ortho_analytic_4b:download"],
                        "properties": {
                            "item_type": "PSScene",
                            "acquired": "2026-01-15T08:15:00Z",
                            "cloud_cover": 0.05,
                            "clear_percent": 93,
                            "gsd": 3.7,
                            "instrument": "PSB.SD",
                            "quality_category": "standard",
                        },
                    }
                ],
            },
        )

    p = _planet(monkeypatch, handler)
    out = p.search_scenes(AOI, date(2026, 1, 1), date(2026, 1, 31), max_cloud=0.1)
    assert seen["url"].endswith("/data/v1/quick-search?_page_size=50")
    assert seen["auth"].startswith("Basic ")
    filters = {f["type"]: f for f in seen["body"]["filter"]["config"]}
    assert filters["RangeFilter"]["config"] == {"lte": 0.1}
    assert filters["DateRangeFilter"]["config"]["gte"] == "2026-01-01T00:00:00Z"
    assert out["count"] == 1 and out["scenes"][0]["gsd_m"] == 3.7


def test_planet_error_mapping(monkeypatch):
    p = _planet(monkeypatch, lambda req: httpx.Response(401, json={"message": "bad key"}))
    with pytest.raises(ProviderUnavailable) as e:
        p.search_scenes(AOI, date(2026, 1, 1), date(2026, 1, 2))
    assert e.value.code == "provider_auth"
    p = _planet(monkeypatch, lambda req: httpx.Response(429, headers={"Retry-After": "5"}))
    with pytest.raises(ProviderUnavailable) as e:
        p.search_scenes(AOI, date(2026, 1, 1), date(2026, 1, 2))
    assert e.value.code == "provider_rate_limited" and e.value.detail == {"retry_after": "5"}
    with pytest.raises(ValidationFailed):
        p.search_scenes(AOI, date(2026, 2, 1), date(2026, 1, 1))


def test_planet_orders_require_budget_and_send_idempotency_key(monkeypatch):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["key"] = req.headers.get("idempotency-key")
        return httpx.Response(202, json={"id": "order-123", "state": "queued", "created_on": "2026-10-20T00:00:00Z"})

    p = _planet(monkeypatch, handler)
    with pytest.raises(ProviderUnavailable, match="BUDGET"):
        p.create_order("t", ["a"], "PSScene", "analytic_sr_udm2", AOI, "k1")
    monkeypatch.setattr(get_settings(), "planet_monthly_budget_usd", 100.0)
    out = p.create_order("t", ["a"], "PSScene", "analytic_sr_udm2", AOI, "k1")
    assert seen["key"] == "k1" and out == {"order_id": "order-123", "state": "queued", "created_on": "2026-10-20T00:00:00Z"}


def test_planet_order_not_reported_delivered_until_success(monkeypatch):
    p = _planet(monkeypatch, lambda req: httpx.Response(200, json={"id": "o", "state": "running", "_links": {}}))
    assert p.get_order("o")["delivered"] is False


def test_pvgis_response_mapping(owner):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/PVcalc") and req.url.params["outputformat"] == "json"
        assert req.url.params["optimalangles"] == "1"
        return httpx.Response(
            200,
            json={
                "inputs": {"location": {"latitude": -15.4, "longitude": 28.3}, "meteo_data": {"radiation_db": "PVGIS-SARAH3"}},
                "outputs": {"totals": {"fixed": {"E_y": 1650.2, "H(i)_y": 2210.4, "SD_y": 40.1}}, "monthly": {"fixed": []}},
            },
        )

    p = PVGISProvider()
    p.transport = httpx.MockTransport(handler)
    out = p.pv_estimate(-15.4, 28.3)
    assert out["data_kind"] == "modelled_historical" and out["annual_energy_kwh"] == 1650.2
    assert "not a site measurement or forecast" in out["limitations"]


# ------------------------------------------------------------------ live suite (opt-in)

live = pytest.mark.skipif(not os.environ.get("CBI_RUN_LIVE"), reason="set CBI_RUN_LIVE=1 to call real providers")


@pytest.mark.live
@live
def test_live_pvgis():
    assert PVGISProvider().check().status == "live_verified"


@pytest.mark.live
@live
@pytest.mark.skipif(not os.environ.get("CBI_PLANET_API_KEY"), reason="no Planet key")
def test_live_planet():
    assert PlanetProvider().check().status == "live_verified"
