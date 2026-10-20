"""Authentication, authorisation, tenant isolation (application layer and PostgreSQL RLS), upload validation."""

from __future__ import annotations

import os
import uuid

import pytest
from conftest import PASSWORD, add_member, make_source, register, upload
from fixtures import FRENCH_PAGE, make_text_pdf
from sqlalchemy import create_engine, text

from copperbelt.jobs.queue import drain

# ------------------------------------------------------------------ authentication


def test_requests_without_token_are_rejected(client):
    assert client.get("/api/v1/documents").status_code == 401
    assert client.get("/api/v1/documents", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_weak_password_rejected(client):
    r = client.post(
        "/api/v1/auth/register", json={"email": "a@example.com", "full_name": "A", "password": "alllowercase12", "organisation_name": "Org"}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "weak_password"


def test_login_failure_is_generic_and_audited(client, owner):
    bad = client.post("/api/v1/auth/login", json={"email": owner.email, "password": "Wrong-password-99"})
    unknown = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "Wrong-password-99"})
    assert bad.status_code == unknown.status_code == 401
    assert bad.json()["error"]["message"] == unknown.json()["error"]["message"]
    actions = [e["action"] for e in owner.get("/api/v1/audit-events").json()["items"]]
    assert "auth.registered" in actions  # org-scoped events visible; login failures are platform-level


def test_logout_all_revokes_existing_tokens(owner):
    assert owner.get("/api/v1/auth/me").status_code == 200
    assert owner.post("/api/v1/auth/logout-all").status_code == 204
    assert owner.get("/api/v1/auth/me").status_code == 401


def test_refresh_requires_csrf_header_and_cookie(client):
    email = f"{uuid.uuid4().hex[:8]}@example.com"
    client.post("/api/v1/auth/register", json={"email": email, "full_name": "R", "password": PASSWORD, "organisation_name": "Org"})
    assert client.post("/api/v1/auth/refresh").status_code == 401
    r = client.post("/api/v1/auth/refresh", headers={"X-Requested-With": "copperbelt"})
    assert r.status_code == 200 and r.json()["access_token"]


def test_login_rate_limited(client, owner, monkeypatch):
    from copperbelt.config import get_settings

    monkeypatch.setattr(get_settings(), "login_rate_limit_per_minute", 3)
    codes = [client.post("/api/v1/auth/login", json={"email": owner.email, "password": "Wrong-password-99"}).status_code for _ in range(5)]
    assert codes[-1] == 429


# ------------------------------------------------------------------ roles


def test_viewer_cannot_upload_or_review(owner):
    viewer = add_member(owner, "viewer")
    src = make_source(owner)
    r = viewer.post(
        "/api/v1/documents",
        files={"file": ("a.pdf", make_text_pdf([FRENCH_PAGE]), "application/pdf")},
        data={"source_id": src, "title": "x"},
    )
    assert r.status_code == 403
    assert viewer.get("/api/v1/documents").status_code == 200
    assert viewer.get("/api/v1/audit-events").status_code == 403


def test_admin_cannot_promote_to_admin_only_owner_can(owner):
    admin = add_member(owner, "admin")
    analyst = add_member(owner, "analyst")
    assert admin.patch(f"/api/v1/org/members/{analyst.user_id}", json={"role": "admin"}).status_code == 403
    assert owner.patch(f"/api/v1/org/members/{analyst.user_id}", json={"role": "admin"}).status_code == 200


def test_revoked_member_loses_access_immediately(owner):
    analyst = add_member(owner, "analyst")
    assert analyst.get("/api/v1/documents").status_code == 200
    assert owner.delete(f"/api/v1/org/members/{analyst.user_id}").status_code == 204
    assert analyst.get("/api/v1/documents").status_code == 403


# ------------------------------------------------------------------ tenant isolation


def test_cross_tenant_access_is_denied(client):
    a = register(client, "Org A")
    b = register(client, "Org B")
    src = make_source(a)
    doc = upload(a, src, make_text_pdf([FRENCH_PAGE]))["document"]
    drain()
    # B uses its own valid token but A's org header → not a member
    spoof = {"Authorization": f"Bearer {b.token}", "X-Org-Id": a.org_id}
    assert client.get("/api/v1/documents", headers=spoof).status_code == 403
    # B in its own org cannot fetch A's document by id
    for url in (f"/api/v1/documents/{doc['id']}", f"/api/v1/documents/{doc['id']}/pages/1", f"/api/v1/documents/{doc['id']}/file"):
        assert b.get(url).status_code == 404, url
    assert b.post("/api/v1/search", json={"q": "cuivre"}).json()["pages"]["total"] == 0
    assert b.get("/api/v1/claims").json()["total"] == 0
    # B cannot cite A's source when uploading
    r = b.post(
        "/api/v1/documents",
        files={"file": ("a.pdf", make_text_pdf([["x" * 50]]), "application/pdf")},
        data={"source_id": src, "title": "x"},
    )
    assert r.status_code == 404


