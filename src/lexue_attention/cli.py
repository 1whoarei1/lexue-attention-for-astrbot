from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .auth import AuthError, BitSsoPageClient, BitSsoTicketClient, BitSsoV4Client, new_session
from .config import load_config
from .core import FetchOptions, sync_events
from .diagnostics import diagnose_login
from .ics import parse_lexue_ics
from .lexue import LexueClient
from .reminder import format_reminder


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and parse Lexue DDL calendar events.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="Fetch DDL events from Lexue.")
    fetch.add_argument("--config", default="config.toml", help="TOML config path. Defaults to config.toml.")
    fetch.add_argument("--calendar-url", help="Existing Lexue .ics subscription URL.")
    fetch.add_argument("--username", help="BIT SSO username. Defaults to BIT_SSO_USERNAME.")
    fetch.add_argument("--password", help="BIT SSO password. Prefer BIT_SSO_PASSWORD or --ask-password.")
    fetch.add_argument("--ask-password", action="store_true", help="Prompt for the BIT SSO password without echo.")
    fetch.add_argument("--ask-sms-code", action="store_true", help="Prompt for an email second-factor code when required.")
    fetch.add_argument("--lexue-base-url")
    fetch.add_argument("--auth-method", choices=("android", "ticket", "page"), default="android")
    fetch.add_argument("--debug-login", action="store_true", help="Print non-sensitive login progress details.")
    fetch.add_argument("--json", action="store_true", help="Print JSON instead of readable text.")

    diagnose = subparsers.add_parser("diagnose-login", help="Diagnose Lexue login without printing secrets.")
    diagnose.add_argument("--config", default="config.toml", help="TOML config path. Defaults to config.toml.")
    diagnose.add_argument("--username", help="BIT SSO username. Defaults to BIT_SSO_USERNAME.")
    diagnose.add_argument("--password", help="BIT SSO password. Prefer BIT_SSO_PASSWORD or --ask-password.")
    diagnose.add_argument("--ask-password", action="store_true", help="Prompt for the BIT SSO password without echo.")
    diagnose.add_argument("--lexue-base-url")

    sync = subparsers.add_parser("sync", help="Fetch events, update state, and print pending notifications.")
    sync.add_argument("--config", default="config.toml", help="TOML config path. Defaults to config.toml.")
    sync.add_argument("--calendar-url", help="Existing Lexue .ics subscription URL.")
    sync.add_argument("--username", help="BIT SSO username. Defaults to BIT_SSO_USERNAME.")
    sync.add_argument("--password", help="BIT SSO password. Prefer BIT_SSO_PASSWORD or --ask-password.")
    sync.add_argument("--ask-password", action="store_true", help="Prompt for the BIT SSO password without echo.")
    sync.add_argument("--ask-sms-code", action="store_true", help="Prompt for an email second-factor code when required.")
    sync.add_argument("--lexue-base-url")
    sync.add_argument("--auth-method", choices=("android", "ticket", "page"), default="android")
    sync.add_argument("--json", action="store_true", help="Print JSON summary.")

    args = parser.parse_args()
    if args.command == "fetch":
        asyncio.run(_fetch(args))
    elif args.command == "diagnose-login":
        asyncio.run(_diagnose_login(args))
    elif args.command == "sync":
        asyncio.run(_sync(args))


