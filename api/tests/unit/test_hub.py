"""The hub's bounds and its visibility filter, without a database."""

import uuid

from app.db.tx import NOTIFICATIONS_CHANNEL, NOTIFY_CHANNEL
from app.domain.enums import TeamRole
from app.domain.policy import ActorContext
from app.realtime.hub import Hub, ItemNotice, NotificationNudge, Resync

PAY, SUP = uuid.uuid4(), uuid.uuid4()


def person(
    *, role: TeamRole | None = None, team: uuid.UUID = PAY, admin: bool = False
) -> ActorContext:
    return ActorContext(uuid.uuid4(), admin, {team: role} if role else {})


def notice(
    event_id: int, *, team: uuid.UUID = PAY, prev: uuid.UUID | None = None, **extra: object
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "item_key": f"PAY-{event_id}",
        "version": 1,
        "team_id": str(team),
        "prev_team_id": str(prev) if prev else None,
        "requester_id": str(uuid.uuid4()),
        "assignee_id": None,
        "confidential": False,
    } | extra


def test_a_client_that_never_reads_gets_a_resync_and_a_bounded_queue() -> None:
    hub = Hub(queue_size=5)
    client = hub.connect(person(role=TeamRole.MEMBER))
    for event_id in range(1, 100):
        hub.publish(NOTIFY_CHANNEL, notice(event_id))
    assert client.queue.qsize() <= 5
    backlog = [client.queue.get_nowait() for _ in range(client.queue.qsize())]
    assert any(isinstance(m, Resync) for m in backlog)


def test_a_full_queue_does_not_stop_other_clients_hearing_events() -> None:
    hub = Hub(queue_size=2)
    slow = hub.connect(person(role=TeamRole.MEMBER))
    quick = hub.connect(person(role=TeamRole.MEMBER))
    for event_id in range(1, 10):
        hub.publish(NOTIFY_CHANNEL, notice(event_id))
        message = quick.queue.get_nowait()
        assert isinstance(message, ItemNotice)
        assert message.event_id == event_id
    assert slow.queue.qsize() <= 2


def test_confidential_items_reach_leads_and_the_assignee_not_other_members() -> None:
    hub = Hub()
    assignee = person(role=TeamRole.MEMBER)
    lead = hub.connect(person(role=TeamRole.LEAD))
    member = hub.connect(person(role=TeamRole.MEMBER))
    owner = hub.connect(assignee)
    other_team = hub.connect(person(role=TeamRole.LEAD, team=SUP))
    hub.publish(NOTIFY_CHANNEL, notice(1, confidential=True, assignee_id=str(assignee.user_id)))
    assert [c.queue.qsize() for c in (lead, member, owner, other_team)] == [1, 0, 1, 0]


def test_a_transfer_is_also_sent_to_the_team_the_item_left() -> None:
    hub = Hub()
    old_team = hub.connect(person(role=TeamRole.MEMBER, team=SUP))
    new_team = hub.connect(person(role=TeamRole.MEMBER, team=PAY))
    stranger = hub.connect(person(role=TeamRole.MEMBER, team=uuid.uuid4()))
    hub.publish(NOTIFY_CHANNEL, notice(1, team=PAY, prev=SUP))
    assert [c.queue.qsize() for c in (old_team, new_team, stranger)] == [1, 1, 0]


def test_a_notification_nudge_goes_only_to_its_user() -> None:
    hub = Hub()
    me = hub.connect(person(role=TeamRole.MEMBER))
    someone = hub.connect(person(role=TeamRole.MEMBER))
    hub.publish(NOTIFICATIONS_CHANNEL, {"user_id": str(me.user_id)})
    assert isinstance(me.queue.get_nowait(), NotificationNudge)
    assert someone.queue.empty()


def test_replay_inside_the_buffer_is_filtered_and_older_than_the_buffer_is_a_resync() -> None:
    hub = Hub(buffer_size=3)
    ctx = person(role=TeamRole.MEMBER)
    hub.reset(floor=0)
    for event_id in range(1, 8):  # ids 1-4 are evicted; 5, 6, 7 remain
        hub.publish(NOTIFY_CHANNEL, notice(event_id))
    hub.publish(NOTIFY_CHANNEL, notice(8, confidential=True))  # evicts 5; hidden from a member
    replayed = [m.event_id for m in hub.replay(ctx, 6) if isinstance(m, ItemNotice)]
    assert replayed == [7]
    assert hub.replay(ctx, 1) == [Resync()]
    assert hub.replay(ctx, 0) == [Resync()]


def test_reset_after_a_listener_reconnect_resyncs_every_client() -> None:
    hub = Hub()
    client = hub.connect(person(role=TeamRole.MEMBER))
    hub.reset(floor=50)
    assert client.queue.get_nowait() == Resync()
    assert hub.replay(person(role=TeamRole.MEMBER), 10) == [Resync()]
