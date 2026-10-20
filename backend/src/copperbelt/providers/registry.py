"""Registry of external providers and their honest status."""

from __future__ import annotations

from copperbelt.config import get_settings
from copperbelt.providers.base import CheckResult, Provider, ProviderInfo
from copperbelt.providers.planet import planet
from copperbelt.providers.pvgis import pvgis


class LLMProvider(Provider):
    """Model provider adapter slot. Extraction currently uses deterministic rules; no LLM output
    is produced until a provider is configured, evaluated (docs/evaluation.md) and enabled."""

    info = ProviderInfo(
        id="llm",
        name="LLM provider (OpenAI / Anthropic)",
        purpose="Structured extraction and report drafting, always validated against cited evidence",
        requires=["CBI_LLM_PROVIDER", "CBI_LLM_API_KEY", "CBI_LLM_MODEL"],
        licence_notes="Confirm the provider's data-retention terms permit processing customer documents.",
        docs_url="docs/adr/0006-ai-boundaries.md",
        capabilities=[],
    )

    def configured(self) -> bool:
        s = get_settings()
        return s.llm_provider != "none" and s.llm_api_key is not None and bool(s.llm_model)

    def _check(self) -> CheckResult:
        return CheckResult("configured_unverified", "Live LLM check not implemented until a provider is selected")


class EmbeddingProvider(Provider):
    info = ProviderInfo(
        id="embeddings",
        name="Embedding provider",
        purpose="Semantic retrieval over document chunks",
        requires=["CBI_EMBEDDING_PROVIDER", "CBI_EMBEDDING_API_KEY", "CBI_EMBEDDING_MODEL"],
        licence_notes="Embeddings are derived data; confirm source licences permit it.",
        docs_url="docs/adr/0004-search.md",
        capabilities=[],
    )

    def configured(self) -> bool:
        s = get_settings()
        return s.embedding_provider != "none" and s.embedding_api_key is not None and bool(s.embedding_model)

    def _check(self) -> CheckResult:
        return CheckResult("configured_unverified", "Embedding adapter not yet implemented")


llm = LLMProvider()
embeddings = EmbeddingProvider()

PROVIDERS: dict[str, Provider] = {p.info.id: p for p in (planet, pvgis, llm, embeddings)}

# Data sources that are registered but have no connector, with the reason.
UNCONNECTED_SOURCES = [
    {"id": "zm_mining_cadastre", "name": "Zambia Mining Cadastre (Ministry of Mines and Minerals Development)",
     "status": "blocked", "reason": "Access method and terms not yet established; see docs/source-register.md"},
    {"id": "cd_cami", "name": "DRC Cadastre Minier (CAMI)", "status": "blocked",
     "reason": "Access method and terms not yet established; see docs/source-register.md"},
    {"id": "rdc_rocks", "name": "RDC Rocks", "status": "blocked",
     "reason": "No confirmed public API or licence for automated extraction or commercial reuse"},
    {"id": "unilu_archive", "name": "University of Lubumbashi geological archive", "status": "blocked",
     "reason": "Requires an institutional data-sharing agreement"},
]
