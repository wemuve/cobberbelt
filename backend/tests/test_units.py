"""Pure-logic tests: coordinate parsing, vocabulary, CRS handling, uncertainty defaults, assessment sealing."""

from __future__ import annotations

import pytest

from copperbelt.errors import ValidationFailed
from copperbelt.geo.service import area_km2, default_uncertainty_m, to_storage_geometry
from copperbelt.ingestion.rules import extract_candidates, parse_coordinates
from copperbelt.ingestion.vocabulary import canonical_commodity, expand_query_terms, find_commodities, normalise
from copperbelt.search.service import _expanded_query_text
from copperbelt.services.projects import _seal, risk_band


@pytest.mark.parametrize(
    ("text", "lat", "lon", "precision"),
    [
        ("situé à 10°48' S, 26°36' E", -10.8, 26.6, "minutes"),
        ("at 11° 40' 30\" S and 27° 28' 00\" E", -11.675, 27.466667, "seconds"),
        ("12°32'Sud 27°10'Est", -12.533333, 27.166667, "minutes"),
        ("lon/lat 26°36' E, 10°48' S", -10.8, 26.6, "minutes"),
        ("position -10.8123, 26.6012", -10.8123, 26.6012, "4dp"),
        ("10.8123 S 26.6012 E", -10.8123, 26.6012, "4dp"),
    ],
)
def test_coordinate_parsing(text, lat, lon, precision):
    hits = parse_coordinates(text)
    assert len(hits) == 1, hits
    assert hits[0]["lat"] == pytest.approx(lat, abs=1e-5) and hits[0]["lon"] == pytest.approx(lon, abs=1e-5)
    assert hits[0]["stated_precision"] == precision and hits[0]["datum"] == "unknown"


def test_coordinate_parser_ignores_numbers_that_are_not_coordinates():
    assert parse_coordinates("Grades of 2.1250 % Cu over 12.5000 m were reported in 1962.") == []
    assert parse_coordinates("Two latitudes 10°48' S, 11°00' S cannot form a pair") == []


def test_vocabulary_normalisation_and_expansion():
    assert normalise("Minéralisation  CUPRIFÈRE") == "mineralisation cuprifere"
    assert canonical_commodity("cuivre") == "copper" and canonical_commodity("Hétérogénite") == "cobalt"
    assert "chalcopyrite" in expand_query_terms("cuivre")
    found = {c for c, *_ in find_commodities("Indices de malachite et d'hétérogénite")}
    assert found == {"copper", "cobalt"}
    # the French word "or" (gold) must not match the English conjunction
    assert not find_commodities("copper or cobalt") or all(c != "gold" for c, *_ in find_commodities("copper or cobalt"))
    assert "or" not in expand_query_terms("gold")[1:]


def test_query_expansion_text():
    assert _expanded_query_text("cuivre kolwezi").startswith("(copper or cuivre")
    assert '"native copper"' in _expanded_query_text("copper")


def test_candidates_have_exact_offsets():
    text = "Intro line.\nLe gisement de cuivre est situé à 10°48' S, 26°36' E. Les indices de malachite sont nombreux."
    for c in extract_candidates(text):
        assert text[c.char_start : c.char_end] == c.statement
        assert 0 <= c.confidence <= 1


def test_utm_geometry_is_transformed_to_wgs84():
    # UTM zone 35S (EPSG:32735) easting/northing near Lubumbashi
    geom, crs, warnings = to_storage_geometry({"type": "Point", "coordinates": [553000, 8710000]}, "EPSG:32735")
    assert crs == "EPSG:32735"
    assert geom.x == pytest.approx(27.49, abs=0.01) and geom.y == pytest.approx(-11.67, abs=0.01)
    assert warnings == []


def test_swapped_axis_order_is_rejected_or_flagged():
    with pytest.raises(ValidationFailed):
        to_storage_geometry({"type": "Point", "coordinates": [-10.8, 126.6]})  # lat,lon given as lon,lat beyond range
    _, _, warnings = to_storage_geometry({"type": "Point", "coordinates": [-10.8, 26.6]})  # plausible but swapped
    assert any("outside the Zambia/DRC focus area" in w for w in warnings)


def test_invalid_geometry_rejected():
    bowtie = {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]}
    with pytest.raises(ValidationFailed, match="not valid"):
        to_storage_geometry(bowtie)
    with pytest.raises(ValidationFailed, match="coordinate reference system"):
        to_storage_geometry({"type": "Point", "coordinates": [27, -11]}, "EPSG:999999")


def test_geodesic_area():
    geom, *_ = to_storage_geometry({"type": "Polygon", "coordinates": [[[27, -11], [28, -11], [28, -10], [27, -10], [27, -11]]]})
    assert area_km2(geom) == pytest.approx(12_090, rel=0.01)  # 1°×1° at ~10.5°S


def test_uncertainty_defaults_follow_stated_precision():
    assert default_uncertainty_m("reported_coordinates", "minutes") == 1850
    assert default_uncertainty_m("reported_coordinates", "seconds") == 31
    assert default_uncertainty_m("reported_coordinates") is None  # unknown stays unknown
    assert default_uncertainty_m("district") == 50_000


def test_assessment_seal_detects_tampering():
    snap = {"dossier": {"features": [{"name": "A", "uncertainty_m": 1850.0}]}, "method_version": "x"}
    seal = _seal(snap)
    assert _seal({"method_version": "x", "dossier": {"features": [{"uncertainty_m": 1850.0, "name": "A"}]}}) == seal
    snap["dossier"]["features"][0]["name"] = "B"
    assert _seal(snap) != seal


@pytest.mark.parametrize(
    ("score", "band"),
    [(1, "low"), (4, "low"), (5, "medium"), (9, "medium"), (10, "high"), (15, "high"), (16, "critical"), (25, "critical")],
)
def test_risk_bands(score, band):
    assert risk_band(score) == band
