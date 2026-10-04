"""GET /api/v1/stream (SPEC 10) over a real socket: who hears about a change, and when."""

import types
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.db.tx import CommandTx, record_event, run_command
from app.domain.enums import EventKind
from tests.integration.test_collaboration import FARAH
from tests.integration.test_teams_members import ASHA, MEERA, PRIYA
from tests.support.items import created, new_key, patch_item, rows
from tests.support.live import SseReader, browser, live_server
from tests.support.seeded import SeededDatabase
from tests.support.workflow import ISHAAN, act

NO_EVENT_WAIT = 0.4


async def test_the_stream_needs_a_session(app_settings: Settings) -> None:
    async with live_server(app_settings) as base, httpx.AsyncClient(base_url=base) as anonymous:
        response = await anonymous.get("/api/v1/stream")
    assert response.status_code == 401


async def test_the_stream_is_an_unbuffered_event_stream(app_settings: Settings) -> None:
    async with live_server(app_settings) as base, browser(base, ASHA) as asha:
        reader = await SseReader(asha).open()
        try:
            assert reader.status == 200
            assert reader.headers is not None
            assert reader.headers["content-type"].startswith("text/event-stream")
            assert reader.headers["cache-control"] == "no-cache"
            assert reader.headers["x-accel-buffering"] == "no"
        finally:
            await reader.close()


async def test_a_team_member_hears_another_users_change_with_only_key_version_and_event_id(
    app_settings: Settings,
) -> None:
    async with (
        live_server(app_settings) as base,
        browser(base, MEERA) as meera,
        browser(base, ASHA) as asha,
    ):
        reader = await SseReader(asha).open()
        try:
            item = await created(meera)
            event = await reader.next()
            assert event.name == "item.changed"
            assert set(event.data) == {"key", "version", "event_id"}
            assert (event.data["key"], event.data["version"]) == (item["key"], 1)
            assert event.id == str(event.data["event_id"])
        finally:
            await reader.close()


async def test_a_member_who_cannot_see_a_confidential_item_gets_no_event_for_it(
    app_settings: Settings,
) -> None:
    async with (
        live_server(app_settings) as base,
        browser(base, MEERA) as meera,
        browser(base, ISHAAN) as lead,
        browser(base, FARAH) as member,
        browser(base, ASHA) as outsider,
    ):
        lead_stream = await SseReader(lead).open()
        member_stream = await SseReader(member).open()
        outsider_stream = await SseReader(outsider).open()
        try:
            item = await created(
                meera, team_key="CMP", type="compliance_request", title="Data export check"
            )
            assert item["confidential"] is True
            heard = await lead_stream.next()  # a lead may see it
            assert heard.data["key"] == item["key"]
            # One NOTIFY is offered to every client in one synchronous pass, so once the lead has
            # it the others have already been judged.
            assert member_stream.pending() == []
            assert outsider_stream.pending() == []
        finally:
            for reader in (lead_stream, member_stream, outsider_stream):
                await reader.close()


async def test_nothing_is_sent_for_a_command_that_rolls_back_but_the_commit_after_it_is(
    app_settings: Settings, seeded: SeededDatabase
) -> None:
    async with (
        live_server(app_settings) as base,
        browser(base, MEERA) as meera,
        browser(base, PRIYA) as priya,
    ):
        item = await created(meera)
        reader = await SseReader(priya).open()
        try:
            facts = (
                await rows(
                    seeded.url,
                    "SELECT id, team_id, version, requester_id, assignee_id, confidential "
                    "FROM work_items WHERE key = $1",
                    item["key"],
                )
            )[0]
            subject = types.SimpleNamespace(**facts)

            async def doomed(tx: CommandTx) -> None:
                await record_event(tx, subject, EventKind.FIELD_CHANGED)
                raise RuntimeError("rolled back after the event was written")

            engine = create_async_engine(app_settings.database_url)
            try:
                async with engine.connect() as conn:
                    with pytest.raises(RuntimeError):
                        await run_command(conn, doomed)
            finally:
                await engine.dispose()

            with pytest.raises(TimeoutError):
                await reader.next(NO_EVENT_WAIT)  # the NOTIFY went with the transaction

            changed = await patch_item(priya, item["key"], {"priority": 1}, version=1)
            assert changed.status_code == 200, changed.text
            event = await reader.next()
            assert (event.data["key"], event.data["version"]) == (item["key"], 2)
            assert reader.pending() == []
        finally:
            await reader.close()


async def test_a_priority_change_by_someone_else_reaches_a_listener_within_a_second(
    app_settings: Settings,
) -> None:
    async with (
        live_server(app_settings) as base,
        browser(base, MEERA) as meera,
        browser(base, PRIYA) as priya,
    ):
        item = await created(meera)
        reader = await SseReader(meera).open()
        try:
            await patch_item(priya, item["key"], {"priority": 1}, version=1)
            event = await reader.next(1.0)
            assert (event.data["key"], event.data["version"]) == (item["key"], 2)
        finally:
            await reader.close()


async def test_reconnecting_with_a_last_event_id_replays_the_visible_events_after_it(
    app_settings: Settings,
) -> None:
    async with (
        live_server(app_settings) as base,
        browser(base, MEERA) as meera,
        browser(base, PRIYA) as priya,
    ):
        item = await created(meera)
        live = await SseReader(priya).open()
        try:
            await patch_item(meera, item["key"], {"priority": 1}, version=1)
            seen = await live.next()
        finally:
            await live.close()
        # priya's stream is closed while this happens
        commented: Any = await act(
            meera,
            item["key"],
            "comments",
            {"body": "while you were away"},
            idempotency_key=new_key(),
        )
        assert commented.status_code == 201, commented.text
        after = await SseReader(priya, {"last_event_id": str(seen.id)}).open()
        try:
            replayed = await after.next()
            assert replayed.name == "item.changed"
            assert replayed.data["key"] == item["key"]
            assert int(replayed.id or 0) > int(seen.id or 0)
        finally:
            await after.close()


async def test_a_last_event_id_older_than_the_buffer_gets_a_resync(app_settings: Settings) -> None:
    async with live_server(app_settings) as base, browser(base, PRIYA) as priya:
        # The seed's events are older than anything this process has seen, so id 0 is "too old".
        reader = await SseReader(priya, {"last_event_id": "0"}).open()
        try:
            assert (await reader.next()).name == "resync"
        finally:
            await reader.close()
