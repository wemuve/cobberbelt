"""Catalogue harvesting for page-numbered JSON APIs (Django REST Framework style:
``?page=N&page_size=M`` → ``{"count", "next", "previous", "results": [...]}``).

* Metadata only. Listing a document in a catalogue never ingests it; import is a
  separate, per-document action.
* Licence-gated: a source must have an established access basis before any request.
* Polite: one request at a time, a minimum interval between requests, Retry-After
  honoured, bounded page count, ``next`` links followed only on the configured host.
* Field mapping is configuration, because each source names its fields differently.
  ``probe`` reports a source's real structure so the mapping can be set from data.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlparse, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field, HttpUrl, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from copperbelt.errors import Conflict, ProviderUnavailable, ValidationFailed
from copperbelt.jobs.queue import JobContext, PermanentJobError, handler
from copperbelt.models.evidence import INGESTIBLE_ACCESS, CatalogRecord, Source
from copperbelt.providers.base import record_usage
from copperbelt.services.audit import record

HARVEST_JOB = "source.harvest"
USER_AGENT = "CopperbeltIntelligence-CatalogHarvester/0.1 (+contact: operator of this deployment)"
MAPPABLE = ("title", "authors", "year", "language", "document_type", "page_count", "landing_url", "file_url", "rights_statement")

# Injected by tests (httpx.MockTransport); None means real network.
TRANSPORT: httpx.BaseTransport | None = None


class ConnectorConfig(BaseModel):
    base_url: HttpUrl
    page_param: str = "page"
    page_size_param: str = "page_size"
    page_size: int = Field(default=24, ge=1, le=200)
    results_key: str = "results"
    next_key: str | None = "next"
    count_key: str | None = "count"
    id_field: str = "id"
    fields: dict[str, str] = Field(default_factory=dict, description="platform field → dotted path in a source record")
    min_interval_seconds: float = Field(default=1.0, ge=0.2, le=60)
    max_pages: int = Field(default=200, ge=1, le=10_000)
    verified_against_live_api: bool = False

    @field_validator("fields")
    @classmethod
    def _known(cls, v: dict[str, str]) -> dict[str, str]:
        unknown = set(v) - set(MAPPABLE)
        if unknown:
            raise ValueError(f"Unknown target fields {sorted(unknown)}; allowed: {list(MAPPABLE)}")
        return v


def _get_path(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return None
    return obj


def _as_int(v: Any) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        digits = v.strip()[:4]
        return int(digits) if digits.isdigit() else None
    return None


def _as_authors(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [a.strip() for a in v.replace(" & ", ";").split(";") if a.strip()][:20]
    if isinstance(v, list):
        out = []
        for a in v:
            if isinstance(a, str):
                out.append(a.strip())
            elif isinstance(a, dict):
                name = a.get("name") or " ".join(filter(None, [a.get("first_name"), a.get("last_name")]))
                if name:
                    out.append(str(name).strip())
        return [a for a in out if a][:20]
    return []


def _abs_url(base: str, v: Any) -> str | None:
    if not isinstance(v, str) or not v.strip():
        return None
    if v.startswith(("http://", "https://")):
        return v[:1000]
    p = urlsplit(base)
    return urlunsplit((p.scheme, p.netloc, v if v.startswith("/") else "/" + v, "", ""))[:1000]


def map_record(cfg: ConnectorConfig, raw: dict[str, Any]) -> dict[str, Any]:
    f = cfg.fields
    get = lambda key: _get_path(raw, f[key]) if key in f else None  # noqa: E731
    title = get("title")
    return {
        "title": str(title)[:5000] if title is not None else None,
        "authors": _as_authors(get("authors")),
        "year": _as_int(get("year")),
        "language": (str(get("language"))[:20] if get("language") is not None else None),
        "document_type": (str(get("document_type"))[:100] if get("document_type") is not None else None),
        "page_count": _as_int(get("page_count")),
        "landing_url": _abs_url(str(cfg.base_url), get("landing_url")),
        "file_url": _abs_url(str(cfg.base_url), get("file_url")),
        "rights_statement": (str(get("rights_statement")) if get("rights_statement") is not None else None),
    }


class _Client:
    """Rate-limited JSON GETs restricted to the configured host."""

    def __init__(self, cfg: ConnectorConfig) -> None:
        self.cfg = cfg
        self.host = urlparse(str(cfg.base_url)).netloc
        self.http = httpx.Client(
            timeout=httpx.Timeout(30, connect=10),
            transport=TRANSPORT,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=False,
        )
        self._last = 0.0
        self.requests = 0

    def close(self) -> None:
        self.http.close()

    def get(self, url: str) -> dict[str, Any]:
        if urlparse(url).netloc != self.host:
            raise PermanentJobError(f"Refusing to follow a link to another host: {urlparse(url).netloc}")
        for attempt in range(4):
            wait = self.cfg.min_interval_seconds - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.requests += 1
            try:
                r = self.http.get(url)
            except httpx.TransportError as exc:
                raise ProviderUnavailable(f"Catalogue unreachable: {type(exc).__name__}: {exc}", code="provider_unreachable") from exc
            if r.status_code in (429, 502, 503, 504) and attempt < 3:
                retry_after = r.headers.get("Retry-After", "")
                time.sleep(min(60.0, float(retry_after)) if retry_after.isdigit() else 2.0 * (attempt + 1))
                continue
            if r.status_code in (401, 403):
                raise PermanentJobError(f"Catalogue refused access ({r.status_code}); access must be arranged with the source")
            if r.status_code >= 400:
                raise ProviderUnavailable(f"Catalogue error {r.status_code}", code="provider_error")
            try:
                data = r.json()
            except ValueError as exc:
                raise PermanentJobError("Catalogue did not return JSON") from exc
            if not isinstance(data, dict):
                raise PermanentJobError("Catalogue returned JSON that is not an object")
            return data
        raise ProviderUnavailable("Catalogue kept asking to slow down; try later", code="provider_rate_limited")


def _page_url(cfg: ConnectorConfig, page: int) -> str:
    p = urlsplit(str(cfg.base_url))
    query = dict(x.split("=", 1) for x in p.query.split("&") if "=" in x)
    query.update({cfg.page_param: str(page), cfg.page_size_param: str(cfg.page_size)})
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(query), ""))


def _require_permitted(source: Source) -> ConnectorConfig:
    if source.access_status not in INGESTIBLE_ACCESS:
        raise Conflict(
            f"Source '{source.name}' has access status '{source.access_status}'. Record permission for automated "
            "access (or the licence that allows it) before harvesting its catalogue.",
            code="source_not_authorised",
        )
    if not source.connector_config:
        raise ValidationFailed("Configure the catalogue connector for this source first", code="connector_unconfigured")
    return ConnectorConfig.model_validate(source.connector_config)


def probe(source: Source) -> dict[str, Any]:
    """Fetch page 1 and describe its real structure. Stores nothing."""
    cfg = _require_permitted(source)
    client = _Client(cfg)
    try:
        data = client.get(_page_url(cfg, 1))
    finally:
        client.close()
    results = data.get(cfg.results_key)
    sample = results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else None

    def describe(obj: Any, prefix: str = "", depth: int = 0) -> list[dict[str, Any]]:
        out = []
        if isinstance(obj, dict) and depth < 3:
            for k, v in obj.items():
                path = f"{prefix}{k}"
                out.append({"path": path, "type": type(v).__name__, "example": (str(v)[:120] if not isinstance(v, (dict, list)) else None)})
                out += describe(v, path + ".", depth + 1)
            return out
        if isinstance(obj, list) and obj and depth < 3:
            out += describe(obj[0], prefix + "0.", depth + 1)
        return out

    return {
        "top_level_keys": sorted(data.keys()),
        "results_key_found": isinstance(results, list),
        "count": data.get(cfg.count_key) if cfg.count_key else None,
        "next": data.get(cfg.next_key) if cfg.next_key else None,
        "results_on_page": len(results) if isinstance(results, list) else 0,
        "record_fields": describe(sample) if sample else [],
        "current_mapping_preview": map_record(cfg, sample) if sample else None,
        "id_field_present": bool(sample and _get_path(sample, cfg.id_field) is not None),
    }


def _upsert(db: Session, source: Source, cfg: ConnectorConfig, raw: dict[str, Any], now: datetime) -> str:
    ext = _get_path(raw, cfg.id_field)
    if ext is None or str(ext).strip() == "":
        return "skipped_no_id"
    ext = str(ext)[:200]
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    rec = db.scalar(select(CatalogRecord).where(CatalogRecord.source_id == source.id, CatalogRecord.external_id == ext))
    fields = map_record(cfg, raw)
    if rec is None:
        db.add(
            CatalogRecord(
                org_id=source.org_id,
                source_id=source.id,
                external_id=ext,
                raw=raw,
                content_hash=digest,
                first_seen_at=now,
                last_seen_at=now,
                **fields,
            )
        )
        return "new"
    rec.last_seen_at = now
    if rec.content_hash == digest:
        return "unchanged"
    rec.raw, rec.content_hash, rec.changed_upstream_at = raw, digest, now
    for k, v in fields.items():
        setattr(rec, k, v)
    return "changed"


@handler(HARVEST_JOB)
def harvest(job: JobContext) -> dict[str, Any]:
    db = job.session()
    try:
        source = db.get(Source, uuid.UUID(job.payload["source_id"]))
        if source is None:
            raise PermanentJobError("Source not found")
        try:
            cfg = _require_permitted(source)
        except (Conflict, ValidationFailed) as exc:
            raise PermanentJobError(exc.message) from exc
        max_pages = min(int(job.payload.get("max_pages") or cfg.max_pages), cfg.max_pages)
        client = _Client(cfg)
        counts = {"new": 0, "changed": 0, "unchanged": 0, "skipped_no_id": 0}
        coverage = dict.fromkeys(MAPPABLE, 0)
        total, pages, url = None, 0, _page_url(cfg, 1)
        try:
            while url and pages < max_pages:
                data = client.get(url)
                results = data.get(cfg.results_key)
                if not isinstance(results, list):
                    raise PermanentJobError(f"Response has no list under '{cfg.results_key}'; run a probe and fix the configuration")
                total = data.get(cfg.count_key) if cfg.count_key else total
                now = datetime.now(UTC)
                for raw in results:
                    if not isinstance(raw, dict):
                        continue
                    counts[_upsert(db, source, cfg, raw, now)] += 1
                    for k, v in map_record(cfg, raw).items():
                        coverage[k] += 1 if v not in (None, [], "") else 0
                db.commit()  # each page is durable; a failure resumes cleanly
                pages += 1
                job.progress(min(0.99, pages / max(1, max_pages)), f"Page {pages}, {sum(counts.values())} records")
                nxt = data.get(cfg.next_key) if cfg.next_key else None
                url = nxt if nxt else (None if not results or len(results) < cfg.page_size else _page_url(cfg, pages + 1))
        finally:
            client.close()
        seen = sum(counts.values()) - counts["skipped_no_id"]
        summary = {
            "pages": pages,
            "requests": client.requests,
            "records_reported_by_source": total,
            **counts,
            "complete": url is None,
            "stopped_at_max_pages": url is not None,
            "field_coverage": {k: f"{v}/{seen}" for k, v in coverage.items() if k in cfg.fields},
            "verified_against_live_api": cfg.verified_against_live_api,
        }
        record_usage("catalog:" + str(source.id), "harvest", client.requests, "request", org_id=source.org_id)
        record(
            db, "source.catalog_harvested", org_id=source.org_id, actor_id=None, entity_type="source", entity_id=source.id, detail=summary
        )
        db.commit()
        return summary
    finally:
        db.close()
