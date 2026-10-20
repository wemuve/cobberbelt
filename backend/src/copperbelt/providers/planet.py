"""Planet Labs adapter (Data API search, Orders API).

Status: implemented against Planet's published API shapes and covered by
contract tests with recorded-shape mocks. NOT yet verified against a live
account — it reports ``unconfigured`` until CBI_PLANET_API_KEY is set and a
live check passes. Product/asset availability depends on the account's
subscription; licence terms govern storage, display and redistribution.

Important limits: PlanetScope (4/8-band VNIR) supports surface-change and
vegetation analysis; it cannot identify subsurface deposits or provide
diagnostic mineralogy. Alteration mapping needs SWIR sensors (e.g. ASTER,
Sentinel-2 SWIR, hyperspectral) via a separate provider.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from copperbelt.config import get_settings
from copperbelt.errors import ProviderUnavailable, ValidationFailed
from copperbelt.providers.base import CheckResult, Provider, ProviderInfo, record_usage

SUPPORTED_ITEM_TYPES = {"PSScene", "SkySatCollect"}


class PlanetProvider(Provider):
    info = ProviderInfo(
        id="planet",
        name="Planet Labs",
        purpose="Search and order satellite imagery for areas of interest; change monitoring",
        requires=["CBI_PLANET_API_KEY"],
        licence_notes="Commercial licence. Confirm permitted analytical use, storage duration, display to end "
                      "customers and redistribution before enabling customer-facing imagery.",
        docs_url="https://docs.planet.com/develop/apis/",
        capabilities=["scene_search", "orders"],
    )

    def configured(self) -> bool:
        return get_settings().planet_api_key is not None

    def _client(self):  # noqa: ANN202
        self.require_configured()
        s = get_settings()
        return self.client(base_url=s.planet_base_url.rstrip("/"), auth=(s.planet_api_key.get_secret_value(), ""))

    @staticmethod
    def _raise_for(r) -> None:  # noqa: ANN001
        if r.status_code in (401, 403):
            raise ProviderUnavailable("Planet rejected the credentials or this account lacks access", code="provider_auth")
        if r.status_code == 429:
            raise ProviderUnavailable("Planet rate limit reached; retry later", code="provider_rate_limited",
                                      detail={"retry_after": r.headers.get("Retry-After")})
        if r.status_code >= 400:
            raise ProviderUnavailable(f"Planet error {r.status_code}: {r.text[:300]}", code="provider_error")

    def _check(self) -> CheckResult:
        with self._client() as http:
            r = http.get("/data/v1/item-types")
        if r.status_code in (401, 403):
            return CheckResult("error", "Credentials rejected by Planet")
        if r.status_code >= 400:
            return CheckResult("error", f"HTTP {r.status_code}")
        types = [t.get("id") for t in r.json().get("item_types", [])]
        return CheckResult("live_verified", f"Data API reachable; item types visible: {', '.join(types[:8])}")

    @staticmethod
    def build_search_filter(geometry: dict[str, Any], date_from: date, date_to: date, max_cloud: float | None) -> dict[str, Any]:
        if date_to < date_from:
            raise ValidationFailed("date_to must be on or after date_from")
        filters: list[dict[str, Any]] = [
            {"type": "GeometryFilter", "field_name": "geometry", "config": geometry},
            {"type": "DateRangeFilter", "field_name": "acquired",
             "config": {"gte": f"{date_from.isoformat()}T00:00:00Z", "lte": f"{date_to.isoformat()}T23:59:59Z"}},
        ]
        if max_cloud is not None:
            filters.append({"type": "RangeFilter", "field_name": "cloud_cover", "config": {"lte": max_cloud}})
        return {"type": "AndFilter", "config": filters}

    def search_scenes(self, geometry: dict[str, Any], date_from: date, date_to: date, *,
                      item_types: list[str] | None = None, max_cloud: float | None = 0.2, limit: int = 50,
                      org_id=None) -> dict[str, Any]:  # noqa: ANN001
        types = item_types or ["PSScene"]
        if bad := set(types) - SUPPORTED_ITEM_TYPES:
            raise ValidationFailed(f"Unsupported item types: {sorted(bad)}")
        body = {"item_types": types, "filter": self.build_search_filter(geometry, date_from, date_to, max_cloud)}
        with self._client() as http:
            r = http.post("/data/v1/quick-search", json=body, params={"_page_size": min(limit, 250)})
        self._raise_for(r)
        record_usage("planet", "quick-search", 1, "request", org_id=org_id)
        scenes = []
        for f in r.json().get("features", [])[:limit]:
            p = f.get("properties", {})
            scenes.append({
                "id": f.get("id"), "item_type": p.get("item_type"), "acquired": p.get("acquired"),
                "cloud_cover": p.get("cloud_cover"), "clear_percent": p.get("clear_percent"),
                "gsd_m": p.get("gsd") or p.get("pixel_resolution"), "satellite_id": p.get("satellite_id"),
                "instrument": p.get("instrument"), "quality_category": p.get("quality_category"),
                "geometry": f.get("geometry"), "assets_permitted": f.get("_permissions", []),
            })
        return {"provider": "planet", "scenes": scenes, "count": len(scenes), "request_filter": body["filter"]}

    def create_order(self, name: str, item_ids: list[str], item_type: str, product_bundle: str,
                     clip_geometry: dict[str, Any] | None, idempotency_key: str, org_id=None) -> dict[str, Any]:  # noqa: ANN001
        """Create an order. The caller must persist the returned order id and only treat
        delivery as successful once the order reaches state 'success'."""
        if not item_ids:
            raise ValidationFailed("At least one item id is required")
        s = get_settings()
        if s.planet_monthly_budget_usd is None:
            raise ProviderUnavailable("Set CBI_PLANET_MONTHLY_BUDGET_USD before placing billable orders", code="budget_unset")
        body: dict[str, Any] = {
            "name": name,
            "products": [{"item_ids": item_ids, "item_type": item_type, "product_bundle": product_bundle}],
            "metadata": {"stac": {}},
        }
        if clip_geometry:
            body["tools"] = [{"clip": {"aoi": clip_geometry}}]
        with self._client() as http:
            r = http.post("/compute/ops/orders/v2", json=body, headers={"Idempotency-Key": idempotency_key})
        self._raise_for(r)
        data = r.json()
        record_usage("planet", "order.create", len(item_ids), "items", org_id=org_id, detail={"order_id": data.get("id")})
        return {"order_id": data.get("id"), "state": data.get("state"), "created_on": data.get("created_on")}

    def get_order(self, order_id: str) -> dict[str, Any]:
        with self._client() as http:
            r = http.get(f"/compute/ops/orders/v2/{order_id}")
        self._raise_for(r)
        data = r.json()
        results = data.get("_links", {}).get("results", []) or []
        return {
            "order_id": data.get("id"), "state": data.get("state"), "last_message": data.get("last_message"),
            "delivered": data.get("state") == "success",
            "results": [{"name": x.get("name"), "expires_at": x.get("expires_at")} for x in results],
        }


planet = PlanetProvider()
