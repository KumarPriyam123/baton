"""ETag and If-Match (SPEC 6.2): the item's `version` is the entity tag."""

import re
from typing import Annotated

from fastapi import Depends, Header

from app.domain.errors import PreconditionRequired, ValidationFailed

_IF_MATCH = re.compile(r'^(?:W/)?"?(\d{1,9})"?$')


def etag(version: int) -> str:
    return f'"{version}"'


def parse_if_match(raw: str | None) -> int:
    """428 when the header is missing, 400 when it is not a version we issued."""
    if raw is None or not raw.strip():
        raise PreconditionRequired(
            "Send the version you are editing in an If-Match header (the ETag from GET)."
        )
    found = _IF_MATCH.fullmatch(raw.strip())
    if found is None:
        raise ValidationFailed(
            'If-Match must be the ETag of the item, like "7".',
            errors=[{"field": "If-Match", "message": "Not a version.", "type": "value_error"}],
        )
    return int(found.group(1))


def if_match_header(
    if_match: Annotated[
        str | None,
        Header(
            alias="If-Match",
            description='The `ETag` of the item as you last saw it, like `"7"`. Required.',
        ),
    ] = None,
) -> int:
    return parse_if_match(if_match)


ExpectedVersion = Annotated[int, Depends(if_match_header)]
