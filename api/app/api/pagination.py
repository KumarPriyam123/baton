"""Opaque keyset cursors (SPEC 8): base64 of the last row's sort values. No OFFSET anywhere."""

import base64
import binascii
import json
import uuid

from app.domain.errors import ValidationFailed

DEFAULT_LIMIT = 20  # directories: members, users
MAX_LIMIT = 50
ITEM_DEFAULT_LIMIT = 50  # items and events (SPEC 8)
ITEM_MAX_LIMIT = 100


def encode_cursor(name: str, row_id: uuid.UUID) -> str:
    raw = json.dumps([name, str(row_id)], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        name, row_id = json.loads(base64.urlsafe_b64decode(padded))
        return str(name), uuid.UUID(row_id)
    except (binascii.Error, ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValidationFailed(
            "That cursor is not valid.",
            errors=[{"field": "cursor", "message": "Invalid cursor.", "type": "value_error"}],
        ) from error
