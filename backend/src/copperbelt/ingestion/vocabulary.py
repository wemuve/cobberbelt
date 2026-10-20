"""Commodity and mineral vocabulary (English/French) used for normalisation and query expansion.

Mapping is deliberately conservative: a mineral name maps to the commodity it is
an ore of, but the original wording is always kept on the page and the claim.
Extend this table with domain review rather than by guesswork.
"""

from __future__ import annotations

import re
import unicodedata

# canonical commodity -> terms (lower-case, accents stripped at match time)
COMMODITY_TERMS: dict[str, list[str]] = {
    "copper": [
        "copper",
        "cuivre",
        "cupriferous",
        "cuprifere",
        "chalcopyrite",
        "chalcocite",
        "bornite",
        "malachite",
        "azurite",
        "chrysocolla",
        "cuprite",
        "covellite",
        "native copper",
    ],
    "cobalt": ["cobalt", "cobaltifere", "cobaltiferous", "heterogenite", "carrollite", "linnaeite", "cobaltite", "asbolane"],
    "uranium": ["uranium", "uraninite", "pitchblende", "uranifere"],
    "zinc": ["zinc", "sphalerite", "blende", "willemite", "smithsonite"],
    "lead": ["lead", "plomb", "galena", "galene", "cerussite"],
    "gold": ["gold", "or natif", "auriferous", "aurifere", "native gold"],
    "silver": ["silver", "argent", "argentiferous"],
    "germanium": ["germanium", "germanite", "renierite"],
    "lithium": ["lithium", "spodumene", "lepidolite", "petalite", "amblygonite"],
    "tin": ["tin", "etain", "cassiterite"],
    "tantalum": ["tantalum", "tantale", "coltan", "tantalite", "columbite-tantalite"],
    "manganese": ["manganese", "pyrolusite", "psilomelane"],
    "nickel": ["nickel", "pentlandite", "garnierite"],
    "iron": ["iron ore", "minerai de fer", "hematite", "magnetite"],
    "emerald": ["emerald", "emeraude"],
}

# Lithostratigraphic names used in the Central African Copperbelt literature.
FORMATION_TERMS = [
    "katanga supergroup",
    "supergroupe du katanga",
    "roan group",
    "groupe de roan",
    "roan",
    "mines subgroup",
    "sous-groupe des mines",
    "r.a.t.",
    "rat",
    "kundelungu",
    "nguba",
    "lower roan",
    "upper roan",
    "ore shale",
    "lufilian arc",
    "arc lufilien",
    "basement complex",
    "muva",
    "mwashia",
    "dipeta",
    "kamoya",
    "dolomitic shale",
    "s.d.b.",
    "r.s.f.",
    "d.strat",
]


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalise(text: str) -> str:
    """Lower-case, strip accents and collapse whitespace; used for matching and English FTS."""
    return re.sub(r"\s+", " ", strip_accents(text).lower()).strip()


_TERM_INDEX: list[tuple[re.Pattern[str], str, str]] = []
for commodity, terms in COMMODITY_TERMS.items():
    for term in terms:
        # word boundary match; allow simple plural
        _TERM_INDEX.append((re.compile(rf"\b{re.escape(term)}s?\b"), commodity, term))


def find_commodities(text: str) -> list[tuple[str, str, int, int]]:
    """Return (commodity, matched_term, start, end) for each match in normalised text."""
    norm = normalise(text)
    hits = []
    for pattern, commodity, term in _TERM_INDEX:
        # "or" alone is French for gold but also the English conjunction: never match it bare.
        for m in pattern.finditer(norm):
            hits.append((commodity, term, m.start(), m.end()))
    return sorted(hits, key=lambda h: h[2])


def canonical_commodity(term: str) -> str | None:
    t = normalise(term)
    for commodity, terms in COMMODITY_TERMS.items():
        if t == commodity or t in terms:
            return commodity
    return None


def expand_query_terms(term: str) -> list[str]:
    """Expand a commodity to its synonyms for search (e.g. 'cuivre' -> copper, chalcopyrite...)."""
    commodity = canonical_commodity(term)
    if not commodity:
        return [term]
    return [commodity, *COMMODITY_TERMS[commodity]]
