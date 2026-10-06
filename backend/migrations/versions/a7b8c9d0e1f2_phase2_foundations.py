"""Phase 2 foundations: btree_gist extension and Phase 2 audit actions.

Adds every audit_action label Phase 2 needs in one step, so later Phase 2
migrations never have to ALTER the enum again. The list is frozen here on
purpose instead of being imported from app code: a migration must keep
meaning the same thing when the application changes.

btree_gist is a trusted extension (PostgreSQL 13+): the database owner can
create it without superuser. It backs the appointment overlap exclusion
constraint added later in Phase 2; nothing depends on it yet.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | None = None
depends_on: str | None = None

PHASE2_AUDIT_ACTIONS = (
    "invitation_revoked",
    "invitation_resent",
    "appointment_confirmed",
    "appointment_completed",
    "appointment_no_show",
    "appointment_request_created",
    "appointment_request_accepted",
    "appointment_request_declined",
    "appointment_request_cancelled",
    "appointment_request_viewed",
    "medical_record_finalized",
    "medical_record_visibility_changed",
    "result_created",
    "result_updated",
    "result_published",
    "result_withdrawn",
    "result_viewed",
    "result_visibility_changed",
    "result_file_uploaded",
    "result_file_downloaded",
    "result_file_deleted",
    "message_thread_created",
    "message_thread_viewed",
    "message_thread_closed",
    "message_sent",
    "notification_read_all",
    "clinic_directory_denied",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    for value in PHASE2_AUDIT_ACTIONS:
        op.execute(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Nothing uses btree_gist at this revision, so dropping it is safe.
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
    # PostgreSQL enum labels on audit_action remain for historical compatibility.
