"""Geometry validation, CRS transformation and spatial helpers.

Everything is stored in EPSG:4326 (lon/lat axis order, as GeoJSON). The CRS the
data arrived in and the verbatim original coordinates are kept alongside.
"""

from __future__ import annotations

from typing import Any

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError
from shapely import wkb
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform
from shapely.validation import explain_validity

from copperbelt.errors import ValidationFailed

STORAGE_CRS = CRS.from_epsg(4326)

# Approximate envelope of the initial geography (Zambia + DRC) used only to warn,
# never to reject: data elsewhere is valid, it is just outside the focus area.
FOCUS_BBOX = (11.5, -18.5, 34.0, 5.5)  # minx, miny, maxx, maxy


def parse_crs(value: str | int | None) -> CRS:
    if value in (None, ""):
        return STORAGE_CRS
    try:
        crs = CRS.from_user_input(value)
    except CRSError as exc:
        raise ValidationFailed(f"Unrecognised coordinate reference system: {value}", code="invalid_crs") from exc
    return crs


def to_storage_geometry(geojson: dict[str, Any], source_crs: str | int | None = None) -> tuple[BaseGeometry, str, list[str]]:
    """Validate a GeoJSON geometry, transform it to EPSG:4326 and return (geometry, crs_label, warnings)."""
    warnings: list[str] = []
    try:
        geom = shape(geojson)
    except Exception as exc:  # noqa: BLE001
        raise ValidationFailed(f"Invalid GeoJSON geometry: {exc}", code="invalid_geometry") from exc
    if geom.is_empty:
        raise ValidationFailed("Geometry is empty", code="invalid_geometry")
    if not geom.is_valid:
        raise ValidationFailed(f"Geometry is not valid: {explain_validity(geom)}", code="invalid_geometry")
    if geom.has_z:
        warnings.append("Z values dropped; elevation is stored separately")
        geom = transform(lambda x, y, z=None: (x, y), geom)

    crs = parse_crs(source_crs)
    label = crs.to_string()
    if not crs.equals(STORAGE_CRS):
        transformer = Transformer.from_crs(crs, STORAGE_CRS, always_xy=True)
        geom = transform(transformer.transform, geom)
        if not geom.is_valid:
            raise ValidationFailed("Geometry became invalid after CRS transformation", code="invalid_geometry")

    minx, miny, maxx, maxy = geom.bounds
    if not (-180 <= minx <= 180 and -180 <= maxx <= 180 and -90 <= miny <= 90 and -90 <= maxy <= 90):
        raise ValidationFailed(
            "Coordinates fall outside longitude/latitude bounds. Check the axis order (GeoJSON is lon, lat) "
            "and the source CRS.", code="coordinates_out_of_range",
        )
    fx0, fy0, fx1, fy1 = FOCUS_BBOX
    if maxx < fx0 or minx > fx1 or maxy < fy0 or miny > fy1:
        warnings.append("Geometry lies outside the Zambia/DRC focus area; check coordinates and axis order")
    return geom, label, warnings


def to_geojson(geom_value: Any) -> dict[str, Any] | None:
    """Convert a GeoAlchemy WKBElement to a GeoJSON dict."""
    if geom_value is None:
        return None
    data = geom_value.data if hasattr(geom_value, "data") else geom_value
    return mapping(wkb.loads(bytes(data)))


def area_km2(geom: BaseGeometry) -> float:
    """Geodesic area of a lon/lat geometry."""
    from pyproj import Geod

    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        return 0.0
    area, _ = Geod(ellps="WGS84").geometry_area_perimeter(geom)
    return round(abs(area) / 1e6, 4)


def default_uncertainty_m(precision: str, stated_precision: str | None = None) -> float | None:
    """Conservative default horizontal uncertainty when a source does not state one.

    Values reflect coordinate *resolution* only (a minute of arc ≈ 1.85 km), not the
    unknown error of the original survey or datum. Reviewers should override.
    """
    if stated_precision == "degrees":
        return 111_000.0
    if stated_precision == "minutes":
        return 1_850.0
    if stated_precision == "seconds":
        return 31.0
    return {
        "surveyed": 5.0,
        "reported_coordinates": None,
        "georeferenced_map": 250.0,
        "approximate": 5_000.0,
        "district": 50_000.0,
        "unknown": None,
    }.get(precision)
