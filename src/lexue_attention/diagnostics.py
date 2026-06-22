from __future__ import annotations

import base64
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .auth import (
    AuthError,
    BitSsoPageClient,
    BitSsoTicketClient,
    encrypt_sso_password,
    new_session,
)


@dataclass(frozen=True, slots=True)
class PageSummary:
    status_code: int
    url: str
    title: str
    is_login_page: bool
    form_action: str
    input_names: tuple[str, ...]
    has_sesskey: bool


def diagnose_login(username: str, password: str, lexue_base_url: str) -> list[str]:
    service_url = lexue_base_url.rstrip("/") + "/login/index.php"
    lines = [
        "diagnose-login",
        f"lexue_base_url={lexue_base_url}",
        f"service_url={service_url}",
    ]

    lines.extend(_probe_initial_lexue(lexue_base_url))
    lines.extend(_probe_android_like_login(username, password, lexue_base_url))
    lines.extend(_probe_ticket_login(username, password, service_url))
    lines.extend(_probe_page_login(username, password, lexue_base_url))
    return lines


def _probe_initial_lexue(lexue_base_url: str) -> list[str]:
    session = new_session()
    try:
        response = session.get(lexue_base_url, allow_redirects=True)
        summary = summarize_page(response)
        return [
            "",
            "[initial-lexue]",
            _format_summary(summary),
        ]
    except requests.RequestException as exc:
        return ["", "[initial-lexue]", f"error={type(exc).__name__}: {exc}"]


