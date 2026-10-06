"""Keyset pagination helper (app/core/pagination.py)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.pagination import (
    INVALID_CURSOR_DETAIL,
    CursorParams,
    _b64encode,
    _signature,
    decode_cursor,
    encode_cursor,
    paginate,
)
from app.models import Clinic

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _signed(raw_json: str) -> str:
    body = _b64encode(raw_json.encode())
    return f"{body}.{_signature(body)}"


def _seed(db, timestamps):
    clinics = [Clinic(id=uuid.uuid4(), name=f"Clínica {i}", created_at=ts) for i, ts in enumerate(timestamps)]
    db.add_all(clinics)
    db.flush()
    return clinics


def _all_pages(db, *, limit, descending=True):
    seen, cursor, pages = [], None, 0
    while True:
        rows, cursor = paginate(
            db.query(Clinic),
            sort_col=Clinic.created_at,
            id_col=Clinic.id,
            cursor=cursor,
            limit=limit,
            descending=descending,
        )
        seen.extend(rows)
        pages += 1
        if cursor is None:
            return seen, pages
        assert pages < 100, "pagination did not terminate"


def test_cursor_round_trip():
    row_id = uuid.uuid4()
    assert decode_cursor(encode_cursor(BASE, row_id)) == (BASE, row_id)


def test_encode_rejects_naive_datetime():
    with pytest.raises(ValueError):
        encode_cursor(datetime(2026, 1, 1), uuid.uuid4())


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "no-dot",
        "abc.def",
        encode_cursor(BASE, uuid.uuid4())[:-2] + "xx",  # signature tampered
        "!!!." + _signature("!!!"),  # signed but not base64/json
        _signed('{"v":"2026-01-01T00:00:00","id":"' + str(uuid.uuid4()) + '"}'),  # naive timestamp
        _signed('{"v":"2026-01-01T00:00:00+00:00"}'),  # missing id
        _signed('{"v":"2026-01-01T00:00:00+00:00","id":"not-a-uuid"}'),
    ],
)
def test_invalid_cursor_is_400(cursor):
    with pytest.raises(HTTPException) as exc:
        decode_cursor(cursor)
    assert exc.value.status_code == 400
    assert exc.value.detail == INVALID_CURSOR_DETAIL


def test_edited_payload_with_old_signature_is_rejected():
    body, signature = encode_cursor(BASE, uuid.uuid4()).split(".")
    forged = _b64encode(b'{"v":"2030-01-01T00:00:00+00:00","id":"' + str(uuid.uuid4()).encode() + b'"}')
    with pytest.raises(HTTPException):
        decode_cursor(f"{forged}.{signature}")


def test_paginate_rejects_out_of_range_limit(db_session):
    for limit in (0, 101):
        with pytest.raises(ValueError):
            paginate(db_session.query(Clinic), sort_col=Clinic.created_at, id_col=Clinic.id, cursor=None, limit=limit)


def test_empty_result(db_session):
    rows, cursor = paginate(
        db_session.query(Clinic), sort_col=Clinic.created_at, id_col=Clinic.id, cursor=None, limit=10
    )
    assert rows == [] and cursor is None


@pytest.mark.parametrize("descending", [True, False])
def test_ties_are_neither_skipped_nor_duplicated(db_session, descending):
    # 7 rows sharing one timestamp + 5 distinct ones, paged 3 at a time.
    timestamps = [BASE] * 7 + [BASE + timedelta(minutes=i) for i in range(1, 6)]
    clinics = _seed(db_session, timestamps)

    seen, pages = _all_pages(db_session, limit=3, descending=descending)

    ids = [c.id for c in seen]
    assert len(ids) == len(set(ids)) == len(clinics)
    assert pages == 4
    keys = [(c.created_at, c.id) for c in seen]
    assert keys == sorted(keys, reverse=descending)


def test_exact_multiple_ends_with_no_cursor(db_session):
    _seed(db_session, [BASE + timedelta(seconds=i) for i in range(4)])
    rows, cursor = paginate(
        db_session.query(Clinic), sort_col=Clinic.created_at, id_col=Clinic.id, cursor=None, limit=2
    )
    assert len(rows) == 2 and cursor is not None
    rows, cursor = paginate(
        db_session.query(Clinic), sort_col=Clinic.created_at, id_col=Clinic.id, cursor=cursor, limit=2
    )
    assert len(rows) == 2 and cursor is None


def test_respects_caller_scoping(db_session):
    # The helper must only narrow the query it is given, never widen it.
    clinics = _seed(db_session, [BASE + timedelta(seconds=i) for i in range(6)])
    allowed = {c.id for c in clinics[:3]}
    rows, cursor = paginate(
        db_session.query(Clinic).filter(Clinic.id.in_(allowed)),
        sort_col=Clinic.created_at,
        id_col=Clinic.id,
        cursor=None,
        limit=10,
    )
    assert {r.id for r in rows} == allowed and cursor is None


def _params_app():
    app = FastAPI()

    @app.get("/items")
    def items(params: CursorParams = Depends()):
        return {"cursor": params.cursor, "limit": params.limit}

    return TestClient(app)


def test_cursor_params_defaults_and_bounds():
    client = _params_app()
    assert client.get("/items").json() == {"cursor": None, "limit": 50}
    assert client.get("/items?limit=100&cursor=abc").json() == {"cursor": "abc", "limit": 100}
    assert client.get("/items?limit=0").status_code == 422
    assert client.get("/items?limit=101").status_code == 422
    assert client.get("/items?cursor=" + "a" * 513).status_code == 422
