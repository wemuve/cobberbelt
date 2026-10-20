"""Background job reliability: retries, dead-lettering, stale-lock recovery, reprocessing."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from conftest import make_source, upload
from fixtures import ENGLISH_PAGE, FRENCH_PAGE, make_text_pdf
from sqlalchemy import create_engine, text

from copperbelt.jobs import queue
from copperbelt.jobs.queue import drain, run_one
from copperbelt.storage import get_store


def _owner_sql(sql: str, **params) -> None:  # noqa: ANN003
    eng = create_engine(os.environ["CBI_DATABASE_OWNER_URL"])
    with eng.begin() as c:
        c.execute(text(sql), params)
    eng.dispose()


def test_transient_failure_is_retried_then_succeeds(owner, monkeypatch):
    doc = upload(owner, make_source(owner), make_text_pdf([FRENCH_PAGE]))["document"]
    from copperbelt.ingestion import extract

    real = extract.extract
    calls = {"n": 0}

    def flaky(*a, **kw):  # noqa: ANN002, ANN003, ANN202
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("temporary storage hiccup")
        return real(*a, **kw)

    monkeypatch.setattr(extract, "extract", flaky)
    job = run_one("t")
    assert str(job.status) == "failed" and "temporary storage hiccup" in job.last_error
    st = owner.get(f"/api/v1/documents/{doc['id']}/status").json()
    assert st["status"] == "queued" and "hiccup" in st["processing_error"]
    _owner_sql("UPDATE jobs SET run_after = now()")  # skip the backoff
    job = run_one("t")
    assert str(job.status) == "succeeded"
    assert owner.get(f"/api/v1/documents/{doc['id']}/status").json()["status"] == "processed"


def test_permanent_failure_goes_dead_and_can_be_retried_by_admin(owner):
    doc = upload(owner, make_source(owner), make_text_pdf([FRENCH_PAGE]))["document"]
    # Corrupt the stored original: checksum verification must fail permanently.
    store = get_store()
    key = f"{owner.org_id}/documents/{doc['sha256']}"
    path = store._path(key)
    original = path.read_bytes()
    path.write_bytes(b"%PDF-1.4 tampered")
    job = run_one("t")
    assert str(job.status) == "dead" and "checksum" in job.last_error
    st = owner.get(f"/api/v1/documents/{doc['id']}/status").json()
    assert st["status"] == "failed" and st["retryable"] is True

    path.write_bytes(original)
    r = owner.post(f"/api/v1/jobs/{job.id}/retry")
    assert r.status_code == 200 and r.json()["status"] == "queued"
    drain()
    assert owner.get(f"/api/v1/documents/{doc['id']}/status").json()["status"] == "processed"


def test_stale_running_job_is_recovered(owner):
    upload(owner, make_source(owner), make_text_pdf([FRENCH_PAGE]))
    job = queue.claim_next("crashed-worker")
    assert job is not None
    assert queue.claim_next("other") is None  # locked
    _owner_sql("UPDATE jobs SET locked_at = :t", t=datetime.now(UTC) - timedelta(hours=2))
    recovered = run_one("rescuer")
    assert recovered.id == job.id and str(recovered.status) == "succeeded"


def test_reprocess_is_deduplicated_and_preserves_reviewed_claims(owner):
    doc = upload(owner, make_source(owner), make_text_pdf([FRENCH_PAGE, ENGLISH_PAGE]))["document"]
    drain()
    claims = owner.get("/api/v1/claims", params={"document_id": doc["id"]}).json()["items"]
    keep = claims[0]
    owner.post(f"/api/v1/claims/{keep['id']}/review", json={"decision": "accepted"})
    j1 = owner.post(f"/api/v1/documents/{doc['id']}/process", json={"force_ocr": True}).json()
    j2 = owner.post(f"/api/v1/documents/{doc['id']}/process", json={}).json()
    assert j1["id"] == j2["id"]  # active job reused, not duplicated
    drain()
    after = owner.get("/api/v1/claims", params={"document_id": doc["id"]}).json()["items"]
    assert any(c["id"] == keep["id"] and c["review_status"] == "accepted" for c in after)
    pages = owner.get(f"/api/v1/documents/{doc['id']}/pages").json()
    assert len(pages) == 2 and all(p["extraction_method"] == "ocr" for p in pages)


def test_rules_only_reextraction_keeps_pages_and_reviewed_claims(owner):
    doc = upload(owner, make_source(owner), make_text_pdf([FRENCH_PAGE, ENGLISH_PAGE]))["document"]
    drain()
    pages_before = owner.get(f"/api/v1/documents/{doc['id']}/pages").json()
    claims = owner.get("/api/v1/claims", params={"document_id": doc["id"]}).json()["items"]
    owner.post(f"/api/v1/claims/{claims[0]['id']}/review", json={"decision": "accepted"})
    r = owner.post(f"/api/v1/documents/{doc['id']}/reextract")
    assert r.status_code == 202
    drain()
    assert owner.get(f"/api/v1/jobs/{r.json()['id']}").json()["status"] == "succeeded"
    assert owner.get(f"/api/v1/documents/{doc['id']}/pages").json() == pages_before
    after = owner.get("/api/v1/claims", params={"document_id": doc["id"]}).json()
    assert after["total"] == len(claims)
    assert any(c["id"] == claims[0]["id"] and c["review_status"] == "accepted" for c in after["items"])


def test_heartbeat_keeps_long_jobs_from_being_reclaimed_and_fencing_discards_stale_results(owner):
    upload(owner, make_source(owner), make_text_pdf([FRENCH_PAGE]))
    job = queue.claim_next("worker-a")
    _owner_sql("UPDATE jobs SET locked_at = :t", t=datetime.now(UTC) - timedelta(hours=2))
    # A heartbeat from the owner refreshes the lock, so nobody can steal the job.
    assert queue.beat(job.id, "worker-a") is True
    assert queue.claim_next("worker-b") is None
    # Another worker cannot heartbeat a job it does not own.
    assert queue.beat(job.id, "worker-b") is False
    # Simulate worker A dying: lock goes stale, B reclaims and finishes.
    _owner_sql("UPDATE jobs SET locked_at = :t", t=datetime.now(UTC) - timedelta(hours=2))
    done = run_one("worker-b")
    assert done.id == job.id and str(done.status) == "succeeded"
    # A late progress report from A is refused rather than silently corrupting state.
    import pytest

    ctx = queue.JobContext(job, "worker-a")
    with pytest.raises(queue.LostLock):
        ctx.progress(0.5, "late")
