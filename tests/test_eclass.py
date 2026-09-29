from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from lexue_attention.eclass import (
    EclassClient,
    EclassCourse,
    EclassError,
    activity_to_event,
    parse_eclass_time,
)


@pytest.mark.asyncio
async def test_fetch_courses_and_course_activities():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/user/recently-visited-courses":
            return httpx.Response(
                200,
                json={"visited_courses": [{"id": 17, "name": "计算机视觉"}]},
            )
        if request.url.path == "/api/courses/17/activities":
            return httpx.Response(
                200,
                json={
                    "activities": [
                        {
                            "id": 4,
                            "title": "第三次作业",
                            "type": "homework",
                            "end_time": "2026-10-01 23:59:00",
                            "submit_times": 2,
                        },
                        {
                            "id": 5,
                            "title": "课件",
                            "type": "material",
                            "end_time": "2026-10-02 23:59:00",
                            "submit_times": 1,
                        },
                    ]
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as session:
        client = EclassClient(session, "https://eclass.example")
        courses = await client.fetch_courses()
        activities = await client.fetch_activities(courses[0])

    events = [
        event
        for activity in activities
        if (event := activity_to_event(activity, courses[0].name)) is not None
    ]
    assert courses == [EclassCourse(id=17, name="计算机视觉")]
    assert [request.url.path for request in requests] == [
        "/api/user/recently-visited-courses",
        "/api/courses/17/activities",
    ]
    assert len(events) == 1
    assert events[0].uid == "eclass:4"
    assert events[0].title == "第三次作业"
    assert events[0].course == "计算机视觉"
    assert events[0].due_at == datetime(2026, 10, 1, 23, 59, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.mark.parametrize(
    ("activity", "expected_title"),
    [
        (
            {
                "id": 1,
                "title": "作业 A",
                "type": "homework",
                "visible_end_at": "2026-10-01 12:30",
                "is_review_homework": False,
            },
            "作业 A",
        ),
        (
            {
                "id": 2,
                "title": "作业 B",
                "type": "homework",
                "end_time": "2026-10-01T00:00:00Z",
                "late_submission_count": 0,
            },
            "作业 B",
        ),
    ],
)
def test_activity_to_event_accepts_homework_markers_and_deadline_fallback(activity, expected_title):
    event = activity_to_event(activity, "计算机视觉")
    assert event is not None
    assert event.title == expected_title


@pytest.mark.parametrize(
    "activity",
    [
        {"id": 1, "type": "material", "end_time": "2026-10-01 12:00", "submit_times": 1},
        {"id": 2, "type": "homework", "end_time": "not-a-date", "submit_times": 1},
        {"id": 3, "type": "homework", "end_time": "2026-10-01 12:00"},
        {"type": "homework", "end_time": "2026-10-01 12:00", "submit_times": 1},
    ],
)
def test_activity_to_event_rejects_materials_missing_markers_or_invalid_data(activity):
    assert activity_to_event(activity, "计算机视觉") is None


def test_parse_eclass_time_converts_offset_timestamp_to_beijing():
    parsed = parse_eclass_time("2026-10-01T00:00:00Z")
    assert parsed == datetime(2026, 10, 1, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.mark.asyncio
async def test_fetch_courses_rejects_non_json_auth_redirect():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="<html>login</html>"))
    ) as session:
        client = EclassClient(session, "https://eclass.example")
        with pytest.raises(EclassError, match="非 JSON"):
            await client.fetch_courses()
