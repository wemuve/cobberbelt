"""Deterministic claim-candidate extraction.

Produces *unreviewed* claim candidates with exact page offsets. It never creates
geographic features by itself: a coordinate found in a document becomes a
``coordinate`` claim that a reviewer can promote to a feature.

Confidence values here are fixed per rule (documented below), not model
probabilities. They express how specific the matched pattern is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from copperbelt.ingestion.vocabulary import FORMATION_TERMS, find_commodities, normalise

RULES_VERSION = "rules-1.0.0"

CONF_COORDINATE = 0.8  # explicit DMS/decimal coordinate with hemisphere letters
CONF_OCCURRENCE = 0.5  # commodity term + occurrence/mineralisation keyword in one sentence
CONF_DRILLING = 0.6
CONF_FORMATION = 0.4

OCCURRENCE_WORDS = re.compile(
    r"\b(occurrence|occurs|deposit|gisement|indice|indices|mineralis|mineraliz|ore|minerai|showing|"
    r"prospect|anomal|vein|filon|lode|orebody|amas|grade|teneur|assay|analyse)\w*",
)
DRILLING_WORDS = re.compile(r"\b(borehole|drill ?hole|drilling|sondage|forage|core|carotte|ddh|rc hole)\w*")
SAMPLE_WORDS = re.compile(r"\b(sample|echantillon|assay|analyse|ppm|g/t|% ?cu|% ?co)\b")

_DMS = (
    r"(?P<{p}deg>\d{{1,3}})\s*[°º]\s*(?:(?P<{p}min>\d{{1,2}}(?:[.,]\d+)?)\s*['’′]\s*)?"
    r"(?:(?P<{p}sec>\d{{1,2}}(?:[.,]\d+)?)\s*(?:\"|''|”|″)\s*)?\s*(?P<{p}hem>[NSEW]|Nord|Sud|Est|Ouest|North|South|East|West)\b"
)
COORD_PAIR = re.compile(_DMS.format(p="a") + r"[\s,;/]*(?:and|et)?[\s,;/]*" + _DMS.format(p="b"), re.IGNORECASE)
DECIMAL_PAIR = re.compile(
    r"(?<![\d.])(?P<lat>-?\d{1,2}\.\d{3,})\s*°?\s*(?P<lath>[NS])?\s*[,;/ ]\s*(?P<lon>-?\d{1,3}\.\d{3,})\s*°?\s*(?P<lonh>[EW])?(?![\d.])"
)

_HEM = {"n": 1, "nord": 1, "north": 1, "s": -1, "sud": -1, "south": -1, "e": 1, "est": 1, "east": 1, "w": -1, "ouest": -1, "west": -1}
_LAT_HEM = {"n", "s", "nord", "sud", "north", "south"}


@dataclass
class Candidate:
    claim_type: str
    statement: str
    char_start: int
    char_end: int
    confidence: float
    commodities: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)


def _num(v: str | None) -> float:
    return float(v.replace(",", ".")) if v else 0.0


def _dms(m: re.Match[str], p: str) -> tuple[float, str]:
    deg, mn, sec = _num(m.group(f"{p}deg")), _num(m.group(f"{p}min")), _num(m.group(f"{p}sec"))
    hem = m.group(f"{p}hem").lower()
    value = (deg + mn / 60 + sec / 3600) * _HEM[hem]
    return value, hem


def parse_coordinates(text: str) -> list[dict[str, Any]]:
    """Find coordinate pairs. Returns lat/lon in WGS84-style decimal degrees *as stated*.

    The datum of historical coordinates is frequently not WGS84 (e.g. Arc 1950 in
    Zambia/Katanga); that is recorded as unknown, not silently assumed.
    """
    out = []
    for m in COORD_PAIR.finditer(text):
        a, ah = _dms(m, "a")
        b, bh = _dms(m, "b")
        lat, lon = (a, b) if ah in _LAT_HEM else (b, a)
        if ah in _LAT_HEM and bh in _LAT_HEM:
            continue
        precision = "seconds" if m.group("asec") else "minutes" if m.group("amin") else "degrees"
        if abs(lat) <= 90 and abs(lon) <= 180:
            out.append(
                {
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "format": "dms",
                    "stated_precision": precision,
                    "verbatim": m.group(0),
                    "start": m.start(),
                    "end": m.end(),
                    "datum": "unknown",
                }
            )
    for m in DECIMAL_PAIR.finditer(text):
        lat, lon = float(m.group("lat")), float(m.group("lon"))
        if m.group("lath") and m.group("lath").upper() == "S":
            lat = -abs(lat)
        if m.group("lonh") and m.group("lonh").upper() == "W":
            lon = -abs(lon)
        if abs(lat) <= 90 and abs(lon) <= 180 and (m.group("lath") or m.group("lonh") or lat < 0):
            out.append(
                {
                    "lat": lat,
                    "lon": lon,
                    "format": "decimal",
                    "verbatim": m.group(0),
                    "stated_precision": f"{len(m.group('lat').split('.')[1])}dp",
                    "start": m.start(),
                    "end": m.end(),
                    "datum": "unknown",
                }
            )
    return out


_SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|\n|$)")


def sentences(text: str) -> list[tuple[int, int]]:
    spans = []
    for m in _SENTENCE.finditer(text):
        s, e = m.start(), m.end()
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e - s >= 15:
            spans.append((s, e))
    return spans


def extract_candidates(text: str) -> list[Candidate]:
    cands: list[Candidate] = []
    for c in parse_coordinates(text):
        # Extend the excerpt to the surrounding sentence for context.
        s = max(text.rfind(".", 0, c["start"]) + 1, text.rfind("\n", 0, c["start"]) + 1, 0)
        e = min([i for i in (text.find(".", c["end"]), text.find("\n", c["end"])) if i != -1] or [len(text)])
        stmt = text[s:e].strip()
        start = text.index(stmt, s) if stmt else c["start"]
        attrs = {k: c[k] for k in ("lat", "lon", "format", "stated_precision", "verbatim", "datum")}
        cands.append(
            Candidate("coordinate", stmt or c["verbatim"], start, start + len(stmt or c["verbatim"]), CONF_COORDINATE, attributes=attrs)
        )

    for s, e in sentences(text):
        sent = text[s:e]
        norm = normalise(sent)
        commodities = sorted({h[0] for h in find_commodities(sent)})
        terms = sorted({h[1] for h in find_commodities(sent)})
        if commodities and DRILLING_WORDS.search(norm):
            cands.append(Candidate("drilling_reference", sent, s, e, CONF_DRILLING, commodities, {"matched_terms": terms}))
        elif commodities and SAMPLE_WORDS.search(norm):
            cands.append(Candidate("sample_assay_reference", sent, s, e, CONF_OCCURRENCE, commodities, {"matched_terms": terms}))
        elif commodities and OCCURRENCE_WORDS.search(norm):
            cands.append(Candidate("mineral_occurrence", sent, s, e, CONF_OCCURRENCE, commodities, {"matched_terms": terms}))
        formations = [f for f in FORMATION_TERMS if len(f) > 3 and re.search(rf"\b{re.escape(f)}\b", norm)]
        if formations:
            cands.append(Candidate("formation", sent, s, e, CONF_FORMATION, commodities, {"formations": formations}))
    return cands
