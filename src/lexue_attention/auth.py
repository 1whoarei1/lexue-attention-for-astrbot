from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class AuthError(RuntimeError):
    """Raised when BIT SSO authentication fails."""


def encrypt_sso_password(password: str, login_crypto: str) -> str:
    """Encrypt a password for BIT SSO page login.

    This follows BIT101-Android's `AESUtils.encryptPassword`: Base64 decode the
    `login-croypto` value, then AES/ECB with PKCS#7-compatible padding.
    """

    key = base64.b64decode(login_crypto)
    plaintext = password.encode("utf-8")
    padding_len = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([padding_len]) * padding_len

    encryptor = Cipher(
        algorithms.AES(key),
        modes.ECB(),
        backend=default_backend(),
    ).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("ascii")


@dataclass(slots=True)
class BitSsoTicketClient:
    """CAS v1 ticket client adapted from BIT-Login.

    This is useful when a target service accepts a CAS service ticket directly.
    Lexue may still require following browser redirects, so `BitSsoPageClient`
    is also provided.
    """

    session: requests.Session
    sso_ticket_url: str = "https://sso.bit.edu.cn/cas/v1/tickets"

    def get_tgt(self, username: str, password: str) -> str:
        response = self.session.post(
            self.sso_ticket_url,
            data={"username": username, "password": password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code == 401:
            raise AuthError("BIT SSO rejected the username or password")
        if response.status_code != 201:
            raise AuthError(f"BIT SSO TGT request failed: HTTP {response.status_code}")

        tgt = response.headers.get("Location")
        if not tgt:
            match = re.search(r'action="([^"]+)"', response.text)
            tgt = match.group(1) if match else None
        if not tgt:
            raise AuthError("BIT SSO did not return a TGT URL")
        return tgt

    def get_service_ticket(self, tgt_url: str, service_url: str) -> str:
        response = self.session.post(
            tgt_url,
            data={"service": service_url},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != 200:
            raise AuthError(f"BIT SSO service-ticket request failed: HTTP {response.status_code}")
        return response.text.strip()

    def build_callback_url(self, service_url: str, ticket: str) -> str:
        separator = "&" if "?" in service_url else "?"
        return f"{service_url}{separator}ticket={ticket}"

    def login_for_service(self, username: str, password: str, service_url: str) -> str:
        """Create a service session with CAS v1 tickets.

        Adapted from BIT-Login's core flow: request TGT, exchange it for a
        service ticket, then visit the service callback URL with that ticket.
        """

        tgt_url = self.get_tgt(username, password)
        ticket = self.get_service_ticket(tgt_url, service_url)
        callback_url = self.build_callback_url(service_url, ticket)
        response = self.session.get(callback_url, allow_redirects=True)
        response.raise_for_status()
        if "sso.bit.edu.cn/cas/login" in response.url or _looks_like_login_page(response.text):
            raise AuthError(f"BIT SSO ticket login stayed on login form: {response.url}")
        return response.url


@dataclass(slots=True)
class BitSsoPageClient:
    """Browser-like BIT SSO page login adapted from BIT101-Android."""

    session: requests.Session
    base_url: str = "https://sso.bit.edu.cn"

    def login_global_like_android(self, username: str, password: str) -> str:
        """Login to the bare SSO page like BIT101-Android does.

        BIT101-Android first creates a global SSO session without a service URL.
        Later requests to Lexue follow CAS redirects and reuse that SSO cookie
        to create the Lexue-specific session.
        """

        login_url = urljoin(self.base_url, "/cas/login")
        init_response = self.session.get(login_url)
        init_response.raise_for_status()

        soup = BeautifulSoup(init_response.text, "html.parser")
        crypto = _required_text(soup, "#login-croypto", "login crypto")
        execution = _required_text(soup, "#login-page-flowkey", "login execution")

        response = self.session.post(
            login_url,
            data={
                "username": username,
                "password": encrypt_sso_password(password, crypto),
                "execution": execution,
                "croypto": crypto,
                "captcha_payload": encrypt_sso_password("{}", crypto),
                "type": "UsernamePassword",
                "geolocation": "",
                "captcha_code": "",
                "_eventId": "submit",
            },
            headers={"Referer": login_url},
            allow_redirects=True,
        )
        response.raise_for_status()
        return response.url

    def login(self, username: str, password: str) -> None:
        login_url = urljoin(self.base_url, "/cas/login")
        init_response = self.session.get(login_url)
        init_response.raise_for_status()

        soup = BeautifulSoup(init_response.text, "html.parser")
        crypto = _required_text(soup, "#login-croypto", "login crypto")
        execution = _required_text(soup, "#login-page-flowkey", "login execution")
        encrypted_password = encrypt_sso_password(password, crypto)
        encrypted_payload = encrypt_sso_password("{}", crypto)

        response = self.session.post(
            login_url,
            data={
                "username": username,
                "password": encrypted_password,
                "execution": execution,
                "croypto": crypto,
                "captcha_payload": encrypted_payload,
                "type": "UsernamePassword",
                "geolocation": "",
                "captcha_code": "",
                "_eventId": "submit",
            },
        )
        response.raise_for_status()

        if _looks_like_login_page(response.text):
            raise AuthError("BIT SSO page login did not leave the login form")

    def login_for_service(self, username: str, password: str, service_url: str) -> str:
        """Login through the SSO page created by the target service.

        Lexue should initiate CAS with its own `service` parameter. Visiting the
        service first gives us the exact login form/action and hidden fields.
        """

        init_response = self.session.get(service_url, allow_redirects=True)
        init_response.raise_for_status()
        if not _looks_like_login_page(init_response.text):
            return init_response.url

        login_url = init_response.url
        soup = BeautifulSoup(init_response.text, "html.parser")
        crypto = _required_text(soup, "#login-croypto", "login crypto")
        execution = _required_text(soup, "#login-page-flowkey", "login execution")

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

        response = self.session.post(
            _login_form_action(soup, login_url),
            data=data,
            headers={"Referer": login_url},
            allow_redirects=True,
        )
        response.raise_for_status()
        if _looks_like_login_page(response.text) and "/cas/login" in response.url:
            raise AuthError(f"BIT SSO service login stayed on login form: {response.url}")
        return response.url


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            )
        }
    )
    return session


def _required_text(soup: BeautifulSoup, selector: str, label: str) -> str:
    node = soup.select_one(selector)
    if node is None:
        raise AuthError(f"BIT SSO page is missing {label}")
    value = node.get_text(strip=True)
    if not value:
        raise AuthError(f"BIT SSO page has empty {label}")
    return value


def _looks_like_login_page(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    return soup.select_one("#login-croypto") is not None or soup.select_one("#login-page-flowkey") is not None


def _collect_form_data(soup: BeautifulSoup) -> dict[str, str]:
    data: dict[str, str] = {}
    form = soup.find("form")
    inputs = form.find_all("input") if form else soup.find_all("input")
    for node in inputs:
        name = node.get("name")
        if name:
            data[name] = node.get("value", "")
    return data


def _login_form_action(soup: BeautifulSoup, fallback_url: str) -> str:
    form = soup.find("form")
    action = form.get("action") if form else None
    return urljoin(fallback_url, action) if action else fallback_url
