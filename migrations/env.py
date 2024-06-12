"""alembic env for audit_log migrations."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from src.audit import Base

config = context.config

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = os.environ.get("AUDIT_DB_URL", config.get_main_option("sqlalchemy.url"))
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    if os.environ.get("AUDIT_DB_URL"):
        cfg["sqlalchemy.url"] = os.environ["AUDIT_DB_URL"]
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
