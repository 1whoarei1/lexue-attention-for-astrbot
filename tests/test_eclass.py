import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from lexue_attention.eclass import (
    EclassClient,
    EclassError,
    activity_to_event,
    parse_eclass_time,
)


@pytest.mark.asyncio
async def test_fetch_all_courses_and_course_homework():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path == "/api/my-courses":
            page = json.loads(request.content)["page"]
            return httpx.Response(
                200,
                json={
                    "courses": (
                        [
                            {"id": 17, "name": "计算机视觉"},
                            {"id": 18, "name": "信号处理"},
                        ]
                        if page == 1
                        else [{"id": 19, "name": "机器学习"}]
                    ),
                    "page": page,
                    "page_size": 100,
                    "pages": 2,
                    "total": 3,
                },
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
        if request.url.path == "/api/courses/18/activities":
            return httpx.Response(
                200,
                json={
                    "activities": [
                        {
                            "id": 6,
                            "title": "滤波实验",
                            "type": "homework",
                            "end_time": "2026-10-03 23:59:00",
                            "late_submission_count": 0,
                        }
                    ]
                },
            )
        if request.url.path == "/api/courses/19/activities":
            return httpx.Response(200, json={"activities": []})
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as session:
        client = EclassClient(session, "https://eclass.example")
        events = await client.fetch_homework_events()

    course_requests = [request for request in requests if request.url.path == "/api/my-courses"]
    assert [request.method for request in course_requests] == ["POST", "POST"]
    assert [json.loads(request.content)["page"] for request in course_requests] == [1, 2]
    assert {request.url.path for request in requests} == {
        "/api/my-courses",
        "/api/courses/17/activities",
        "/api/courses/18/activities",
        "/api/courses/19/activities",
    }
    assert [(event.uid, event.title, event.course) for event in events] == [
        ("eclass:4", "第三次作业", "计算机视觉"),
        ("eclass:6", "滤波实验", "信号处理"),
    ]
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
