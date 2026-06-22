from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class DdlEvent:
    """A normalized Lexue DDL event.

    Field names follow the BIT101-Android model:
    UID -> uid, SUMMARY -> title, DESCRIPTION -> description,
    CATEGORIES -> course, DTSTART -> due_at.
    """

    uid: str
    title: str
    description: str
    course: str
    due_at: datetime

    @property
    def identity_hash(self) -> str:
        """Stable content key used to detect changed Lexue events."""

        normalized_due = self.due_at.astimezone(timezone.utc).isoformat()
        return "\n".join(
            [
                self.uid,
                self.title.strip(),
                self.description.strip(),
                self.course.strip(),
                normalized_due,
            ]
        )
