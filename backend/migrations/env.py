"""Alembic environment. Migrations run as the database owner role (CBI_DATABASE_OWNER_URL)."""

from __future__ import annotations

from logging.config import fileConfig

import geoalchemy2  # noqa: F401  (registers spatial types for autogenerate)
from alembic import context
from sqlalchemy import create_engine, pool

import copperbelt.models  # noqa: F401
from copperbelt.config import get_settings
from copperbelt.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

POSTGIS_INTERNAL = {"spatial_ref_sys", "topology", "layer"}


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001
    if type_ == "table" and name in POSTGIS_INTERNAL:
        return False
    return True


def run_migrations_online() -> None:
    url = config.attributes.get("url") or get_settings().database_owner_url
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported; run against a database.")
run_migrations_online()
