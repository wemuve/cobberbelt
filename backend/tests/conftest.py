"""Test harness.

Runs against a real PostgreSQL + PostGIS database (``copperbelt_test``) migrated
with Alembic. The app connects as the non-superuser application role, so
row-level security is exercised exactly as in production. External providers
are never called: tests inject httpx.MockTransport, and such tests prove only
that our adapter handles the documented response shapes, not that a live
integration works.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

TEST_DB = os.environ.get("CBI_TEST_DB", "copperbelt_test")
HOST = os.environ.get("CBI_TEST_DB_HOST", "localhost")
os.environ["CBI_ENVIRONMENT"] = "test"
os.environ["CBI_DATABASE_URL"] = (
    f"postgresql+psycopg://copperbelt_app:{os.environ.get('CBI_TEST_APP_PASSWORD', 'devpassword')}@{HOST}:5432/{TEST_DB}"
)
os.environ["CBI_DATABASE_OWNER_URL"] = (
    f"postgresql+psycopg://copperbelt:{os.environ.get('CBI_TEST_OWNER_PASSWORD', 'devpassword')}@{HOST}:5432/{TEST_DB}"
)
os.environ.setdefault("CBI_RATE_LIMIT_PER_MINUTE", "100000")
os.environ.setdefault("CBI_LOGIN_RATE_LIMIT_PER_MINUTE", "100000")
for k in ("CBI_PLANET_API_KEY", "CBI_LLM_API_KEY", "CBI_EMBEDDING_API_KEY", "CBI_CLAMAV_HOST"):
    os.environ.pop(k, None)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

import copperbelt.services.documents  # noqa: E402, F401  (job handlers)
from copperbelt.config import get_settings  # noqa: E402
from copperbelt.storage import reset_store  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture(scope="session", autouse=True)
def migrated_db(tmp_path_factory) -> Iterator[None]:
    os.environ["CBI_STORAGE_LOCAL_ROOT"] = str(tmp_path_factory.mktemp("objects"))
    get_settings.cache_clear()
    reset_store()
    owner = create_engine(os.environ["CBI_DATABASE_OWNER_URL"])
    with owner.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO copperbelt"))
    owner.dispose()
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND, "migrations"))
    command.upgrade(cfg, "head")
    yield


TRUNCATE = (
    "TRUNCATE assessments, evidence_gaps, risks, project_features, project_documents, areas_of_interest, "
    "project_members, projects, review_decisions, feature_evidence, geo_features, claims, page_chunks, "
    "document_pages, document_access_grants, documents, sources, idempotency_records, jobs, provider_usage, "
    "provider_checks, audit_events, memberships, users, organisations CASCADE"
)


@pytest.fixture(autouse=True)
def clean_db(migrated_db) -> Iterator[None]:
    yield
    owner = create_engine(os.environ["CBI_DATABASE_OWNER_URL"])
    with owner.begin() as conn:
        conn.execute(text("ALTER TABLE assessments DISABLE TRIGGER trg_assessment_immutable"))
        conn.execute(text(TRUNCATE))
        conn.execute(text("ALTER TABLE assessments ENABLE TRIGGER trg_assessment_immutable"))
    owner.dispose()


@pytest.fixture
def client() -> TestClient:
    from copperbelt.api.ratelimit import limiter
    from copperbelt.main import app

    limiter.reset()
    return TestClient(app)


PASSWORD = "Correct-Horse-42-battery"


class Actor:
    """An authenticated user bound to one organisation."""

    def __init__(self, client: TestClient, token: str, org_id: str, user_id: str, email: str) -> None:
        self.client, self.token, self.org_id, self.user_id, self.email = client, token, org_id, user_id, email

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "X-Org-Id": self.org_id}

    def get(self, url: str, **kw):  # noqa: ANN201, ANN003
        return self.client.get(url, headers=self.headers, **kw)

    def post(self, url: str, **kw):  # noqa: ANN201, ANN003
        return self.client.post(url, headers=self.headers, **kw)

    def put(self, url: str, **kw):  # noqa: ANN201, ANN003
        return self.client.put(url, headers=self.headers, **kw)

    def patch(self, url: str, **kw):  # noqa: ANN201, ANN003
        return self.client.patch(url, headers=self.headers, **kw)

    def delete(self, url: str, **kw):  # noqa: ANN201, ANN003
        return self.client.delete(url, headers=self.headers, **kw)


def register(client: TestClient, org: str = "Test Org") -> Actor:
    email = f"{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/v1/auth/register", json={"email": email, "full_name": "Owner", "password": PASSWORD, "organisation_name": org})
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    return Actor(client, token, me["memberships"][0]["org_id"], me["id"], email)


def add_member(owner: Actor, role: str) -> Actor:
    email = f"{role}-{uuid.uuid4().hex[:6]}@example.com"
    r = owner.post("/api/v1/org/members", json={"email": email, "full_name": role.title(), "role": role, "initial_password": PASSWORD})
    assert r.status_code == 201, r.text
    token = owner.client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
    return Actor(owner.client, token, owner.org_id, r.json()["user_id"], email)


@pytest.fixture
def owner(client) -> Actor:  # noqa: ANN001
    return register(client)


def make_source(actor: Actor, access_status: str = "public_domain", name: str | None = None) -> str:
    r = actor.post(
        "/api/v1/sources",
        json={
            "name": name or f"Source {uuid.uuid4().hex[:6]}",
            "access_status": access_status,
            "licence": "Synthetic test content",
            "country": "CD",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def upload(actor: Actor, source_id: str, data: bytes, filename: str = "report.pdf", **fields) -> dict:  # noqa: ANN003
    form = {"source_id": source_id, "title": fields.pop("title", "Synthetic report"), **{k: str(v) for k, v in fields.items()}}
    r = actor.post("/api/v1/documents", files={"file": (filename, data, "application/octet-stream")}, data=form)
    assert r.status_code == 201, r.text
    return r.json()
