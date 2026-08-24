from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .auth import (
    BitSsoPageClient,
    BitSsoTicketClient,
    BitSsoV4Client,
    SmsCodeCallback,
    new_session,
)
from .config import AppConfig
from .ics import parse_lexue_ics
from .lexue import LexueClient
from .models import DdlEvent
from .reminder import Reminder, plan_reminders
from .state import EventState, JsonStateStore


@dataclass(frozen=True, slots=True)
class FetchOptions:
    username: str = ""
    password: str = ""
    calendar_url: str = ""
    lexue_base_url: str = "https://lexue.bit.edu.cn"
    auth_method: str = "android"
    sms_code_callback: SmsCodeCallback | None = None


@dataclass(slots=True)
class SyncResult:
    events: list[DdlEvent]
    new_events: list[DdlEvent]
    changed_events: list[DdlEvent]
    reminders: list[Reminder]
    state: dict[str, EventState]


async def fetch_events(options: FetchOptions) -> list[DdlEvent]:
    async with new_session() as session:
        client = LexueClient(session=session, base_url=options.lexue_base_url)

        calendar_url = options.calendar_url
        if not calendar_url:
            if not options.username or not options.password:
                raise ValueError("username/password or calendar_url is required")
            service_url = options.lexue_base_url.rstrip("/") + "/login/index.php"
            if options.auth_method == "android":
                await BitSsoV4Client(session).login_for_service(
                    options.username,
                    options.password,
                    service_url,
                    sms_code_callback=options.sms_code_callback,
                )
            elif options.auth_method == "ticket":
                await BitSsoTicketClient(session).login_for_service(options.username, options.password, service_url)
            elif options.auth_method == "page":
                await BitSsoPageClient(session).login_for_service(
                    options.username,
                    options.password,
                    options.lexue_base_url,
                )
            else:
                raise ValueError(f"unsupported auth_method: {options.auth_method}")
            calendar_url = await client.export_calendar_url()

        return parse_lexue_ics(await client.fetch_ics(calendar_url))


async def create_calendar_subscription(
    username: str,
    password: str,
    lexue_base_url: str = "https://lexue.bit.edu.cn",
    *,
    sms_code_callback: SmsCodeCallback | None = None,
) -> str:
    """Authorize Lexue once and return its durable iCalendar subscription URL."""

    service_url = lexue_base_url.rstrip("/") + "/login/index.php"
    async with new_session() as session:
        await BitSsoV4Client(session).login_for_service(
            username,
            password,
            service_url,
            sms_code_callback=sms_code_callback,
        )
        return await LexueClient(session=session, base_url=lexue_base_url).export_calendar_url()


async def sync_events(
    options: FetchOptions,
    state_path: str,
    now: datetime | None = None,
    milestones_hours: tuple[int, ...] = (72, 24, 6),
) -> SyncResult:
    events = await fetch_events(options)
    store = JsonStateStore(state_path)
    state, new_events, changed_events = await asyncio.to_thread(store.merge_events, events)
    reminders = plan_reminders(
        events=events,
        state=state,
        now=now or datetime.now(ZoneInfo("Asia/Shanghai")),
        milestones_hours=milestones_hours,
    )
    await asyncio.to_thread(store.save, state)
    return SyncResult(
        events=events,
        new_events=new_events,
        changed_events=changed_events,
        reminders=reminders,
        state=state,
    )


def fetch_options_from_config(config: AppConfig, **overrides: str) -> FetchOptions:
    return FetchOptions(
        username=overrides.get("username") or config.username,
        password=overrides.get("password") or config.password,
        calendar_url=overrides.get("calendar_url") or config.calendar_url,
        lexue_base_url=overrides.get("lexue_base_url") or config.lexue_base_url,
        auth_method=overrides.get("auth_method") or "android",
    )
