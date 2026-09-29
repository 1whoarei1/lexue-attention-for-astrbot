from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from .models import DdlEvent

ECLASS_BASE_URL = "https://zy-eclass.bit.edu.cn"
ECLASS_COURSES_PATH = "/api/my-courses"
ECLASS_ACTIVITIES_PATH = "/api/courses/{course_id}/activities"
ECLASS_COURSE_PAGE_SIZE = 100
ECLASS_ACTIVITY_CONCURRENCY = 8
ECLASS_TZ = ZoneInfo("Asia/Shanghai")


class EclassError(RuntimeError):
    """Raised when the course-center API cannot provide complete usable data."""


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
        courses: list[EclassCourse] = []
        seen_ids: set[int] = set()
        page = 1
        while True:
            payload = await self._post_json(
                ECLASS_COURSES_PATH,
                {
                    "page": page,
                    "page_size": ECLASS_COURSE_PAGE_SIZE,
                    "fields": "id,name",
                    "conditions": {},
                },
            )
            raw_courses = payload.get("courses")
            if not isinstance(raw_courses, list):
                raise EclassError("课程中心课程列表响应缺少 courses")

            page_courses = self._parse_courses(raw_courses)
            added = [course for course in page_courses if course.id not in seen_ids]
            if raw_courses and not added:
                raise EclassError("课程中心课程列表分页没有前进，停止读取以避免漏课")
            courses.extend(added)
            seen_ids.update(course.id for course in added)

            pages = _positive_int(payload.get("pages"))
            response_page = _positive_int(payload.get("page")) or page
            if response_page != page:
                raise EclassError("课程中心课程列表返回了意外的分页页码")
            if not raw_courses or (pages is not None and page >= pages):
                break
            if pages is None and len(raw_courses) < ECLASS_COURSE_PAGE_SIZE:
                break
            page += 1
        return courses

    @staticmethod
    def _parse_courses(raw_courses: list[Any]) -> list[EclassCourse]:
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
        courses = await self.fetch_courses()
        semaphore = asyncio.Semaphore(ECLASS_ACTIVITY_CONCURRENCY)

        async def fetch_course_events(course: EclassCourse) -> list[DdlEvent]:
            async with semaphore:
                activities = await self.fetch_activities(course)
            return [
                event
                for activity in activities
                if (event := activity_to_event(activity, course.name)) is not None
            ]

        results = await asyncio.gather(
            *(fetch_course_events(course) for course in courses),
            return_exceptions=True,
        )
        failures = [result for result in results if isinstance(result, Exception)]
        if failures:
            raise EclassError(
                f"课程中心有 {len(failures)} 门课程活动读取失败，已中止以避免返回不完整结果"
            ) from failures[0]

        events: list[DdlEvent] = []
        for result in results:
            if isinstance(result, BaseException):
                raise result
            events.extend(result)
        return events

    async def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.session.post(self.base_url + path, json=body)
        except httpx.HTTPError as exc:
            raise EclassError(f"课程中心请求失败（{path}）：{type(exc).__name__}") from exc
        return self._parse_json_response(response, path)

    async def _get_json(self, path: str) -> dict[str, Any]:
        try:
            response = await self.session.get(self.base_url + path)
        except httpx.HTTPError as exc:
            raise EclassError(f"课程中心请求失败（{path}）：{type(exc).__name__}") from exc
        return self._parse_json_response(response, path)

    @staticmethod
    def _parse_json_response(response: httpx.Response, path: str) -> dict[str, Any]:
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
        source="eclass",
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


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
