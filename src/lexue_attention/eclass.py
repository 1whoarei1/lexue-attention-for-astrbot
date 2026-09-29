from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from .models import DdlEvent

ECLASS_BASE_URL = "https://zy-eclass.bit.edu.cn"
ECLASS_COURSES_PATH = "/api/user/recently-visited-courses"
ECLASS_ACTIVITIES_PATH = "/api/courses/{course_id}/activities"
ECLASS_TZ = ZoneInfo("Asia/Shanghai")


class EclassError(RuntimeError):
    """Raised when the course-center API cannot provide usable data."""


@dataclass(frozen=True, slots=True)
class EclassCourse:
    id: int
    name: str


class EclassClient:
    def __init__(
        self,
        session: httpx.AsyncClient,
        base_url: str = ECLASS_BASE_URL,
    ) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")

    async def fetch_courses(self) -> list[EclassCourse]:
        payload = await self._get_json(ECLASS_COURSES_PATH)
        raw_courses = payload.get("visited_courses")
        if not isinstance(raw_courses, list):
            raise EclassError("课程中心课程列表响应缺少 visited_courses")

        courses: list[EclassCourse] = []
        for raw in raw_courses:
            if not isinstance(raw, dict):
                continue
            try:
                course_id = int(raw["id"])
            except (KeyError, TypeError, ValueError):
                continue
            name = str(raw.get("name") or "").strip()
            if name:
                courses.append(EclassCourse(id=course_id, name=name))
        return courses

    async def fetch_activities(self, course: EclassCourse) -> list[dict[str, Any]]:
        path = ECLASS_ACTIVITIES_PATH.format(course_id=quote(str(course.id), safe=""))
        payload = await self._get_json(path)
        activities = payload.get("activities")
        if not isinstance(activities, list):
            raise EclassError(f"课程中心动态响应缺少 activities（课程：{course.name}）")
        return [item for item in activities if isinstance(item, dict)]

    async def fetch_homework_events(self) -> list[DdlEvent]:
        events: list[DdlEvent] = []
        for course in await self.fetch_courses():
            activities = await self.fetch_activities(course)
            events.extend(
                event
                for activity in activities
                if (event := activity_to_event(activity, course.name)) is not None
            )
        return events

    async def _get_json(self, path: str) -> dict[str, Any]:
        try:
            response = await self.session.get(self.base_url + path)
        except httpx.HTTPError as exc:
            raise EclassError(f"课程中心请求失败（{path}）：{type(exc).__name__}") from exc
        if response.is_error:
            raise EclassError(f"课程中心请求失败（{path}）：HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise EclassError(f"课程中心返回了非 JSON 响应（{path}），会话可能未建立") from exc
        if not isinstance(payload, dict):
            raise EclassError(f"课程中心响应格式无效（{path}）")
        return payload


def activity_to_event(activity: dict[str, Any], course_name: str) -> DdlEvent | None:
    """Map one course activity to a DDL only when its homework markers exist."""

    activity_type = str(activity.get("type") or "").strip().casefold()
    if activity_type == "material":
        return None

    deadline = parse_eclass_time(activity.get("end_time"))
    if deadline is None:
        deadline = parse_eclass_time(activity.get("visible_end_at"))
    if deadline is None:
        return None

    if not any(
        activity.get(field) is not None
        for field in ("submit_times", "is_review_homework", "late_submission_count")
    ):
        return None

    try:
        activity_id = int(activity["id"])
    except (KeyError, TypeError, ValueError):
        return None

    title = str(activity.get("title") or "").strip() or "未命名作业"
    return DdlEvent(
        uid=f"eclass:{activity_id}",
        title=title,
        description="",
        course=course_name,
        due_at=deadline,
    )


def parse_eclass_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.casefold() == "null":
        return None

    iso_text = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=ECLASS_TZ)
        return parsed.astimezone(ECLASS_TZ)

    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
    ):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=ECLASS_TZ)
        except ValueError:
            continue
    return None
