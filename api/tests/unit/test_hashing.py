import uuid

import pytest

from app.domain.hashing import subject_hash

TEAM = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_TEAM = uuid.UUID("22222222-2222-2222-2222-222222222222")


def test_same_content_gives_the_same_hash() -> None:
    first = subject_hash(TEAM, "payment_investigation", "Refund stuck", "Order 48213")
    second = subject_hash(str(TEAM), "payment_investigation", "Refund stuck", "Order 48213")

    assert first == second
    assert len(first) == 64


@pytest.mark.parametrize(
    ("team", "item_type", "title", "description"),
    [
        (OTHER_TEAM, "incident", "Title", "Body"),
        (TEAM, "engineering", "Title", "Body"),
        (TEAM, "incident", "Title!", "Body"),
        (TEAM, "incident", "Title", "Body!"),
    ],
    ids=["team", "type", "title", "description"],
)
def test_every_material_field_changes_the_hash(
    team: uuid.UUID, item_type: str, title: str, description: str
) -> None:
    baseline = subject_hash(TEAM, "incident", "Title", "Body")

    assert subject_hash(team, item_type, title, description) != baseline


def test_moving_text_between_title_and_description_changes_the_hash() -> None:
    assert subject_hash(TEAM, "incident", "ab", "") != subject_hash(TEAM, "incident", "a", "b")
    assert subject_hash(TEAM, "incident", "a b", "c") != subject_hash(TEAM, "incident", "a", "b c")
