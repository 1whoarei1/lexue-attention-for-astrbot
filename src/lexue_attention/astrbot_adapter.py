from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

from .core import FetchOptions, SyncResult
from .models import DdlEvent

DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_LEXUE_BASE_URL = "https://lexue.bit.edu.cn"
DEFAULT_MILESTONES_HOURS = (72, 24, 6)
DEFAULT_T2I_ENDPOINT = "official"

DDL_CARD_TEMPLATE = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <style>
    * {
      box-sizing: border-box;
    }

    html {
      width: 760px;
      margin: 0;
      padding: 0;
      background: #f5f7fa;
    }

    body {
      width: 760px;
      margin: 0;
      padding: 24px;
      background: #f5f7fa;
      color: #172033;
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", Arial, sans-serif;
      letter-spacing: 0;
    }

    .panel {
      width: 100%;
      border: 1px solid #dfe5ee;
      border-radius: 8px;
      background: #ffffff;
      overflow: hidden;
    }

    .header {
      padding: 22px 24px 18px;
      border-bottom: 1px solid #e6ebf2;
    }

    .header-row {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 18px;
    }

    .title {
      margin: 0;
      font-size: 30px;
      line-height: 1.2;
      font-weight: 800;
    }

    .subtitle {
      margin-top: 8px;
      font-size: 14px;
      line-height: 1.45;
      color: #667085;
    }

    .total {
      min-width: 112px;
      padding: 10px 12px;
      border: 1px solid #d7dde7;
      border-radius: 8px;
      text-align: center;
      background: #f8fafc;
    }

    .total-number {
      display: block;
      font-size: 28px;
      line-height: 1;
      font-weight: 800;
      color: #172033;
    }

    .total-label {
      display: block;
      margin-top: 5px;
      font-size: 13px;
      color: #667085;
    }

    .metrics {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 18px;
    }

    .metric {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      min-height: 30px;
      padding: 5px 10px;
      border: 1px solid #d7dde7;
      border-radius: 6px;
      font-size: 13px;
      color: #475467;
      background: #ffffff;
    }

    .metric strong {
      font-size: 15px;
      color: #172033;
    }

    .metric.expired strong { color: #b42318; }
    .metric.critical strong { color: #c2410c; }
    .metric.today strong { color: #a16207; }
    .metric.soon strong { color: #175cd3; }
    .metric.later strong { color: #087443; }

    .content {
      padding: 16px;
    }

    .empty {
      padding: 34px 24px;
      border: 1px dashed #cbd5e1;
      border-radius: 8px;
      text-align: center;
      color: #667085;
      font-size: 18px;
      background: #f8fafc;
    }

    .event {
      position: relative;
      display: grid;
      grid-template-columns: 1fr 172px;
      gap: 16px;
      min-height: 112px;
      padding: 16px 16px 16px 20px;
      border: 1px solid #e1e7ef;
      border-left-width: 6px;
      border-radius: 8px;
      background: #ffffff;
    }

    .event + .event {
      margin-top: 10px;
    }

    .event.expired { border-left-color: #dc2626; }
    .event.critical { border-left-color: #f97316; }
    .event.today { border-left-color: #eab308; }
    .event.soon { border-left-color: #2563eb; }
    .event.later { border-left-color: #16a34a; }

    .course {
      display: inline-flex;
      max-width: 100%;
      min-height: 24px;
      align-items: center;
      padding: 3px 8px;
      border-radius: 5px;
      background: #f1f5f9;
      color: #334155;
      font-size: 13px;
      line-height: 1.35;
      font-weight: 700;
      overflow-wrap: anywhere;
    }

    .event-title {
      margin-top: 9px;
      font-size: 22px;
      line-height: 1.28;
      font-weight: 800;
      color: #111827;
      overflow-wrap: anywhere;
    }

    .remaining {
      margin-top: 9px;
      font-size: 15px;
      line-height: 1.35;
      color: #475467;
    }

    .side {
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      justify-content: space-between;
      gap: 12px;
    }

    .status {
      min-width: 86px;
      padding: 6px 10px;
      border-radius: 6px;
      text-align: center;
      font-size: 14px;
      line-height: 1;
      font-weight: 800;
    }

    .status.expired { color: #b42318; background: #fee4e2; }
    .status.critical { color: #c2410c; background: #ffedd5; }
    .status.today { color: #a16207; background: #fef3c7; }
    .status.soon { color: #175cd3; background: #dbeafe; }
    .status.later { color: #087443; background: #dcfce7; }

    .due {
      text-align: right;
    }

    .due-date {
      font-size: 17px;
      line-height: 1.25;
      font-weight: 800;
      color: #172033;
    }

    .due-time {
      margin-top: 5px;
      font-size: 28px;
      line-height: 1;
      font-weight: 800;
      color: #172033;
    }

    .hidden {
      margin-top: 12px;
      padding: 10px 12px;
      border-radius: 6px;
      background: #f1f5f9;
      color: #475467;
      font-size: 14px;
      text-align: center;
    }
  </style>
</head>
<body>
  <section class="panel">
    <header class="header">
      <div class="header-row">
        <div>
          <h1 class="title">{{ title|e }}</h1>
          <div class="subtitle">{{ generated_at|e }} 更新{% if hidden_count > 0 %}，已显示 {{ shown_count }} / {{ total_count }} 个{% endif %}</div>
        </div>
        <div class="total">
          <span class="total-number">{{ total_count }}</span>
          <span class="total-label">DDL</span>
        </div>
      </div>

      <div class="metrics">
        {% for metric in metrics %}
        <span class="metric {{ metric.tone|e }}">{{ metric.label|e }} <strong>{{ metric.value }}</strong></span>
        {% endfor %}
      </div>
    </header>

    <main class="content">
      {% if events %}
        {% for item in events %}
        <article class="event {{ item.tone|e }}">
          <div>
            {% if item.course %}
            <div class="course">{{ item.course|e }}</div>
            {% endif %}
            <div class="event-title">{{ item.title|e }}</div>
            <div class="remaining">{{ item.remaining|e }}</div>
          </div>
          <div class="side">
            <div class="status {{ item.tone|e }}">{{ item.status_label|e }}</div>
            <div class="due">
              <div class="due-date">{{ item.due_date|e }} {{ item.weekday|e }}</div>
              <div class="due-time">{{ item.due_time|e }}</div>
            </div>
          </div>
        </article>
        {% endfor %}
        {% if hidden_count > 0 %}
        <div class="hidden">还有 {{ hidden_count }} 个 DDL 未显示，可调大 max_events。</div>
        {% endif %}
      {% else %}
        <div class="empty">暂无 DDL</div>
      {% endif %}
    </main>
  </section>
</body>
</html>
"""


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
    enable_image_mode: bool
    t2i_endpoint: str
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
        enable_image_mode=_get_bool(raw, "enable_image_mode", True),
        t2i_endpoint=_get_str(raw, "t2i_endpoint", DEFAULT_T2I_ENDPOINT) or DEFAULT_T2I_ENDPOINT,
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
        card = _event_card(event, now)
        course = f"[{card['course']}] " if card["course"] else ""
        lines.append(
            f"{index}. {course}{card['title']}\n"
            f"   DDL: {card['due_full']} · {card['status_label']}\n"
            f"   {card['remaining']}"
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


def build_ddl_card_context(
    events: list[DdlEvent],
    now: datetime,
    *,
    title: str = "DDL 列表",
    limit: int = 20,
) -> dict[str, Any]:
    sorted_events = sorted(events, key=lambda item: item.due_at)
    visible_events = sorted_events[:limit]
    cards = [_event_card(event, now) for event in visible_events]
    counts = _status_counts(sorted_events, now)
    return {
        "title": title,
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "total_count": len(sorted_events),
        "shown_count": len(visible_events),
        "hidden_count": max(0, len(sorted_events) - len(visible_events)),
        "events": cards,
        "metrics": [
            {"label": "已过期", "value": counts["expired"], "tone": "expired"},
            {"label": "马上截止", "value": counts["critical"], "tone": "critical"},
            {"label": "今日截止", "value": counts["today"], "tone": "today"},
            {"label": "3 天内", "value": counts["soon"], "tone": "soon"},
            {"label": "待办", "value": counts["later"], "tone": "later"},
        ],
    }


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


def _event_card(event: DdlEvent, now: datetime) -> dict[str, str]:
    due_at = _to_timezone(event.due_at, now)
    status = _event_status(due_at, now)
    return {
        "title": _clean_title(event.title),
        "course": _clean_course(event.course),
        "due_full": due_at.strftime("%Y-%m-%d %H:%M"),
        "due_date": due_at.strftime("%m 月 %d 日"),
        "due_time": due_at.strftime("%H:%M"),
        "weekday": _weekday(due_at),
        "remaining": _format_compact_remaining(due_at, now),
        "status_label": status["label"],
        "tone": status["tone"],
    }


def _event_status(due_at: datetime, now: datetime) -> dict[str, str]:
    total_minutes = int((due_at - now).total_seconds() // 60)
    if total_minutes < 0:
        return {"tone": "expired", "label": "已过期"}
    if total_minutes <= 6 * 60:
        return {"tone": "critical", "label": "马上截止"}
    if due_at.date() == now.date() or total_minutes <= 24 * 60:
        return {"tone": "today", "label": "今日截止"}
    if total_minutes <= 72 * 60:
        return {"tone": "soon", "label": "3 天内"}
    return {"tone": "later", "label": "待办"}


def _status_counts(events: list[DdlEvent], now: datetime) -> dict[str, int]:
    counts = {"expired": 0, "critical": 0, "today": 0, "soon": 0, "later": 0}
    for event in events:
        tone = _event_status(_to_timezone(event.due_at, now), now)["tone"]
        counts[tone] += 1
    return counts


def _format_compact_remaining(due_at: datetime, now: datetime) -> str:
    delta = due_at - now
    total_minutes = int(delta.total_seconds() // 60)
    prefix = "剩余"
    if total_minutes < 0:
        prefix = "已过期"
        total_minutes = abs(total_minutes)

    days, rem = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem, 60)
    if days:
        return f"{prefix} {days} 天 {hours} 小时"
    if hours:
        return f"{prefix} {hours} 小时 {minutes} 分钟"
    return f"{prefix} {minutes} 分钟"


def _clean_title(value: str) -> str:
    text = value.strip()
    text = re.sub(r"[（(]\s*截止时间[^）)]*[）)]", "", text)
    text = re.sub(r"^请在此提交[:：]?", "", text)
    text = re.sub(r"\s*(已到期|已截止|已过期)\s*$", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -:：") or value.strip()


def _clean_course(value: str) -> str:
    course = value.strip()
    if not course:
        return ""
    course = re.sub(r"^\d{4}-\d{4}[-\s]*第?\d学期[-\s-]*", "", course)
    course = re.sub(r"^\d{4}-\d{4}\s*第[一二三四五六七八九十\d]+学期\s*", "", course)
    course = re.sub(r"[_-]\d+$", "", course)
    parts = [part.strip() for part in re.split(r"--+| - ", course) if part.strip()]
    if len(parts) > 1 and parts[-1].endswith("老师"):
        course = " ".join(parts[:-1])
    course = re.sub(r"\s+", " ", course)
    return course.strip(" -_") or value.strip()


def _weekday(value: datetime) -> str:
    names = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    return names[value.weekday()]


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
