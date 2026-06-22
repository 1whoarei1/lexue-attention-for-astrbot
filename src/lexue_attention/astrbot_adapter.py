from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .core import FetchOptions, SyncResult
from .models import DdlEvent

DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_LEXUE_BASE_URL = "https://lexue.bit.edu.cn"
DEFAULT_MILESTONES_HOURS = (72, 24, 6)


@dataclass(frozen=True, slots=True)
class AstrBotPluginConfig:
    username: str
    password: str
    calendar_url: str
    lexue_base_url: str
    auth_method: str
    state_path: str
    daily_push_time: str
    check_interval_minutes: int
    reminder_milestones_hours: tuple[int, ...]
    max_events: int
    enable_daily_push: bool
    enable_interval_sync: bool
    timezone: ZoneInfo

    def fetch_options(self) -> FetchOptions:
        return FetchOptions(
            username=self.username,
            password=self.password,
            calendar_url=self.calendar_url,
            lexue_base_url=self.lexue_base_url,
            auth_method=self.auth_method,
        )


def normalize_plugin_config(raw: Any, state_path: str | Path) -> AstrBotPluginConfig:
    return AstrBotPluginConfig(
        username=_get_str(raw, "username"),
        password=_get_str(raw, "password"),
        calendar_url=_get_str(raw, "calendar_url"),
        lexue_base_url=_get_str(raw, "lexue_base_url", DEFAULT_LEXUE_BASE_URL),
        auth_method=_normalize_auth_method(_get_str(raw, "auth_method", "android")),
        state_path=str(state_path),
        daily_push_time=_get_str(raw, "daily_push_time", "08:30"),
        check_interval_minutes=max(5, _get_int(raw, "check_interval_minutes", 60)),
        reminder_milestones_hours=_get_int_tuple(
            raw,
            "reminder_milestones_hours",
            DEFAULT_MILESTONES_HOURS,
        ),
        max_events=max(1, _get_int(raw, "max_events", 20)),
        enable_daily_push=_get_bool(raw, "enable_daily_push", True),
        enable_interval_sync=_get_bool(raw, "enable_interval_sync", True),
        timezone=ZoneInfo(_get_str(raw, "timezone", DEFAULT_TIMEZONE)),
    )


def validate_fetch_config(config: AstrBotPluginConfig) -> None:
    if config.calendar_url:
        return
    if not config.username:
        raise ValueError("缺少 BIT 统一认证账号。请使用 /lexue account 或配置 calendar_url。")
    if not config.password:
        raise ValueError("缺少 BIT 统一认证密码。请使用 /lexue account 或配置 calendar_url。")


def parse_hhmm(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError("时间格式应为 HH:MM，例如 08:30。")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ValueError("时间格式应为 HH:MM，例如 08:30。") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("时间范围无效，小时应为 0-23，分钟应为 0-59。")
    return time(hour=hour, minute=minute)


def is_same_minute(now: datetime, hhmm: str) -> bool:
    scheduled = parse_hhmm(hhmm)
    return now.hour == scheduled.hour and now.minute == scheduled.minute


def format_event_list(
    events: list[DdlEvent],
    now: datetime,
    *,
    title: str = "DDL 列表",
    limit: int = 20,
) -> str:
    if not events:
        return f"{title}\n暂无 DDL。"

    sorted_events = sorted(events, key=lambda item: item.due_at)
    lines = [title]
    for index, event in enumerate(sorted_events[:limit], start=1):
        course = f"[{event.course}] " if event.course else ""
        due_at = _to_timezone(event.due_at, now).strftime("%Y-%m-%d %H:%M")
        lines.append(
            f"{index}. {course}{event.title}\n"
            f"   DDL: {due_at}\n"
            f"   {_format_remaining(event.due_at, now)}"
        )

    remaining = len(sorted_events) - limit
    if remaining > 0:
        lines.append(f"还有 {remaining} 个 DDL 未显示，可调大 max_events。")
    return "\n".join(lines)


def format_sync_summary(
    result: SyncResult,
    now: datetime,
    *,
    max_events: int = 20,
) -> str:
    lines = [
        (
            f"同步完成：共 {len(result.events)} 个 DDL，"
            f"新增 {len(result.new_events)} 个，"
            f"变更 {len(result.changed_events)} 个，"
            f"提醒 {len(result.reminders)} 条。"
        )
    ]

    if result.new_events:
        lines.append(format_event_list(result.new_events, now, title="新增 DDL", limit=max_events))
    if result.changed_events:
        lines.append(format_event_list(result.changed_events, now, title="变更 DDL", limit=max_events))
    if result.reminders:
        lines.append("到期提醒\n" + "\n\n".join(reminder.text for reminder in result.reminders))
    if result.events:
        lines.append(format_event_list(result.events, now, title="当前 DDL", limit=max_events))
    return "\n\n".join(lines)


def format_sync_notifications(
    result: SyncResult,
    now: datetime,
    *,
    max_events: int = 20,
) -> list[str]:
    messages: list[str] = []
    if result.new_events:
        messages.append(format_event_list(result.new_events, now, title="新增 DDL", limit=max_events))
    if result.changed_events:
        messages.append(format_event_list(result.changed_events, now, title="变更 DDL", limit=max_events))
    for reminder in result.reminders:
        messages.append("DDL 提醒\n" + reminder.text)
    return messages


def _format_remaining(due_at: datetime, now: datetime) -> str:
    due_at = _to_timezone(due_at, now)
    delta = due_at - now
    total_minutes = int(delta.total_seconds() // 60)
    prefix = "剩余"
    if total_minutes < 0:
        prefix = "已过期"
        total_minutes = abs(total_minutes)

    days, rem = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem, 60)
    if days:
        return f"{prefix} {days} 天 {hours} 小时 {minutes} 分钟"
    return f"{prefix} {hours} 小时 {minutes} 分钟"


def _to_timezone(value: datetime, now: datetime) -> datetime:
    tz = now.tzinfo or ZoneInfo(DEFAULT_TIMEZONE)
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _normalize_auth_method(value: str) -> str:
    if value in {"android", "ticket", "page"}:
        return value
    return "android"


def _get(raw: Any, key: str, default: Any = None) -> Any:
    if hasattr(raw, "get"):
        return raw.get(key, default)
    return default


def _get_str(raw: Any, key: str, default: str = "") -> str:
    value = _get(raw, key, default)
    if value is None:
        return default
    return str(value).strip()


def _get_int(raw: Any, key: str, default: int = 0) -> int:
    value = _get(raw, key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_bool(raw: Any, key: str, default: bool = False) -> bool:
    value = _get(raw, key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "on"}:
            return True
        if lowered in {"false", "no", "0", "off"}:
            return False
    return bool(value) if value is not None else default


def _get_int_tuple(raw: Any, key: str, default: tuple[int, ...]) -> tuple[int, ...]:
    value = _get(raw, key, default)
    if isinstance(value, str):
        candidates = [item.strip() for item in value.split(",")]
    elif isinstance(value, list | tuple):
        candidates = list(value)
    else:
        candidates = list(default)

    parsed: list[int] = []
    for item in candidates:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0:
            parsed.append(number)
    return tuple(parsed) or default
