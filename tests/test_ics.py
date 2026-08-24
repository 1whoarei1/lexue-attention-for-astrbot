from datetime import timezone
from pathlib import Path

from lexue_attention.ics import parse_lexue_ics


def test_parse_lexue_ics_fixture():
    text = Path("tests/fixtures/lexue_sample.ics").read_text(encoding="utf-8")

    events = parse_lexue_ics(text)

    assert [event.uid for event in events] == ["assignment-1@example", "quiz-2@example"]
    assert events[0].title == "提交实验报告"
    assert events[0].description == "完成第 1 次实验报告\n上传 PDF"
    assert events[0].course == "软件工程"
    assert events[0].due_at.year == 2026
    assert events[0].due_at.utcoffset().total_seconds() == 8 * 3600
    assert events[1].due_at.astimezone(timezone.utc).hour == 12
