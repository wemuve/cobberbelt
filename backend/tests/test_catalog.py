"""Catalogue harvester tests against a SYNTHETIC Django-REST-Framework-shaped catalogue.

The fake API below proves the harvester's behaviour (paging, politeness, licence gate,
host pinning, change detection, import). It does NOT prove that any real catalogue
(e.g. rdc.rocks) has this shape: that is established with the probe endpoint against
the live API, after which `verified_against_live_api` may be set.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import make_source, register
from fixtures import FRENCH_PAGE, make_text_pdf

from copperbelt.connectors import catalog as cat
from copperbelt.jobs.queue import drain

HOST = "https://catalog.example.test"
BASE = f"{HOST}/api/v1/catalog/documents/"
PDF = make_text_pdf([FRENCH_PAGE])


def synthetic_catalog(n_records: int = 5, page_size: int = 2, *, next_host: str = HOST, changed_title: str | None = None):
    records = [
        {
            "id": 100 + i,
            "title": f"SYNTHETIC report {i}",
            "authors": [{"name": f"Author {i}"}],
            "year": f"{1930 + i}",
            "lang": "fr",
            "kind": "rapport",
            "pages": 10 + i,
            "files": [{"url": f"/media/docs/{100 + i}.pdf"}],
            "rights": "Synthetic test rights statement",
        }
        for i in range(n_records)
    ]
    if changed_title:
        records[0]["title"] = changed_title
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if req.url.path.startswith("/media/docs/"):
            return httpx.Response(200, content=PDF, headers={"Content-Type": "application/pdf"})
        page = int(req.url.params.get("page", "1"))
        size = int(req.url.params.get("page_size", str(page_size)))
        chunk = records[(page - 1) * size : page * size]
        more = page * size < len(records)
        nxt = f"{next_host}/api/v1/catalog/documents/?page={page + 1}&page_size={size}" if more else None
        return httpx.Response(200, json={"count": len(records), "next": nxt, "previous": None, "results": chunk})

    return handler, calls


CONFIG = {
    "base_url": BASE,
    "page_size": 2,
    "min_interval_seconds": 0.2,
    "fields": {
        "title": "title",
        "authors": "authors",
        "year": "year",
        "language": "lang",
        "document_type": "kind",
        "page_count": "pages",
        "file_url": "files.0.url",
        "rights_statement": "rights",
    },
}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(cat.time, "sleep", lambda s: None)
    yield
    cat.TRANSPORT = None


def _setup(owner, access="authorised", **handler_kw):  # noqa: ANN001, ANN003
    handler, calls = synthetic_catalog(**handler_kw)
    cat.TRANSPORT = httpx.MockTransport(handler)
    src = make_source(owner, access_status=access)
    assert owner.put(f"/api/v1/sources/{src}/connector", json=CONFIG).status_code == 200
    return src, calls


def test_harvest_is_refused_without_an_access_basis(owner):
    src, calls = _setup(owner, access="unknown")
    r = owner.post(f"/api/v1/sources/{src}/harvest")
    assert r.status_code == 409 and r.json()["error"]["code"] == "source_not_authorised"
    assert owner.post(f"/api/v1/sources/{src}/connector/probe").status_code == 409
    assert calls == []  # not a single request was made to the source


def test_probe_reports_real_structure_without_storing(owner):
    src, calls = _setup(owner)
    p = owner.post(f"/api/v1/sources/{src}/connector/probe").json()
    assert p["top_level_keys"] == ["count", "next", "previous", "results"] and p["count"] == 5
    paths = {f["path"] for f in p["record_fields"]}
    assert {"title", "authors.0.name", "files.0.url", "rights"} <= paths
    assert p["current_mapping_preview"]["authors"] == ["Author 0"] and p["id_field_present"] is True
    assert owner.get(f"/api/v1/sources/{src}/catalog").json()["total"] == 0
    assert len(calls) == 1


def test_harvest_pages_through_and_detects_upstream_changes(owner):
    src, calls = _setup(owner)
    job = owner.post(f"/api/v1/sources/{src}/harvest").json()
    drain()
    res = owner.get(f"/api/v1/jobs/{job['id']}").json()
    assert res["status"] == "succeeded", res
    assert res["result"]["pages"] == 3 and res["result"]["new"] == 5 and res["result"]["complete"] is True
    assert res["result"]["field_coverage"]["file_url"] == "5/5"
    assert res["result"]["verified_against_live_api"] is False
    listing = owner.get(f"/api/v1/sources/{src}/catalog").json()
    first = listing["items"][0]
    assert first["title"] == "SYNTHETIC report 0" and first["year"] == 1930 and first["authors"] == ["Author 0"]
    assert first["file_url"] == f"{HOST}/media/docs/100.pdf" and first["document_id"] is None
    assert listing["connector_verified_against_live_api"] is False

    # Second run: nothing changed upstream.
    owner.post(f"/api/v1/sources/{src}/harvest")
    drain()
    jobs = owner.get("/api/v1/jobs", params={"limit": 5}).json()
    assert jobs[0]["result"]["unchanged"] == 5 and jobs[0]["result"]["new"] == 0

    # Upstream edits a title: detected and recorded, raw record kept.
    handler, _ = synthetic_catalog(changed_title="SYNTHETIC report 0 (revised)")
    cat.TRANSPORT = httpx.MockTransport(handler)
    owner.post(f"/api/v1/sources/{src}/harvest")
    drain()
    assert owner.get("/api/v1/jobs", params={"limit": 5}).json()[0]["result"]["changed"] == 1
    item = owner.get(f"/api/v1/sources/{src}/catalog", params={"q": "revised"}).json()["items"][0]
    assert item["changed_upstream_at"] is not None


def test_next_links_to_other_hosts_are_refused(owner):
    src, calls = _setup(owner, next_host="https://evil.example.test")
    job = owner.post(f"/api/v1/sources/{src}/harvest").json()
    drain()
    res = owner.get(f"/api/v1/jobs/{job['id']}").json()
    assert res["status"] == "dead" and "another host" in res["last_error"]
    assert not any("evil" in u for u in calls)
    # The first page was still committed: partial progress is durable.
    assert owner.get(f"/api/v1/sources/{src}/catalog").json()["total"] == 2


def test_rate_limit_responses_are_honoured(owner, monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(cat.time, "sleep", lambda s: slept.append(s))
    inner, _ = synthetic_catalog(n_records=1)
    state = {"n": 0}

    def handler(req):  # noqa: ANN001, ANN202
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return inner(req)

    src = make_source(owner)
    owner.put(f"/api/v1/sources/{src}/connector", json=CONFIG)
    cat.TRANSPORT = httpx.MockTransport(handler)
    owner.post(f"/api/v1/sources/{src}/harvest")
    drain()
    assert 7.0 in slept and owner.get(f"/api/v1/sources/{src}/catalog").json()["total"] == 1


def test_import_downloads_from_the_catalogue_host_and_ingests(owner):
    src, calls = _setup(owner)
    owner.post(f"/api/v1/sources/{src}/harvest")
    drain()
    rec = owner.get(f"/api/v1/sources/{src}/catalog").json()["items"][0]
    r = owner.post(f"/api/v1/sources/{src}/catalog/{rec['id']}/import", json={"country": "CD"})
    assert r.status_code == 201, r.text
    doc = r.json()["document"]
    assert doc["title"] == "SYNTHETIC report 0" and doc["publication_year"] == 1930
    drain()
    assert owner.get(f"/api/v1/documents/{doc['id']}/status").json()["status"] == "processed"
    assert owner.post(f"/api/v1/sources/{src}/catalog/{rec['id']}/import").status_code == 409
    assert owner.get(f"/api/v1/sources/{src}/catalog", params={"imported": True}).json()["total"] == 1


def test_catalogue_is_tenant_isolated(client, owner):
    src, _ = _setup(owner)
    owner.post(f"/api/v1/sources/{src}/harvest")
    drain()
    other = register(client, "Other Org")
    assert other.get(f"/api/v1/sources/{src}/catalog").status_code == 404
    assert other.post(f"/api/v1/sources/{src}/harvest").status_code == 404


def test_connector_config_rejects_unknown_target_fields(owner):
    src = make_source(owner)
    bad = {**CONFIG, "fields": {"secret_field": "x"}}
    assert owner.put(f"/api/v1/sources/{src}/connector", json=bad).status_code == 422