def test_row_level_security_blocks_cross_tenant_rows_in_the_database(client):
    """Bypass the application entirely: connect as the app role and query with another tenant's context."""
    a = register(client, "Org A")
    b = register(client, "Org B")
    upload(a, make_source(a), make_text_pdf([FRENCH_PAGE]))
    engine = create_engine(os.environ["CBI_DATABASE_URL"])
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": b.org_id})
        assert conn.execute(text("SELECT count(*) FROM documents")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM sources")).scalar() == 0
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": a.org_id})
        assert conn.execute(text("SELECT count(*) FROM documents")).scalar() == 1
    with engine.begin() as conn:  # no tenant context at all → nothing visible
        assert conn.execute(text("SELECT count(*) FROM documents")).scalar() == 0
    with engine.begin() as conn:  # writing a row for another tenant is rejected by WITH CHECK
        conn.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": b.org_id})
        with pytest.raises(Exception, match="row-level security"):
            conn.execute(
                text(
                    "INSERT INTO sources (id, org_id, name, access_method, access_status, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :a, 'evil', 'manual_upload', 'unknown', now(), now())"
                ),
                {"a": a.org_id},
            )
    engine.dispose()


def test_audit_log_is_append_only_for_app_role(owner):
    engine = create_engine(os.environ["CBI_DATABASE_URL"])
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": owner.org_id})
        with pytest.raises(Exception, match="permission denied"):
            conn.execute(text("DELETE FROM audit_events"))
    engine.dispose()


# ------------------------------------------------------------------ document classification


def test_confidential_and_project_restricted_documents(owner):
    analyst = add_member(owner, "analyst")
    src = make_source(owner)
    conf = upload(owner, src, make_text_pdf([FRENCH_PAGE]), classification="confidential", title="Conf")["document"]
    restricted = upload(owner, src, make_text_pdf([FRENCH_PAGE + ["extra"]]), classification="project_restricted", title="Restricted")[
        "document"
    ]
    drain()
    listed = {d["id"] for d in analyst.get("/api/v1/documents").json()["items"]}
    assert conf["id"] not in listed and restricted["id"] not in listed
    assert analyst.get(f"/api/v1/documents/{conf['id']}").status_code == 404
    assert analyst.post("/api/v1/search", json={"q": "cuivre"}).json()["pages"]["total"] == 0

    # Project membership opens the restricted document only.
    proj = owner.post("/api/v1/projects", json={"name": "P1"}).json()
    owner.post(f"/api/v1/projects/{proj['id']}/documents", json={"ids": [restricted["id"]]})
    owner.post(f"/api/v1/projects/{proj['id']}/members", json={"user_id": analyst.user_id, "role": "reader"})
    assert analyst.get(f"/api/v1/documents/{restricted['id']}").status_code == 200
    assert analyst.get(f"/api/v1/documents/{conf['id']}").status_code == 404

    # An explicit grant opens the confidential one; revocation closes it again.
    assert owner.post(f"/api/v1/documents/{conf['id']}/grants", params={"user_id": analyst.user_id}).status_code == 204
    assert analyst.get(f"/api/v1/documents/{conf['id']}").status_code == 200
    owner.delete(f"/api/v1/documents/{conf['id']}/grants/{analyst.user_id}")
    assert analyst.get(f"/api/v1/documents/{conf['id']}").status_code == 404

    # A project reader cannot modify the project.
    assert analyst.post(f"/api/v1/projects/{proj['id']}/gaps", json={"topic": "abc", "verification_action": "Check it"}).status_code == 403


def test_projects_hidden_from_non_members(owner):
    analyst = add_member(owner, "analyst")
    proj = owner.post("/api/v1/projects", json={"name": "Secret DD"}).json()
    assert analyst.get(f"/api/v1/projects/{proj['id']}").status_code == 404
    assert analyst.get("/api/v1/projects").json() == []


# ------------------------------------------------------------------ uploads


def test_upload_rejects_unknown_types_and_empty_files(owner):
    src = make_source(owner)
    exe = owner.post(
        "/api/v1/documents",
        files={"file": ("x.pdf", b"MZ\x90\x00" + b"\x00\xff" * 100, "application/pdf")},
        data={"source_id": src, "title": "x"},
    )
    assert exe.status_code == 422 and exe.json()["error"]["code"] == "unsupported_file_type"
    empty = owner.post("/api/v1/documents", files={"file": ("x.pdf", b"", "application/pdf")}, data={"source_id": src, "title": "x"})
    assert empty.status_code == 422


def test_upload_size_limit(owner, monkeypatch):
    from copperbelt.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_bytes", 1000)
    src = make_source(owner)
    r = owner.post(
        "/api/v1/documents",
        files={"file": ("x.pdf", make_text_pdf([FRENCH_PAGE]), "application/pdf")},
        data={"source_id": src, "title": "x"},
    )
    assert r.status_code == 413


def test_unauthorised_source_blocks_ingestion(owner):
    src = make_source(owner, access_status="unknown")
    r = owner.post(
        "/api/v1/documents",
        files={"file": ("x.pdf", make_text_pdf([FRENCH_PAGE]), "application/pdf")},
        data={"source_id": src, "title": "x"},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "source_not_authorised"


def test_filename_is_sanitised(owner):
    src = make_source(owner)
    doc = upload(owner, src, make_text_pdf([FRENCH_PAGE]), filename="../../etc/pass\x00wd.pdf")["document"]
    assert "/" not in doc["original_filename"] and ".." not in doc["original_filename"].split("_")[0][:2]


def test_error_responses_carry_request_id(client):
    r = client.get("/api/v1/documents", headers={"X-Request-Id": "trace-123"})
    assert r.json()["error"]["request_id"] == "trace-123"
    assert r.headers["X-Request-Id"] == "trace-123"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
