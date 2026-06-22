from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import DdlEvent
from .state import EventState


@dataclass(frozen=True, slots=True)
class Reminder:
    rule_id: str
    event: DdlEvent
    text: str


def plan_reminders(
    events: list[DdlEvent],
    state: dict[str, EventState],
    now: datetime,
    milestones_hours: tuple[int, ...] = (72, 24, 6),
) -> list[Reminder]:
    """Return reminders that should be sent now.

    This only plans reminders and marks state in memory. The caller decides how
    to deliver them and when to persist state.
    """

    planned: list[Reminder] = []
    for event in sorted(events, key=lambda item: item.due_at):
        event_state = state.get(event.uid)
        if event_state is None or event_state.done:
            continue

        remaining_hours = (event.due_at - now).total_seconds() / 3600
        for milestone in sorted(milestones_hours, reverse=True):
            rule_id = f"before_{milestone}h"
            if 0 <= remaining_hours <= milestone and rule_id not in event_state.sent_rules:
                event_state.sent_rules.add(rule_id)
                planned.append(
                    Reminder(
                        rule_id=rule_id,
                        event=event,
                        text=format_reminder(event, now),
                    )
                )
                break

    return planned


def format_reminder(event: DdlEvent, now: datetime) -> str:
    delta = event.due_at - now
    total_minutes = int(delta.total_seconds() // 60)
    if total_minutes >= 0:
        days, rem = divmod(total_minutes, 24 * 60)
        hours, minutes = divmod(rem, 60)
        remaining = f"剩余 {days} 天 {hours} 小时 {minutes} 分钟" if days else f"剩余 {hours} 小时 {minutes} 分钟"
    else:
        minutes_abs = abs(total_minutes)
        days, rem = divmod(minutes_abs, 24 * 60)
        hours, minutes = divmod(rem, 60)
        remaining = f"已过期 {days} 天 {hours} 小时 {minutes} 分钟" if days else f"已过期 {hours} 小时 {minutes} 分钟"

    course = f"[{event.course}] " if event.course else ""
    return f"{course}{event.title}\nDDL: {event.due_at:%Y-%m-%d %H:%M}\n{remaining}"
