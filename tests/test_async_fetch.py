from pathlib import Path

import httpx
import pytest

from lexue_attention import core
from lexue_attention.auth import BitSsoTicketClient, BitSsoV4Client
from lexue_attention.core import FetchOptions
from lexue_attention.lexue import LexueClient


@pytest.mark.asyncio
async def test_fetch_events_uses_async_http_client(monkeypatch):
    ics_text = Path("tests/fixtures/lexue_sample.ics").read_text(encoding="utf-8")
    seen_urls: list[str] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, text=ics_text)

    def make_session() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handle_request))

    monkeypatch.setattr(core, "new_session", make_session)

    events = await core.fetch_events(
        FetchOptions(calendar_url="https://lexue.example/calendar.ics?token=secret")
    )

    assert [event.uid for event in events] == ["assignment-1@example", "quiz-2@example"]
    assert seen_urls == ["https://lexue.example/calendar.ics?token=secret"]


@pytest.mark.asyncio
async def test_ticket_login_keeps_one_async_session_across_redirects():
    seen_requests: list[tuple[str, str]] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        seen_requests.append((request.method, request.url.path))
        if request.url.path == "/cas/v1/tickets":
            return httpx.Response(
                201,
                headers={"Location": "https://sso.example/cas/v1/tickets/TGT-1"},
            )
        if request.url.path == "/cas/v1/tickets/TGT-1":
            return httpx.Response(200, text="ST-1")
        return httpx.Response(200, text="<html><title>Lexue</title></html>")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle_request),
        follow_redirects=True,
    ) as session:
        client = BitSsoTicketClient(
            session,
            sso_ticket_url="https://sso.example/cas/v1/tickets",
        )

        final_url = await client.login_for_service(
            "student",
            "password",
            "https://lexue.example/login/index.php",
        )

    assert final_url == "https://lexue.example/login/index.php?ticket=ST-1"
    assert seen_requests == [
        ("POST", "/cas/v1/tickets"),
        ("POST", "/cas/v1/tickets/TGT-1"),
        ("GET", "/login/index.php"),
    ]


@pytest.mark.asyncio
async def test_create_calendar_subscription_uses_v4_login_and_returns_durable_url(monkeypatch):
    seen: dict[str, object] = {}

    def make_session() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))

    async def fake_login(self, username, password, service_url, *, sms_code_callback=None, trust_device=False):
        seen.update(
            username=username,
            password=password,
            service_url=service_url,
            sms_code_callback=sms_code_callback,
        )
        return service_url

    async def fake_export(self, sesskey=None):
        return "https://lexue.example/calendar/export_execute.php?token=durable"

    async def callback(context):
        return "123456"

    monkeypatch.setattr(core, "new_session", make_session)
    monkeypatch.setattr(BitSsoV4Client, "login_for_service", fake_login)
    monkeypatch.setattr(LexueClient, "export_calendar_url", fake_export)

    calendar_url = await core.create_calendar_subscription(
        "student",
        "password",
        "https://lexue.example",
        sms_code_callback=callback,
    )

    assert calendar_url.endswith("token=durable")
    assert seen["service_url"] == "https://lexue.example/login/index.php"
    assert seen["sms_code_callback"] is callback
