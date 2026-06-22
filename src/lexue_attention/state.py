from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .models import DdlEvent


@dataclass(slots=True)
class EventState:
    content_hash: str
    done: bool = False
    sent_rules: set[str] = field(default_factory=set)


class JsonStateStore:
    """Small JSON store for event state.

    The important upstream behavior from BIT101-Android is preserving local
    state by UID when Lexue events are refreshed.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, EventState]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return {
            uid: EventState(
                content_hash=item.get("content_hash", ""),
                done=bool(item.get("done", False)),
                sent_rules=set(item.get("sent_rules", [])),
            )
            for uid, item in raw.items()
        }

    def save(self, state: dict[str, EventState]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = {
            uid: {
                "content_hash": item.content_hash,
                "done": item.done,
                "sent_rules": sorted(item.sent_rules),
            }
            for uid, item in sorted(state.items())
        }
        self.path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    def merge_events(self, events: list[DdlEvent]) -> tuple[dict[str, EventState], list[DdlEvent], list[DdlEvent]]:
        state = self.load()
        new_events: list[DdlEvent] = []
        changed_events: list[DdlEvent] = []

        for event in events:
            current_hash = event.identity_hash
            old = state.get(event.uid)
            if old is None:
                new_events.append(event)
                state[event.uid] = EventState(content_hash=current_hash)
            elif old.content_hash != current_hash:
                changed_events.append(event)
                old.content_hash = current_hash

        self.save(state)
        return state, new_events, changed_events
