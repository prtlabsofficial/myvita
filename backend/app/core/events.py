"""
In-process domain events.

A domain service calls `emit(db, DomainEvent(...))` after changing state;
registered handlers (notifications, from P2.2 on) react using the SAME
session, so their writes commit or roll back together with the change that
caused them. `emit` itself never commits.

This is deliberately not the audit trail: audit rows are written in their
own transaction by app.core.audit.record_audit_event so that failures are
still recorded. Events only describe successful domain changes.

Payloads carry identifiers only — never clinical content — because they
end up driving user-facing notifications.
"""

import enum
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from sqlalchemy.orm import Session


class EventType(str, enum.Enum):
    APPOINTMENT_CREATED = "appointment.created"
    APPOINTMENT_RESCHEDULED = "appointment.rescheduled"
    APPOINTMENT_CONFIRMED = "appointment.confirmed"
    APPOINTMENT_CANCELLED = "appointment.cancelled"
    APPOINTMENT_REQUEST_CREATED = "appointment_request.created"
    APPOINTMENT_REQUEST_ACCEPTED = "appointment_request.accepted"
    APPOINTMENT_REQUEST_DECLINED = "appointment_request.declined"
    MEDICAL_RECORD_SHARED = "medical_record.shared"
    RESULT_PUBLISHED = "result.published"
    MESSAGE_SENT = "message.sent"


# Only identifiers may travel in a payload.
ALLOWED_PAYLOAD_KEYS = frozenset(
    {
        "patient_id",
        "staff_id",
        "appointment_id",
        "appointment_request_id",
        "medical_record_id",
        "result_id",
        "thread_id",
        "message_id",
        "recipient_user_id",
    }
)

EventHandler = Callable[[Session, "DomainEvent"], None]


@dataclass(frozen=True)
class DomainEvent:
    type: EventType
    clinic_id: uuid.UUID
    actor_user_id: uuid.UUID | None = None
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.type, EventType):
            raise ValueError(f"Unknown domain event type: {self.type!r}")
        unexpected = set(self.payload) - ALLOWED_PAYLOAD_KEYS
        if unexpected:
            raise ValueError(f"Domain event payload keys not allowed: {sorted(unexpected)}")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


_handlers: dict[EventType, list[EventHandler]] = {}


def register(event_type: EventType, handler: EventHandler) -> None:
    _handlers.setdefault(event_type, []).append(handler)


def clear_handlers() -> None:
    """Test helper: forget every registered handler."""
    _handlers.clear()


def emit(db: Session, event: DomainEvent) -> None:
    """Runs handlers in registration order inside the caller's transaction. Never commits."""
    for handler in list(_handlers.get(event.type, ())):
        handler(db, event)
