"""
Migration a7b8c9d0e1f2 (Phase 2 foundations): btree_gist + Phase 2 audit
actions, and a drift guard between the Python AuditAction enum and the
database's audit_action type. Always leaves the test database at head.
"""

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from app.models import AuditAction
from tests.conftest import TEST_DATABASE_URL, alembic_config

REVISION = "a7b8c9d0e1f2"
PREVIOUS = "f6a7b8c9d0e1"


def _state():
    engine = create_engine(TEST_DATABASE_URL, future=True)
    try:
        with engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            has_btree_gist = bool(
                connection.execute(text("SELECT count(*) FROM pg_extension WHERE extname = 'btree_gist'")).scalar()
            )
            labels = set(
                connection.execute(text("SELECT unnest(enum_range(NULL::audit_action))::text")).scalars()
            )
        return version, has_btree_gist, labels
    finally:
        engine.dispose()


def test_revision_is_the_single_head():
    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_heads() == [REVISION]
    assert script.get_revision(REVISION).down_revision == PREVIOUS


def test_head_has_btree_gist_and_matches_python_audit_actions():
    version, has_btree_gist, labels = _state()
    assert version == REVISION
    assert has_btree_gist
    # Drift guard: every Python value exists in Postgres and vice versa.
    assert labels == {action.value for action in AuditAction}


def test_downgrade_then_upgrade_round_trip():
    config = alembic_config()
    try:
        command.downgrade(config, PREVIOUS)
        version, has_btree_gist, labels = _state()
        assert version == PREVIOUS
        assert not has_btree_gist
        # Enum labels are intentionally kept on downgrade.
        assert "clinic_directory_denied" in labels
    finally:
        command.upgrade(config, "head")

    version, has_btree_gist, labels = _state()
    assert version == REVISION
    assert has_btree_gist
    assert labels == {action.value for action in AuditAction}