async def _fetch(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    lexue_base_url = args.lexue_base_url or config.lexue_base_url
    async with new_session() as session:
        client = LexueClient(session=session, base_url=lexue_base_url)

        calendar_url = args.calendar_url or config.calendar_url
        if not calendar_url:
            username, password = _resolve_credentials(args, config)
            service_url = _lexue_login_service_url(lexue_base_url)
            if args.auth_method == "android":
                final_url = await BitSsoV4Client(session).login_for_service(
                    username,
                    password,
                    service_url,
                    sms_code_callback=_sms_code_callback(args),
                )
            elif args.auth_method == "ticket":
                final_url = await BitSsoTicketClient(session).login_for_service(username, password, service_url)
            else:
                final_url = await BitSsoPageClient(session).login_for_service(
                    username,
                    password,
                    lexue_base_url,
                )
            if args.debug_login:
                _debug(f"service login final url: {_redact_url(final_url)}")
                _debug("exporting Lexue calendar URL...")
            calendar_url = await client.export_calendar_url()
            if args.debug_login:
                _debug(f"calendar url: {_redact_url(calendar_url)}")

        if args.debug_login:
            _debug("fetching and parsing ICS...")
        events = parse_lexue_ics(await client.fetch_ics(calendar_url))

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "uid": event.uid,
                        "title": event.title,
                        "description": event.description,
                        "course": event.course,
                        "due_at": event.due_at.isoformat(),
                    }
                    for event in events
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    for event in sorted(events, key=lambda item: item.due_at):
        print(format_reminder(event, now))
        print()


async def _diagnose_login(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    username, password = _resolve_credentials(args, config)
    lexue_base_url = args.lexue_base_url or config.lexue_base_url
    for line in await diagnose_login(username, password, lexue_base_url):
        print(line)


async def _sync(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    username, password = _resolve_optional_credentials(args, config)
    options = FetchOptions(
        username=username,
        password=password,
        calendar_url=args.calendar_url or config.calendar_url,
        lexue_base_url=args.lexue_base_url or config.lexue_base_url,
        auth_method=args.auth_method,
        sms_code_callback=_sms_code_callback(args),
    )
    result = await sync_events(options, config.state_path)
    if args.json:
        print(
            json.dumps(
                {
                    "events": len(result.events),
                    "new_events": [event.uid for event in result.new_events],
                    "changed_events": [event.uid for event in result.changed_events],
                    "reminders": [
                        {
                            "rule_id": item.rule_id,
                            "uid": item.event.uid,
                            "text": item.text,
                        }
                        for item in result.reminders
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    for event in result.new_events:
        print("[新增] " + format_reminder(event, datetime.now(ZoneInfo("Asia/Shanghai"))))
        print()
    for event in result.changed_events:
        print("[变更] " + format_reminder(event, datetime.now(ZoneInfo("Asia/Shanghai"))))
        print()
    for reminder in result.reminders:
        print("[提醒] " + reminder.text)
        print()


def _resolve_credentials(args: argparse.Namespace, config) -> tuple[str, str]:
    username = args.username or os.environ.get("BIT_SSO_USERNAME") or config.username
    password = args.password or os.environ.get("BIT_SSO_PASSWORD") or config.password
    if args.ask_password:
        password = getpass.getpass("BIT SSO password: ")
    if not username or not password:
        raise SystemExit(
            "Credentials are required. Use --username plus --ask-password, "
            "or BIT_SSO_USERNAME/BIT_SSO_PASSWORD."
        )
    return username, password


def _resolve_optional_credentials(args: argparse.Namespace, config) -> tuple[str, str]:
    username = args.username or os.environ.get("BIT_SSO_USERNAME") or config.username
    password = args.password or os.environ.get("BIT_SSO_PASSWORD") or config.password
    if args.ask_password:
        password = getpass.getpass("BIT SSO password: ")
    return username, password


def _lexue_login_service_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/login/index.php"


def _sms_code_callback(args: argparse.Namespace):
    if not getattr(args, "ask_sms_code", False):
        return None

    async def request_code(context) -> str:
        channel = "email" if getattr(context, "channel", "sms") == "email" else "SMS"
        return await asyncio.to_thread(
            getpass.getpass,
            f"BIT SSO {channel} code ({context.masked_phone}): ",
        )

    return request_code


def _debug(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _redact_url(url: str) -> str:
    parsed = urlparse(url)
    query = parsed.query
    if query:
        query = "<redacted-query>"
    return parsed._replace(query=query, fragment="").geturl()