def _probe_ticket_login(username: str, password: str, service_url: str) -> list[str]:
    lines = ["", "[ticket-auth]"]
    for mode, secret in (("plain", password), ("xor", _xor_password(password))):
        session = new_session()
        client = BitSsoTicketClient(session)
        lines.append(f"mode={mode}")
        try:
            tgt_response = session.post(
                client.sso_ticket_url,
                data={"username": username, "password": secret},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            lines.append(
                "  tgt_status="
                f"{tgt_response.status_code}; has_location={bool(tgt_response.headers.get('Location'))}; "
                f"title={_page_title(tgt_response.text) or '<none>'}"
            )
            if tgt_response.status_code != 201:
                continue

            tgt_url = tgt_response.headers.get("Location")
            if not tgt_url:
                lines.append("  result=failed: no TGT Location header")
                continue

            st_response = session.post(
                tgt_url,
                data={"service": service_url},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            lines.append(f"  st_status={st_response.status_code}; ticket_len={len(st_response.text.strip())}")
            if st_response.status_code != 200:
                continue

            callback_url = client.build_callback_url(service_url, st_response.text.strip())
            callback_response = session.get(callback_url, allow_redirects=True)
            lines.append("  callback=" + _format_summary(summarize_page(callback_response)))
        except requests.RequestException as exc:
            lines.append(f"  error={type(exc).__name__}: {exc}")
    return lines


def _probe_android_like_login(username: str, password: str, lexue_base_url: str) -> list[str]:
    lines = ["", "[android-like-auth]"]
    session = new_session()
    try:
        final_url = BitSsoPageClient(session).login_global_like_android(username, password)
        lines.append(f"global_login_final_url={_redact_url(final_url)}")
        lines.append(f"cookies_after_global_login={_cookie_domains(session)}")
        lexue_response = session.get(lexue_base_url, allow_redirects=True)
        lines.append("lexue_after_global=" + _format_summary(summarize_page(lexue_response)))
        lines.append(f"cookies_after_lexue={_cookie_domains(session)}")
    except requests.HTTPError as exc:
        lines.append(f"error={type(exc).__name__}: {exc}")
        if exc.response is not None:
            lines.append("response=" + _format_summary(summarize_page(exc.response)))
            lines.append(f"cookies={_cookie_domains(session)}")
    except (requests.RequestException, AuthError) as exc:
        lines.append(f"error={type(exc).__name__}: {exc}")
        lines.append(f"cookies={_cookie_domains(session)}")
    return lines


def _probe_page_login(username: str, password: str, lexue_base_url: str) -> list[str]:
    lines = ["", "[page-auth]"]
    session = new_session()
    try:
        init_response = session.get(lexue_base_url, allow_redirects=True)
        init_summary = summarize_page(init_response)
        lines.append("init=" + _format_summary(init_summary))
        if not init_summary.is_login_page:
            lines.append("result=already authenticated or not redirected to SSO")
            return lines

        soup = BeautifulSoup(init_response.text, "html.parser")
        crypto = _text(soup, "#login-croypto")
        execution = _text(soup, "#login-page-flowkey")
        lines.append(f"crypto_present={bool(crypto)}; execution_present={bool(execution)}")
        if not crypto or not execution:
            lines.append("result=failed: login form is missing crypto/execution")
            return lines

        data = _collect_form_data(soup)
        data.update(
            {
                "username": username,
                "password": encrypt_sso_password(password, crypto),
                "execution": execution,
                "croypto": crypto,
                "captcha_payload": encrypt_sso_password("{}", crypto),
                "type": "UsernamePassword",
                "geolocation": "",
                "captcha_code": "",
                "_eventId": "submit",
            }
        )
        response = session.post(
            _login_form_action(soup, init_response.url),
            data=data,
            headers={"Referer": init_response.url},
            allow_redirects=True,
        )
        lines.append("post=" + _format_summary(summarize_page(response)))
    except (requests.RequestException, AuthError) as exc:
        lines.append(f"error={type(exc).__name__}: {exc}")
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            lines.append("response=" + _format_summary(summarize_page(exc.response)))
    return lines


def summarize_page(response: requests.Response) -> PageSummary:
    soup = BeautifulSoup(response.text, "html.parser")
    form = soup.find("form")
    action = form.get("action", "") if form else ""
    input_names = []
    for node in (form.find_all("input") if form else soup.find_all("input")):
        name = node.get("name")
        if name:
            input_names.append(name)
    return PageSummary(
        status_code=response.status_code,
        url=_redact_url(response.url),
        title=_page_title(response.text) or "<none>",
        is_login_page=soup.select_one("#login-croypto") is not None
        or soup.select_one("#login-page-flowkey") is not None,
        form_action=_redact_url(action),
        input_names=tuple(input_names),
        has_sesskey="sesskey" in response.text,
    )


def _format_summary(summary: PageSummary) -> str:
    return (
        f"status={summary.status_code}; url={summary.url}; title={summary.title}; "
        f"is_login_page={summary.is_login_page}; has_sesskey={summary.has_sesskey}; "
        f"form_action={summary.form_action or '<none>'}; input_names={','.join(summary.input_names) or '<none>'}"
    )


def _page_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return soup.title.get_text(" ", strip=True) if soup.title else ""


def _text(soup: BeautifulSoup, selector: str) -> str:
    node = soup.select_one(selector)
    return node.get_text(strip=True) if node else ""


def _collect_form_data(soup: BeautifulSoup) -> dict[str, str]:
    form = soup.find("form")
    inputs = form.find_all("input") if form else soup.find_all("input")
    data: dict[str, str] = {}
    for node in inputs:
        name = node.get("name")
        if name:
            data[name] = node.get("value", "")
    return data


def _login_form_action(soup: BeautifulSoup, fallback_url: str) -> str:
    form = soup.find("form")
    action = form.get("action") if form else None
    if not action:
        return fallback_url
    return requests.compat.urljoin(fallback_url, action)


def _xor_password(password: str) -> str:
    key = b"bit-sso-AutoLogin-key"
    raw = password.encode("utf-8")
    out = bytes(raw[index] ^ key[index % len(key)] for index in range(len(raw)))
    return "xor:" + base64.b64encode(out).decode("ascii")


def _redact_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    path = parsed.path
    if "/cas/v1/tickets/" in path:
        path = "/cas/v1/tickets/<redacted>"
    query = parsed.query
    if "ticket=" in query:
        query = "<redacted-ticket-query>"
    if "service=" in query:
        query = "<redacted-service-query>"
    return parsed._replace(path=path, query=query, fragment="").geturl()


def _cookie_domains(session: requests.Session) -> str:
    items = sorted({f"{cookie.domain}:{cookie.name}" for cookie in session.cookies})
    return ",".join(items) or "<none>"
