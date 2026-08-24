from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

import pytest

from lexue_attention import mail_code
from lexue_attention.mail_code import MailCodeConfig, MailCodeError


def _verification_email(*, sent_at: datetime, code: str = "482731") -> bytes:
    message = EmailMessage()
    message["From"] = "统一身份认证 <no-reply@sso.bit.edu.cn>"
    message["To"] = "student@bit.edu.cn"
    message["Subject"] = "统一身份认证验证码"
    message["Date"] = format_datetime(sent_at)
    message.set_content(f"您的验证码是 {code}，请勿泄露。")
    return message.as_bytes()


class FakeImap:
    messages: list[bytes] = []
    instances: list["FakeImap"] = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.login_args = None
        self.select_args = None
        self.fetch_queries: list[str] = []
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def login(self, username, password):
        self.login_args = (username, password)
        return "OK", []

    def select(self, mailbox, readonly=False):
        self.select_args = (mailbox, readonly)
        return "OK", [str(len(self.messages)).encode()]

    def search(self, *args):
        ids = b" ".join(str(index).encode() for index in range(1, len(self.messages) + 1))
        return "OK", [ids]

    def fetch(self, message_id, query):
        self.fetch_queries.append(query)
        raw = self.messages[int(message_id) - 1]
        return "OK", [(b"RFC822", raw)]

    def noop(self):
        return "OK", []


@pytest.mark.asyncio
async def test_wait_for_code_reads_fresh_message_without_marking_read(monkeypatch):
    requested_at = datetime.now(timezone.utc)
    FakeImap.messages = [
        _verification_email(sent_at=requested_at - timedelta(hours=1), code="111111"),
        _verification_email(sent_at=requested_at + timedelta(seconds=1), code="482731"),
    ]
    FakeImap.instances = []
    monkeypatch.setattr(mail_code.imaplib, "IMAP4_SSL", FakeImap)

    code = await mail_code.wait_for_sso_email_code(
        MailCodeConfig(username="student@bit.edu.cn", password="mail-secret"),
        requested_at=requested_at.timestamp(),
    )

    instance = FakeImap.instances[0]
    assert code == "482731"
    assert instance.select_args == ("INBOX", True)
    assert instance.fetch_queries == ["(BODY.PEEK[])"]


@pytest.mark.asyncio
async def test_wait_for_code_ignores_stale_message_and_times_out(monkeypatch):
    requested_at = datetime.now(timezone.utc)
    FakeImap.messages = [
        _verification_email(sent_at=requested_at - timedelta(minutes=10)),
    ]
    FakeImap.instances = []
    monkeypatch.setattr(mail_code.imaplib, "IMAP4_SSL", FakeImap)

    with pytest.raises(MailCodeError, match="超时"):
        await mail_code.wait_for_sso_email_code(
            MailCodeConfig(
                username="student@bit.edu.cn",
                password="mail-secret",
                poll_timeout_seconds=0,
            ),
            requested_at=requested_at.timestamp(),
        )


def test_mail_config_repr_hides_password():
    assert "mail-secret" not in repr(
        MailCodeConfig(username="student@bit.edu.cn", password="mail-secret")
    )
