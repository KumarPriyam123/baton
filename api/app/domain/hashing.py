"""Subject hash for approvals (SPEC 3.2, 4.4). Pure: no I/O.

An approval covers the material fields: team, type, title and description. Hashing a JSON list
keeps the encoding unambiguous: ("a", "b") and ("ab", "") cannot produce the same bytes.
"""

import hashlib
import json
from uuid import UUID


def subject_hash(team_id: UUID | str, item_type: str, title: str, description: str) -> str:
    payload = json.dumps(
        [str(team_id), str(item_type), title, description],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
