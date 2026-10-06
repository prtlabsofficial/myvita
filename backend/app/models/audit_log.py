"""
Append-only audit trail — covers both:
  - security/administrative events (login, permission checks, CRUD on
    patients/staff/appointments/clinics)
  - clinical access events (staff viewing a patient's record, a patient
    viewing their own record)

These are kept in ONE table (not two) on purpose: they're the same shape
(who, what, when, on what, with what result) and every query a real
investigation needs ("who touched this patient's record, and when?") spans
both anyway. Splitting them would mean either two tables joined for every
investigation, or duplicated logging code — see AuditAction below for how
the two categories are told apart when needed (by prefix/import site, not
by a second table).

STRICT RULE: never put clinical content, passwords, JWTs, cookies, CSRF
tokens, or full request/response bodies in `metadata`. Only identifiers
(a patient's UUID, not their name; a resource type string, not its payload).
This table is written to by app.core.audit.record_audit_event — write
through that helper, not directly, so the "no full session available"
requirement in its docstring is respected everywhere.

The database enforces append-only semantics (migration f6a7b8c9d0e1):
UPDATE/DELETE/TRUNCATE are rejected by triggers, except the referential
ON DELETE SET NULL of clinic_id/actor_user_id, and the runtime role is
granted INSERT/SELECT only (app/db_provisioning.py).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuditAction(str, enum.Enum):
    # --- security / administrative ---
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"
    PASSWORD_CHANGE = "password_change"
    PASSWORD_CHANGE_REQUIRED = "password_change_required"
    PASSWORD_RESET_REQUESTED = "password_reset_requested"
    PASSWORD_RESET_ISSUED = "password_reset_issued"
    PASSWORD_RESET_COMPLETED = "password_reset_completed"
    LOGIN_MFA_CHALLENGE = "login_mfa_challenge"
    MFA_ENABLED = "mfa_enabled"
    MFA_DISABLED = "mfa_disabled"
    MFA_RESET = "mfa_reset"
    MFA_FAILURE = "mfa_failure"
    MFA_RECOVERY_CODE_USED = "mfa_recovery_code_used"
    MFA_RECOVERY_CODES_REGENERATED = "mfa_recovery_codes_regenerated"
    INVITATION_CREATED = "invitation_created"
    INVITATION_ACCEPTED = "invitation_accepted"
    USER_CREATED = "user_created"
    USER_DISABLED = "user_disabled"
    USER_ENABLED = "user_enabled"
    PATIENT_CREATED = "patient_created"
    PATIENT_UPDATED = "patient_updated"
    PATIENT_DELETED = "patient_deleted"
    STAFF_CREATED = "staff_created"
    STAFF_UPDATED = "staff_updated"
    APPOINTMENT_CREATED = "appointment_created"
    APPOINTMENT_UPDATED = "appointment_updated"
    APPOINTMENT_CANCELLED = "appointment_cancelled"
    CLINIC_CREATED = "clinic_created"
    CONSENT_GRANTED = "consent_granted"
    CONSENT_REVOKED = "consent_revoked"
    MEDICAL_RECORD_CREATED = "medical_record_created"
    MEDICAL_RECORD_UPDATED = "medical_record_updated"
    MEDICATION_CREATED = "medication_created"
    MEDICATION_UPDATED = "medication_updated"
    MEDICATION_DEACTIVATED = "medication_deactivated"
    NOTIFICATION_READ = "notification_read"
    # --- Phase 2 (added together by migration a7b8c9d0e1f2) ---
    INVITATION_REVOKED = "invitation_revoked"
    INVITATION_RESENT = "invitation_resent"
    APPOINTMENT_CONFIRMED = "appointment_confirmed"
    APPOINTMENT_COMPLETED = "appointment_completed"
    APPOINTMENT_NO_SHOW = "appointment_no_show"
    APPOINTMENT_REQUEST_CREATED = "appointment_request_created"
    APPOINTMENT_REQUEST_ACCEPTED = "appointment_request_accepted"
    APPOINTMENT_REQUEST_DECLINED = "appointment_request_declined"
    APPOINTMENT_REQUEST_CANCELLED = "appointment_request_cancelled"
    APPOINTMENT_REQUEST_VIEWED = "appointment_request_viewed"
    MEDICAL_RECORD_FINALIZED = "medical_record_finalized"
    MEDICAL_RECORD_VISIBILITY_CHANGED = "medical_record_visibility_changed"
    RESULT_CREATED = "result_created"
    RESULT_UPDATED = "result_updated"
    RESULT_PUBLISHED = "result_published"
    RESULT_WITHDRAWN = "result_withdrawn"
    RESULT_VIEWED = "result_viewed"
    RESULT_VISIBILITY_CHANGED = "result_visibility_changed"
    RESULT_FILE_UPLOADED = "result_file_uploaded"
    RESULT_FILE_DOWNLOADED = "result_file_downloaded"
    RESULT_FILE_DELETED = "result_file_deleted"
    MESSAGE_THREAD_CREATED = "message_thread_created"
    MESSAGE_THREAD_VIEWED = "message_thread_viewed"
    MESSAGE_THREAD_CLOSED = "message_thread_closed"
    MESSAGE_SENT = "message_sent"
    NOTIFICATION_READ_ALL = "notification_read_all"
    CLINIC_DIRECTORY_DENIED = "clinic_directory_denied"
    PERMISSION_DENIED = "permission_denied"
    CSRF_FAILURE = "csrf_failure"
    RATE_LIMITED = "rate_limited"
    # --- clinical access (who looked at what) ---
    STAFF_VIEWED_PATIENT = "staff_viewed_patient"
    STAFF_VIEWED_APPOINTMENT = "staff_viewed_appointment"
    PATIENT_VIEWED_OWN_RECORD = "patient_viewed_own_record"
    CONSENT_VIEWED = "consent_viewed"
    MEDICAL_RECORD_VIEWED = "medical_record_viewed"
    MEDICATION_VIEWED = "medication_viewed"


class AuditResult(str, enum.Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        # These four are the actual investigation queries this table exists
        # for: "everything in clinic X", "everything user Y did", "everything
        # that happened to resource Z", "what happened around time T".
        # Deliberately not indexing `metadata` or `result` — nobody searches
        # audit history by those alone.
        Index("ix_audit_logs_clinic_id", "clinic_id"),
        Index("ix_audit_logs_actor_user_id", "actor_user_id"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # Nullable: some events genuinely have no clinic (a login attempt for an
    # email that doesn't exist) or no user (same case). SET NULL on delete
    # so a later user/clinic deletion can never silently delete history —
    # it just loses the live FK link, while actor_email keeps the identity.
    clinic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clinics.id", ondelete="SET NULL"), nullable=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    action: Mapped[AuditAction] = mapped_column(
        Enum(AuditAction, name="audit_action", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        index=True,
    )
    resource_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    result: Mapped[AuditResult] = mapped_column(
        Enum(AuditResult, name="audit_result", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )

    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)  # long enough for IPv6
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Minimal identifiers only — see module docstring. JSONB, not JSON: we
    # never query into it in a hot path, but JSONB is the Postgres-idiomatic
    # choice and costs nothing extra here.
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
