"""Shared fixtures.

DB tests run against a dedicated `<db>_test` database on the same Postgres server
(created and migrated once per session). Each test runs inside a transaction that is
rolled back afterwards. DB tests are skipped when Postgres is not reachable.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from alembic import command
from app.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    base_url = make_url(get_settings().database_url)
    test_url = base_url.set(database=f"{base_url.database}_test")

    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{test_url.database}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{test_url.database}"'))
    except OperationalError as exc:
        pytest.skip(f"Postgres not reachable: {exc.orig}")
    finally:
        admin.dispose()

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["database_url"] = test_url.render_as_string(hide_password=False)
    command.upgrade(cfg, "head")

    engine = create_engine(test_url)
    yield engine
    engine.dispose()


@pytest.fixture
def alembic_config(db_engine: Engine) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["database_url"] = db_engine.url.render_as_string(hide_password=False)
    return cfg


@pytest.fixture
def session(db_engine: Engine) -> Iterator[Session]:
    with db_engine.connect() as conn:
        trans = conn.begin()
        sess = Session(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield sess
        finally:
            sess.close()
            trans.rollback()
