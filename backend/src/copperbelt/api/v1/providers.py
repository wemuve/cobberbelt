from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from copperbelt.api.schemas import GeometryIn
from copperbelt.errors import NotFound
from copperbelt.geo.service import to_storage_geometry
from copperbelt.providers.planet import planet
from copperbelt.providers.pvgis import pvgis
from copperbelt.providers.registry import PROVIDERS, UNCONNECTED_SOURCES
from copperbelt.security.deps import Ctx, require
from copperbelt.services.audit import record_ctx

router = APIRouter(tags=["providers"])


@router.get("/providers")
def list_providers(c: Ctx = Depends(require("providers:read"))) -> dict:
    return {"providers": [p.status() for p in PROVIDERS.values()], "unconnected_sources": UNCONNECTED_SOURCES}


@router.post("/providers/{provider_id}/check")
def check_provider(provider_id: str, c: Ctx = Depends(require("providers:check"))) -> dict:
    p = PROVIDERS.get(provider_id)
    if p is None:
        raise NotFound("Unknown provider")
    result = p.check()
    record_ctx(c, "provider.checked", "provider", provider_id, status=result.status)
    return p.status()


class SolarIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    peak_kw: float = Field(default=1.0, gt=0, le=100_000)
    loss_pct: float = Field(default=14.0, ge=0, le=100)
    angle: float | None = Field(default=None, ge=0, le=90)
    aspect: float = Field(default=0.0, ge=-180, le=180)


@router.post("/climate/solar-estimate")
def solar_estimate(body: SolarIn, c: Ctx = Depends(require("providers:use"))) -> dict:
    """Modelled PV yield from PVGIS for site screening (not a forecast or measurement)."""
    result = pvgis.pv_estimate(body.lat, body.lon, body.peak_kw, body.loss_pct, body.angle, body.aspect, org_id=c.org_id)
    record_ctx(c, "climate.solar_estimate", "location", f"{body.lat},{body.lon}")
    return result


class SatelliteSearchIn(BaseModel):
    geometry: GeometryIn
    date_from: date
    date_to: date
    max_cloud: float | None = Field(default=0.2, ge=0, le=1)
    item_types: list[str] = ["PSScene"]
    limit: int = Field(default=50, ge=1, le=250)


@router.post("/satellite/search")
def satellite_search(body: SatelliteSearchIn, c: Ctx = Depends(require("providers:use"))) -> dict:
    geom, _, warnings = to_storage_geometry(body.geometry.model_dump())
    from shapely.geometry import mapping

    result = planet.search_scenes(
        mapping(geom), body.date_from, body.date_to, item_types=body.item_types, max_cloud=body.max_cloud, limit=body.limit, org_id=c.org_id
    )
    record_ctx(c, "satellite.search", "provider", "planet", count=result["count"])
    return {**result, "warnings": warnings}
