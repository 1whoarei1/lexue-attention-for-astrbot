from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .models import DdlEvent

DEFAULT_TZ = ZoneInfo("Asia/Shanghai")


class IcsParseError(ValueError):
    """Raised when an iCalendar payload cannot be parsed."""


def parse_lexue_ics(text: str, default_tz: ZoneInfo = DEFAULT_TZ) -> list[DdlEvent]:
    """Parse Lexue `.ics` content into DDL events.

    BIT101-Android uses ical4j and reads UID, SUMMARY, DESCRIPTION,
    CATEGORIES, and DTSTART. This parser keeps the same field mapping while
    avoiding Android/Java dependencies in the Python core.
    """

    unfolded = _unfold_lines(text)
    events: list[DdlEvent] = []
    current: list[str] | None = None

    for line in unfolded:
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            current = []
        elif upper == "END:VEVENT":
            if current is not None:
                events.append(_parse_event(current, default_tz))
            current = None
        elif current is not None:
            current.append(line)

    return events


def _unfold_lines(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded: list[str] = []
    for line in lines:
        if not line:
            continue
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def _parse_event(lines: list[str], default_tz: ZoneInfo) -> DdlEvent:
    props: dict[str, str] = {}
    params: dict[str, dict[str, str]] = {}
    for line in lines:
        key_part, value = _split_property(line)
        key, key_params = _parse_property_key(key_part)
        props[key] = _unescape(value)
        params[key] = key_params

    try:
        return DdlEvent(
            uid=props["UID"],
            title=props.get("SUMMARY", ""),
            description=props.get("DESCRIPTION", ""),
            course=props.get("CATEGORIES", ""),
            due_at=_parse_dtstart(props["DTSTART"], params.get("DTSTART", {}), default_tz),
        )
    except KeyError as exc:
        raise IcsParseError(f"VEVENT is missing {exc.args[0]}") from exc


def _split_property(line: str) -> tuple[str, str]:
    if ":" not in line:
        raise IcsParseError(f"Invalid iCalendar property line: {line!r}")
    return line.split(":", 1)


def _parse_property_key(key_part: str) -> tuple[str, dict[str, str]]:
    pieces = key_part.split(";")
    key = pieces[0].upper()
    params: dict[str, str] = {}
    for piece in pieces[1:]:
        if "=" in piece:
            name, value = piece.split("=", 1)
            params[name.upper()] = value
    return key, params


def _unescape(value: str) -> str:
    return (
        value.replace(r"\n", "\n")
        .replace(r"\N", "\n")
        .replace(r"\,", ",")
        .replace(r"\;", ";")
        .replace(r"\\", "\\")
    )


def _parse_dtstart(value: str, params: dict[str, str], default_tz: ZoneInfo) -> datetime:
    if params.get("VALUE", "").upper() == "DATE":
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=default_tz)

    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(default_tz)

    tzid = params.get("TZID")
    tz = ZoneInfo(tzid) if tzid else default_tz
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=tz)
        except ValueError:
            pass
    raise IcsParseError(f"Unsupported DTSTART value: {value!r}")
