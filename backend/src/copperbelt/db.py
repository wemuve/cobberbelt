"""Database engine, sessions and tenant context.

Tenant isolation is enforced twice:

1. In the application layer, every tenant-scoped query filters by ``org_id``
   (see ``copperbelt.security.deps``).
2. In PostgreSQL, row-level security policies on tenant tables compare
   ``org_id`` with the transaction-local setting ``app.org_id``. The setting is
   applied at the start of every transaction from ``session.info["org_id"]``.

The application connects as a non-superuser role so RLS is not bypassed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from uuid import UUID

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from copperbelt.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@event.listens_for(Session, "after_begin")
def _apply_tenant_context(session: Session, transaction, connection) -> None:  # noqa: ANN001
    org_id = session.info.get("org_id")
    bypass = session.info.get("system", False)
    connection.execute(
        text("SELECT set_config('app.org_id', :org, true), set_config('app.system', :sys, true)"),
        {"org": str(org_id) if org_id else "", "sys": "on" if bypass else "off"},
    )


def new_session(org_id: UUID | None = None, *, system: bool = False) -> Session:
    """Create a session bound to a tenant, or a system session (worker, auth lookups)."""
    session = get_sessionmaker()()
    session.info["org_id"] = org_id
    session.info["system"] = system
    return session


def set_tenant(session: Session, org_id: UUID) -> None:
    """Switch the tenant for subsequent transactions and the current one."""
    session.info["org_id"] = org_id
    session.info["system"] = False
    if session.in_transaction():
        session.execute(
            text("SELECT set_config('app.org_id', :org, true), set_config('app.system', 'off', true)"),
            {"org": str(org_id)},
        )


@contextmanager
def system_session() -> Iterator[Session]:
    session = new_session(system=True)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
