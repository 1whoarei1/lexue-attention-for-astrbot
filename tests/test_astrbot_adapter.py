from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from lexue_attention.astrbot_adapter import (
    build_ddl_card_context,
    format_event_list,
    is_same_minute,
    normalize_plugin_config,
    parse_hhmm,
    validate_fetch_config,
)
from lexue_attention.models import DdlEvent


def test_parse_hhmm_accepts_valid_time():
    parsed = parse_hhmm("08:30")

    assert parsed.hour == 8
    assert parsed.minute == 30


def test_parse_hhmm_rejects_invalid_time():
    with pytest.raises(ValueError):
        parse_hhmm("24:00")


def test_is_same_minute():
    now = datetime(2026, 6, 21, 8, 30, 15, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert is_same_minute(now, "08:30")
    assert not is_same_minute(now, "08:31")


def test_normalize_plugin_config_defaults_state_path():
    config = normalize_plugin_config({}, "data/plugin_data/lexue/state.json")

    assert config.lexue_base_url == "https://lexue.bit.edu.cn"
    assert config.auth_method == "android"
    assert config.state_path == "data/plugin_data/lexue/state.json"
    assert config.reminder_milestones_hours == (72, 24, 6)
    assert config.mail_username == ""
    assert config.mail_password == ""
    assert config.enable_mail_auto_code is True
    assert config.enable_image_mode is True
    assert config.t2i_endpoint == "astrbot"


def test_plugin_config_repr_hides_passwords():
    config = normalize_plugin_config(
        {"password": "sso-secret", "mail_password": "mail-secret"},
        "state.json",
    )

    rendered = repr(config)

    assert "sso-secret" not in rendered
    assert "mail-secret" not in rendered


def test_validate_fetch_config_requires_credentials_without_calendar_url():
    config = normalize_plugin_config({}, "state.json")

    with pytest.raises(ValueError):
        validate_fetch_config(config)


def test_format_event_list_sorts_by_due_time():
    now = datetime(2026, 6, 21, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    events = [
        DdlEvent(
            uid="2",
            title="晚交作业",
            description="",
            course="课程 B",
            due_at=datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc),
        ),
        DdlEvent(
            uid="1",
            title="先交作业",
            description="",
            course="课程 A",
            due_at=datetime(2026, 6, 22, 8, 0, tzinfo=timezone.utc),
        ),
    ]

    text = format_event_list(events, now, title="当前 DDL")

    assert text.index("先交作业") < text.index("晚交作业")
    assert "当前 DDL" in text


def test_build_ddl_card_context_cleans_and_groups_events():
    now = datetime(2026, 6, 22, 13, 26, tzinfo=ZoneInfo("Asia/Shanghai"))
    events = [
        DdlEvent(
            uid="expired",
            title="请在此提交第5章作业（截止时间6月18日） 已到期",
            description="",
            course="2025-2026-第2学期--计算理论与算法分析设计--张春霞老师",
            due_at=datetime(2026, 6, 18, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        ),
        DdlEvent(
            uid="critical",
            title="实验报告",
            description="",
            course="软件工程_1",
            due_at=datetime(2026, 6, 22, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        ),
        DdlEvent(
            uid="soon",
            title="阅读材料",
            description="",
            course="课程 C",
            due_at=datetime(2026, 6, 24, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        ),
    ]

    context = build_ddl_card_context(events, now, title="今日 DDL 推送")

    assert context["total_count"] == 3
    assert context["events"][0]["title"] == "第5章作业"
    assert context["events"][0]["course"] == "计算理论与算法分析设计"
    assert context["events"][0]["tone"] == "expired"
    assert context["events"][1]["tone"] == "critical"
    assert context["events"][2]["tone"] == "soon"
    assert {item["tone"]: item["value"] for item in context["metrics"]}["expired"] == 1
