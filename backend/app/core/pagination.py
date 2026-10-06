"""
Keyset (cursor) pagination shared by the Phase 2 list endpoints.

Offset pagination (page/page_size) skips or repeats rows when new rows are
inserted between requests, and gets slower the deeper the page. Keyset
pagination orders by (timestamp, id) — `id` breaks ties between rows that
share a timestamp — and continues strictly after the last row returned.

The cursor is opaque to clients and signed: it carries only the sort value
and id of a row the caller was already allowed to see, and a forged or
edited cursor is rejected with 400 instead of being trusted as a filter.
Authorization and tenant scoping are NOT the cursor's job — callers must
pass an already-scoped query.
"""

import base64
import binascii
import hashlib
import hmac
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import literal, tuple_
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.orm import Query as OrmQuery

from app.core.config import settings

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 100
INVALID_CURSOR_DETAIL = "Cursor de paginação inválido."


class CursorPage[T](BaseModel):
    items: list[T]
    next_cursor: str | None


class CursorParams:
    """FastAPI dependency: `?cursor=...&limit=...`."""

    def __init__(
        self,
        cursor: str | None = Query(default=None, max_length=512),
        limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    ) -> None:
        self.cursor = cursor
        self.limit = limit


def _cursor_signing_key() -> bytes:
    # Derived subkey, same approach as the CSRF key in app/core/security.py:
    # no extra secret to configure, and no key shared across constructions.
    return hmac.new(settings.JWT_SECRET_KEY.encode(), b"myvita-cursor-key-v1", hashlib.sha256).digest()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signature(body: str) -> str:
    return _b64encode(hmac.new(_cursor_signing_key(), body.encode(), hashlib.sha256).digest())


def encode_cursor(sort_value: datetime, row_id: uuid.UUID) -> str:
    if sort_value.tzinfo is None or sort_value.utcoffset() is None:
        raise ValueError("Cursor sort values must be timezone-aware.")
    body = _b64encode(json.dumps({"v": sort_value.isoformat(), "id": str(row_id)}, separators=(",", ":")).encode())
    return f"{body}.{_signature(body)}"


def _invalid_cursor() -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_CURSOR_DETAIL)


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Returns (sort_value, id); raises HTTP 400 on anything malformed or unsigned."""
    try:
        body, signature = cursor.split(".", 1)
    except ValueError:
        raise _invalid_cursor() from None
    if not hmac.compare_digest(_signature(body), signature):
        raise _invalid_cursor()
    try:
        payload = json.loads(_b64decode(body))
        sort_value = datetime.fromisoformat(payload["v"])
        row_id = uuid.UUID(payload["id"])
    except (binascii.Error, ValueError, KeyError, TypeError):
        raise _invalid_cursor() from None
    if sort_value.tzinfo is None:
        raise _invalid_cursor()
    return sort_value, row_id


def paginate(
    query: OrmQuery[Any],
    *,
    sort_col: InstrumentedAttribute[Any],
    id_col: InstrumentedAttribute[Any],
    cursor: str | None,
    limit: int,
    descending: bool = True,
) -> tuple[list[Any], str | None]:
    """
    Applies keyset ordering/filtering to an already tenant-scoped query and
    returns (rows, next_cursor). `next_cursor` is None on the last page.
    """
    if not 1 <= limit <= MAX_PAGE_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_LIMIT}")
    key = tuple_(sort_col, id_col)
    if cursor is not None:
        sort_value, row_id = decode_cursor(cursor)
        boundary = tuple_(literal(sort_value, sort_col.type), literal(row_id, id_col.type))
        query = query.filter(key < boundary if descending else key > boundary)
    if descending:
        query = query.order_by(sort_col.desc(), id_col.desc())
    else:
        query = query.order_by(sort_col.asc(), id_col.asc())
    rows = query.limit(limit + 1).all()
    if len(rows) <= limit:
        return rows, None
    rows = rows[:limit]
    last = rows[-1]
    return rows, encode_cursor(getattr(last, sort_col.key), getattr(last, id_col.key))
