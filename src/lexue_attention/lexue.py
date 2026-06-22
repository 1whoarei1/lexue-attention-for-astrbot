from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


class LexueError(RuntimeError):
    """Raised when Lexue calendar export or fetching fails."""


@dataclass(slots=True)
class LexueClient:
    """Client for Lexue calendar export.

    The endpoint sequence comes from BIT101-Android:
    GET `/` -> extract `sesskey` -> POST `/calendar/export.php` -> parse
    `.calendarurl` -> GET the generated `.ics` URL.
    """

    session: requests.Session
    base_url: str = "https://lexue.bit.edu.cn"
    request_timeout: float = 20.0

    def get_sesskey(self) -> str:
        response = self.session.get(urljoin(self.base_url, "/"), timeout=self.request_timeout)
        response.raise_for_status()

        match = re.search(r"""["']sesskey["']\s*:\s*["']([^"']+)["']""", response.text)
        if not match:
            raise LexueError(_page_error("Lexue index page did not contain sesskey", response))
        return match.group(1)

    def export_calendar_url(self, sesskey: str | None = None) -> str:
        final_sesskey = sesskey or self.get_sesskey()
        response = self.session.post(
            urljoin(self.base_url, "/calendar/export.php"),
            data={
                "sesskey": final_sesskey,
                "_qf__core_calendar_export_form": "1",
                "events[exportevents]": "all",
                "period[timeperiod]": "recentupcoming",
                "generateurl": "获取日历网址",
            },
            timeout=self.request_timeout,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        calendar_node = soup.select_one(".calendarurl")
        text = calendar_node.get_text(" ", strip=True) if calendar_node else ""
        match = re.search(r"https?://\S+", text)
        if not match:
            raise LexueError(_page_error("Lexue calendar export page did not contain a calendar URL", response))
        return match.group(0)

    def fetch_ics(self, calendar_url: str) -> str:
        response = self.session.get(calendar_url, timeout=self.request_timeout)
        response.raise_for_status()
        return response.text


def _page_error(message: str, response: requests.Response) -> str:
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return f"{message}; url={response.url}; title={title or '<none>'}"
