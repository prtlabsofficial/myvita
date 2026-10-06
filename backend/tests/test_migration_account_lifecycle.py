"""
Migration f6a7b8c9d0e1 must refuse — without changing anything — to create
the case-insensitive e-mail index when existing accounts differ only by
letter case, and must apply cleanly once they are resolved.

Runs Alembic against the validated test database and always leaves it at
head again.
"""

import uuid

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from tests.conftest import TEST_DATABASE_URL, alembic_config

PREVIOUS = "e5f6a7b8c9d0"


def _insert_user(connection, email: str) -> None:
    connection.execute(
        text(
            "INSERT INTO users (id, email, hashed_password, full_name, role, is_active, token_epoch) "
            "VALUES (:id, :email, 'x', 'Legacy', 'patient', true, 0)"
        ),
        {"id": uuid.uuid4(), "email": email},
    )


def test_upgrade_stops_on_case_duplicates_and_changes_nothing():
    config = alembic_config()
    engine = create_engine(TEST_DATABASE_URL, future=True)
    try:
        command.downgrade(config, PREVIOUS)
        with engine.begin() as connection:
            _insert_user(connection, "Ana@Clinica.pt")
            _insert_user(connection, "ana@clinica.pt")

        with pytest.raises(RuntimeError, match="differ only by letter case"):
            command.upgrade(config, "head")

        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == PREVIOUS
            columns = connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'users'")
            ).scalars().all()
            assert "must_change_password" not in columns
            assert connection.execute(text("SELECT count(*) FROM users")).scalar() == 2

        with engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE email = 'Ana@Clinica.pt'"))
    finally:
        command.upgrade(config, "head")
        engine.dispose()

    with engine.connect() as connection:
        head = ScriptDirectory.from_config(config).get_current_head()
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == head
        triggers = connection.execute(
            text("SELECT tgname FROM pg_trigger WHERE tgrelid = 'audit_logs'::regclass AND NOT tgisinternal")
        ).scalars().all()
        assert set(triggers) == {"audit_logs_append_only", "audit_logs_no_truncate"}
    engine.dispose()
