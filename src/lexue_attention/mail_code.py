from __future__ import annotations

import asyncio
import imaplib
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import timezone
from email import message_from_bytes
from email.message import Message
from email.policy import default
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup


class MailCodeError(RuntimeError):
    """Raised when the mailbox cannot provide a fresh SSO verification code."""


@dataclass(frozen=True, slots=True)
class MailCodeConfig:
    username: str
    password: str = field(repr=False)
    host: str = "mail.bit.edu.cn"
    port: int = 993
    connect_timeout_seconds: float = 20.0
    poll_timeout_seconds: float = 120.0
    poll_interval_seconds: float = 3.0
    max_messages: int = 30


async def wait_for_sso_email_code(
    config: MailCodeConfig,
    *,
    requested_at: float,
) -> str:
    """Wait for the newest SSO verification email without marking it as read."""

    return await asyncio.to_thread(_wait_for_sso_email_code, config, requested_at)


def _wait_for_sso_email_code(config: MailCodeConfig, requested_at: float) -> str:
    if not config.username.strip() or not config.password:
        raise MailCodeError("邮箱自动取码账号或密码未配置")

    deadline = time.monotonic() + max(0.0, config.poll_timeout_seconds)
    try:
        with imaplib.IMAP4_SSL(
            config.host,
            config.port,
            timeout=config.connect_timeout_seconds,
        ) as mailbox:
            mailbox.login(config.username.strip(), config.password)
            status, _ = mailbox.select("INBOX", readonly=True)
            if status != "OK":
                raise MailCodeError("无法以只读方式打开邮箱收件箱")

            while True:
                code = _latest_code(mailbox, requested_at, config.max_messages)
                if code:
                    return code
                if time.monotonic() >= deadline:
                    raise MailCodeError("等待统一身份认证邮箱验证码超时")
                time.sleep(max(0.1, config.poll_interval_seconds))
                mailbox.noop()
    except MailCodeError:
        raise
    except imaplib.IMAP4.error as exc:
        raise MailCodeError("邮箱登录失败，请检查邮箱账号、密码或 IMAP 权限") from exc
    except (OSError, socket.timeout) as exc:
        raise MailCodeError("连接校内邮箱失败") from exc


def _latest_code(mailbox, requested_at: float, max_messages: int) -> str:
    status, data = mailbox.search(None, "ALL")
    if status != "OK" or not data or not data[0]:
        return ""

    message_ids = data[0].split()[-max(1, max_messages) :]
    for message_id in reversed(message_ids):
        status, fetched = mailbox.fetch(message_id, "(BODY.PEEK[])")
        if status != "OK":
            continue
        raw = _fetched_message_bytes(fetched)
        if not raw:
            continue
        message = message_from_bytes(raw, policy=default)
        if not _is_fresh(message, requested_at):
            continue
        code = _extract_sso_code(message)
        if code:
            return code
    return ""


def _fetched_message_bytes(fetched) -> bytes:
    for item in fetched or []:
        if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
            return item[1]
    return b""


def _is_fresh(message: Message, requested_at: float) -> bool:
    try:
        sent_at = parsedate_to_datetime(str(message.get("Date") or ""))
    except (TypeError, ValueError, OverflowError):
        return False
    if sent_at is None:
        return False
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)
    return sent_at.timestamp() >= requested_at - 120


def _extract_sso_code(message: Message) -> str:
    subject = str(message.get("Subject") or "")
    sender = str(message.get("From") or "")
    body = _message_text(message)
    searchable = "\n".join((subject, sender, body))
    lowered = searchable.lower()
    if "验证码" not in searchable and "verification code" not in lowered:
        return ""
    if not any(
        marker in lowered
        for marker in ("统一身份认证", "身份认证", "sso.bit.edu.cn", "verification code")
    ):
        return ""

    patterns = (
        r"(?:验证码|校验码|动态码|verification\s+code)[^\d]{0,24}(\d{4,8})(?!\d)",
        r"(?<!\d)(\d{4,8})[^\d]{0,24}(?:验证码|校验码|动态码|verification\s+code)",
    )
    for pattern in patterns:
        match = re.search(pattern, searchable, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _message_text(message: Message) -> str:
    parts: list[str] = []
    candidates = message.walk() if message.is_multipart() else (message,)
    for part in candidates:
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeDecodeError):
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        text = str(content)
        if content_type == "text/html":
            text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
        parts.append(text)
    return "\n".join(parts)
