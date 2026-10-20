"""European Commission JRC PVGIS adapter.

PVGIS provides modelled solar radiation, PV performance estimates and typical
meteorological year (TMY) data. It is NOT a weather forecast service and does
not provide live observations. Outputs are labelled ``modelled_historical``.
PVGIS needs no API key; it does apply rate limits (documented as ~30 req/s).
"""

from __future__ import annotations

from typing import Any

from copperbelt.config import get_settings
from copperbelt.errors import ProviderUnavailable, ValidationFailed
from copperbelt.providers.base import CheckResult, Provider, ProviderInfo, record_usage

# A neutral, stable check location (Lusaka) and a tiny request.
_CHECK_POINT = {"lat": -15.4, "lon": 28.3}


class PVGISProvider(Provider):
    info = ProviderInfo(
        id="pvgis",
        name="PVGIS (EC Joint Research Centre)",
        purpose="Solar resource, PV yield estimates and typical meteorological year data for site screening",
        requires=[],
        licence_notes="Free to use with attribution to the European Commission JRC; modelled data, not observations.",
        docs_url="https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/getting-started-pvgis/api-non-interactive-service_en",
        capabilities=["pv_estimate", "monthly_radiation", "tmy"],
    )

    def configured(self) -> bool:
        return bool(get_settings().pvgis_base_url)

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        base = get_settings().pvgis_base_url.rstrip("/")
        with self.client() as http:
            r = http.get(f"{base}/{endpoint}", params={**params, "outputformat": "json"})
        if r.status_code == 429:
            raise ProviderUnavailable("PVGIS rate limit reached; retry later", code="provider_rate_limited")
        if r.status_code >= 400:
            msg = r.json().get("message") if "json" in r.headers.get("content-type", "") else r.text[:300]
            raise ProviderUnavailable(f"PVGIS rejected the request ({r.status_code}): {msg}", code="provider_error")
        return r.json()

    def _check(self) -> CheckResult:
        try:
            data = self._get("PVcalc", {**_CHECK_POINT, "peakpower": 1, "loss": 14})
        except ProviderUnavailable as exc:
            return CheckResult("error", exc.message)
        yearly = data.get("outputs", {}).get("totals", {}).get("fixed", {}).get("E_y")
        if yearly is None:
            return CheckResult("error", "Unexpected PVGIS response shape")
        return CheckResult("live_verified", f"PVcalc OK at Lusaka check point (E_y={yearly} kWh/kWp)")

    @staticmethod
    def _validate_point(lat: float, lon: float) -> None:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValidationFailed("Latitude/longitude out of range")

    def pv_estimate(
        self,
        lat: float,
        lon: float,
        peak_kw: float = 1.0,
        loss_pct: float = 14.0,
        angle: float | None = None,
        aspect: float = 0.0,
        org_id=None,
    ) -> dict[str, Any]:  # noqa: ANN001
        self._validate_point(lat, lon)
        params: dict[str, Any] = {"lat": lat, "lon": lon, "peakpower": peak_kw, "loss": loss_pct, "aspect": aspect}
        if angle is None:
            params["optimalangles"] = 1
        else:
            params["angle"] = angle
        data = self._get("PVcalc", params)
        record_usage("pvgis", "PVcalc", 1, "request", org_id=org_id)
        fixed = data["outputs"]["totals"]["fixed"]
        return {
            "data_kind": "modelled_historical",
            "provider": "PVGIS",
            "inputs": data.get("inputs", {}),
            "annual_energy_kwh": fixed.get("E_y"),
            "annual_irradiation_kwh_m2": fixed.get("H(i)_y"),
            "year_to_year_variability_kwh": fixed.get("SD_y"),
            "monthly": data["outputs"].get("monthly", {}).get("fixed", []),
            "meta_radiation_database": data.get("inputs", {}).get("meteo_data", {}).get("radiation_db"),
            "attribution": "PVGIS © European Union, 2001–2026",
            "limitations": "Modelled from satellite-derived radiation databases; not a site measurement or forecast.",
        }


pvgis = PVGISProvider()
