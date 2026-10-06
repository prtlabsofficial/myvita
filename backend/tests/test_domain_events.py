"""In-process domain events (app/core/events.py)."""

import uuid

import pytest

from app.core import events
from app.core.events import DomainEvent, EventType, emit, register
from app.models import Clinic


@pytest.fixture(autouse=True)
def _isolated_handlers():
    events.clear_handlers()
    yield
    events.clear_handlers()


def _event(**overrides):
    values = {"type": EventType.APPOINTMENT_CREATED, "clinic_id": uuid.uuid4()}
    values.update(overrides)
    return DomainEvent(**values)


def test_emit_without_handlers_is_a_no_op(db_session):
    emit(db_session, _event())


def test_handlers_run_in_registration_order_for_their_type_only(db_session):
    calls = []
    register(EventType.APPOINTMENT_CREATED, lambda db, e: calls.append("first"))
    register(EventType.APPOINTMENT_CREATED, lambda db, e: calls.append("second"))
    register(EventType.RESULT_PUBLISHED, lambda db, e: calls.append("other"))

    emit(db_session, _event())

    assert calls == ["first", "second"]


def test_unknown_event_type_is_rejected():
    with pytest.raises(ValueError, match="Unknown domain event type"):
        _event(type="appointment.created")  # plain string, not EventType


def test_payload_only_accepts_identifier_keys():
    _event(payload={"patient_id": uuid.uuid4(), "appointment_id": uuid.uuid4()})
    with pytest.raises(ValueError, match="not allowed"):
        _event(payload={"patient_id": uuid.uuid4(), "body": "clinical text"})


def test_event_and_payload_are_immutable():
    event = _event(payload={"patient_id": uuid.uuid4()})
    with pytest.raises(AttributeError):
        event.clinic_id = uuid.uuid4()  # type: ignore[misc]
    with pytest.raises(TypeError):
        event.payload["patient_id"] = uuid.uuid4()  # type: ignore[index]


def test_handler_writes_share_the_callers_transaction(engine):
    """emit never commits: rolling back the caller discards handler writes."""
    from sqlalchemy.orm import Session

    clinic_id = uuid.uuid4()
    register(EventType.APPOINTMENT_CREATED, lambda db, e: db.add(Clinic(id=e.clinic_id, name="via handler")))

    with Session(engine) as session:
        emit(session, _event(clinic_id=clinic_id))
        session.flush()
        assert session.get(Clinic, clinic_id) is not None
        session.rollback()

    with Session(engine) as session:
        assert session.get(Clinic, clinic_id) is None

    with Session(engine) as session:
        emit(session, _event(clinic_id=clinic_id))
        session.commit()
    with Session(engine) as session:
        assert session.get(Clinic, clinic_id) is not None
