from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import string
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asymmetric_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class AuthError(RuntimeError):
    """Raised when BIT SSO authentication fails."""


class SmsVerificationRequired(AuthError):
    """Raised when password login needs an interactive verification code."""

    def __init__(self, masked_phone: str, channel: str = "sms"):
        self.masked_phone = masked_phone
        self.channel = channel
        channel_name = "邮箱" if channel == "email" else "短信"
        super().__init__(
            f"统一身份认证需要{channel_name}二次验证（{masked_phone}），"
            "请使用 /lexue login 完成授权。"
        )


class CaptchaRequired(AuthError):
    """Raised when the account is challenged with an image captcha."""


@dataclass(frozen=True, slots=True)
class SmsCodeContext:
    masked_phone: str
    purpose: str = "password_second_factor"
    channel: str = "sms"
    requested_at: float = 0.0


SmsCodeCallback = Callable[[SmsCodeContext], Awaitable[str]]


_LOGIN_ERROR_MESSAGES = {
    "1030027": "用户名或密码错误",
    "1030028": "账号已被锁定",
    "1030031": "用户名或密码错误",
    "1320007": "验证码错误或已失效",
    "1320010": "图形验证码错误",
    "1330001": "登录被账号风控拒绝",
    "1410040": "账号状态无效",
    "1410041": "账号状态无效",
    "3910001": "账号已休眠，请先完成账号激活",
}

_URL_CRYPTO_PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAjVr1zKwohU3xA0afprWLSQvIymaSH/V27MedFc+CecXSnORIFMAp4uEIb4taDq/2X4eMeTI66Mu/rB5GKSFDbExF2Gu4NaO/CNDpf1gHMScUrIFCh4CDqzBnx17kclvezLkIK0T8FVa4cRsINvzjbnA6jUSMaf6Fm1n9wTAtW6QYBjssGOEtCj+c38PTBdFMmJbXp3brt1tEBesz6lb3Fjp76FGvDZ08xtYG8fxYPuiMwKU04eS+mcX/BunwgpU3zwekHYB+PWRIvq0lBry9Wms25sJE5T/RAv5fEuMLbBkfcZK3+7ivSZthTmPpr2Ap/ji70ZZ6u2jvR5VJq+LJHQIDAQAB
-----END PUBLIC KEY-----"""

_FINGERPRINT_FONTS = ["Arial", "Helvetica Neue", "PingFang SC", "Times New Roman"]
_FINGERPRINT_RESOLUTION = [956, 1470]


@dataclass(slots=True)
class BitSsoV4Client:
    """BIT SSO password + verification-code flow based on BIT-Login v4.0.2."""

    session: httpx.AsyncClient
    base_url: str = "https://sso.bit.edu.cn"
    _login_referer: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self._login_referer = f"{self.base_url}/cas/login"

    async def login_for_service(
        self,
        username: str,
        password: str,
        service_url: str,
        *,
        sms_code_callback: SmsCodeCallback | None = None,
        trust_device: bool = False,
    ) -> str:
        """Establish the target service session, requesting a code when required."""

        username = username.strip()
        if not username or not password:
            raise AuthError("统一身份认证账号和密码不能为空")

        loaded = await self.session.get(
            f"{self.base_url}/cas/login",
            params={"service": service_url},
            follow_redirects=False,
        )
        loaded.raise_for_status()
        if loaded.is_redirect:
            return await self._finish_service_redirect(loaded)

        soup = BeautifulSoup(loaded.text, "html.parser")
        crypto = _required_text(soup, "#login-croypto", "login crypto")
        execution = _required_text(soup, "#login-page-flowkey", "login execution")
        self._login_referer = str(loaded.url)

        captcha = await self._request_json(
            "GET",
            f"{self.base_url}/cas/api/protected/user/findCaptchaCount/{quote(username, safe='')}",
        )
        captcha_data = captcha.get("data") if isinstance(captcha.get("data"), dict) else {}
        if _json_truthy(captcha_data.get("captchaInvisible")):
            raise CaptchaRequired(
                "统一身份认证要求图形验证码，当前机器人登录仅支持默认的邮箱二次验证流程。"
            )

        form = {
            "type": "UsernamePassword",
            "_eventId": "submit",
            "geolocation": "",
            "execution": execution,
            "username": username,
            "croypto": crypto,
            "captcha_code": "",
            "password": encrypt_sso_password(password, crypto),
            "captcha_payload": encrypt_sso_password(
                json.dumps(captcha_data.get("captchaPayload") or {}, separators=(",", ":")),
                crypto,
            ),
        }
        await self._add_risk_fields(form, soup, username, crypto)
        response = await self._login_post(_login_form_action(soup, str(loaded.url)), form)

        second_factor = _parse_second_factor_page(response)
        if second_factor is not None:
            response = await self._complete_second_factor(
                username,
                second_factor,
                sms_code_callback,
                trust_device,
            )
        else:
            self._raise_if_login_rejected(response, "用户名或密码错误")

        return await self._finish_service_redirect(response)

    async def _complete_second_factor(
        self,
        username: str,
        page: dict[str, str],
        callback: SmsCodeCallback | None,
        trust_device: bool,
    ) -> httpx.Response:
        self._login_referer = f"{self.base_url}/cas/"
        email = await self._second_factor_email(page)
        masked_email = _mask_email(email)
        if callback is None:
            raise SmsVerificationRequired(masked_email, channel="email")

        requested_at = time.time()
        sent = await self._request_json(
            "POST",
            f"{self.base_url}/cas/api/protected/mail/publicNoToken/sendMailCode4SecondAuth",
            json_body={
                "type": "DEFAULT",
                "mbemail": email,
                "businessNo": "2025031701",
            },
        )
        sent_data = sent.get("data")
        sent_rejected = isinstance(sent_data, dict) and sent_data.get("result") is False
        if (_response_code(sent) != 200 or sent_rejected) and not _sms_code_remains_valid(sent):
            raise AuthError(_response_message(sent) or "邮箱验证码发送失败")

        code = (
            await callback(
                SmsCodeContext(
                    masked_phone=masked_email,
                    channel="email",
                    requested_at=requested_at,
                )
            )
        ).strip()
        if not re.fullmatch(r"\d{4,8}", code):
            raise AuthError("邮箱验证码格式无效")

        checked = await self._request_json(
            "POST",
            f"{self.base_url}/cas/api/protected/mail/publicNoToken/checkTokenResult",
            json_body={
                "email": email,
                "token": code,
                "deleteFlag": False,
            },
        )
        if _response_code(checked) != 200:
            raise AuthError(_response_message(checked) or "邮箱验证码错误或已失效")

        response = await self._login_post(
            page["form_action"],
            {
                "username": page.get("user_id") or username,
                "password": code,
                "type": "mailLogin",
                "_eventId": "submit",
                "geolocation": "",
                "execution": page["execution"],
                "captcha_code": "",
                "trustDevice": str(trust_device).lower(),
            },
        )
        self._raise_if_login_rejected(response, "邮箱验证码错误或已失效，请重新发起登录")
        return response

    async def _second_factor_email(self, page: dict[str, str]) -> str:
        page_email = str(page.get("email") or "").strip()
        response = await self._request_json(
            "POST",
            f"{self.base_url}/cas/api/protected/mail/publicNoToken/findMail",
            json_body={"userId": page["user_object_id"]},
        )
        data = response.get("data")
        if isinstance(data, str):
            email = data.strip()
        elif isinstance(data, dict):
            email = str(data.get("mbemail") or data.get("email") or data.get("mail") or "").strip()
        else:
            email = ""
        email = email or page_email
        if not email:
            if _response_code(response) != 200:
                raise AuthError(_response_message(response) or "统一身份认证查询绑定邮箱失败")
            raise AuthError("统一身份认证账号未绑定可用邮箱")
        return email

    async def _second_factor_phone(self, user_object_id: str) -> dict[str, object]:
        aes_key = os.urandom(16)
        plaintext = json.dumps(
            {"userId": user_object_id}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        body = base64.b64encode(_aes_encrypt(plaintext, aes_key)).decode("ascii")
        public_key = serialization.load_pem_public_key(_URL_CRYPTO_PUBLIC_KEY)
        encrypted_key = public_key.encrypt(
            base64.b64encode(aes_key),
            asymmetric_padding.PKCS1v15(),
        )
        response = await self._request(
            "POST",
            f"{self.base_url}/cas/api/protected/sms/getPhoneNumberByUserId",
            content=body,
            extra_headers={
                "Content-Type": "application/json",
                "hasCrypto": "true",
                "privateKey": base64.b64encode(encrypted_key).decode("ascii"),
            },
        )
        if not response.text.strip():
            raise AuthError("统一身份认证未返回手机信息")
        decoded = _decrypt_url_crypto_response(response.text, aes_key)
        data = decoded.get("data") if isinstance(decoded, dict) else None
        return data if isinstance(data, dict) else {}

    async def _add_risk_fields(
        self,
        form: dict[str, str],
        soup: BeautifulSoup,
        username: str,
        crypto: str,
    ) -> None:
        if _optional_text(soup, "#riskSystemSwitch").upper() != "USTC":
            return
        payload = await self._default_risk_payload()
        form.update(
            {
                "risk_payload": encrypt_sso_password(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")), crypto
                ),
                "targetSystem": _optional_text(soup, "#targetSystem") or "sso",
                "siteId": _optional_text(soup, "#siteId") or "sourceId",
                "riskEngine": "true",
            }
        )

    async def _default_risk_payload(self) -> dict[str, object]:
        device = _cookie_value(self.session, "device")
        if not device:
            device = hashlib.sha256(str(int(time.time() * 1000)).encode("ascii")).hexdigest()
            host = urlparse(self.base_url).hostname or "sso.bit.edu.cn"
            self.session.cookies.set("device", device, domain=host, path="/")
        group_id = _cookie_value(self.session, "riskSystemGroupId")
        user_agent = self.session.headers.get("User-Agent", "")
        fingerprint = _browser_fingerprint(device, user_agent, group_id)
        try:
            result = await self._request_json(
                "POST", f"{self.base_url}/ustc-rba-front/fp", json_body=fingerprint
            )
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            token = str(result.get("responsetoken") or data.get("responsetoken") or "")
            if token:
                return {"token": token, "groupId": group_id}
        except (AuthError, httpx.HTTPError, ValueError):
            pass
        return {"error": True}

    async def _login_post(self, url: str, form: dict[str, str]) -> httpx.Response:
        response = await self._request(
            "POST",
            url,
            data=form,
            follow_redirects=False,
            raise_for_status=False,
        )
        is_cas_login = urlparse(str(response.url)).path.rstrip("/").endswith("/cas/login")
        if response.status_code >= 400 and not (
            response.status_code in {400, 401, 403} and is_cas_login
        ):
            response.raise_for_status()
        return response

    async def _finish_service_redirect(self, response: httpx.Response) -> str:
        location = response.headers.get("Location")
        if not location:
            self._raise_if_login_rejected(response, "统一身份认证未签发服务票据")
            raise AuthError("统一身份认证未签发乐学服务票据")
        callback_url = urljoin(str(response.url), location)
        callback = await self.session.get(callback_url, follow_redirects=True)
        callback.raise_for_status()
        if "/cas/login" in str(callback.url) or _looks_like_login_page(callback.text):
            raise AuthError("乐学服务回调后仍停留在统一身份认证页面")
        return str(callback.url)

    def _raise_if_login_rejected(self, response: httpx.Response, fallback: str) -> None:
        soup = BeautifulSoup(response.text, "html.parser")
        is_login_url = urlparse(str(response.url)).path.rstrip("/").endswith("/cas/login")
        login_markup = any(
            marker in response.text
            for marker in (
                "login-page-flowkey",
                "normalLoginForm",
                "smsLoginForm",
                "secondSmsLoginForm",
                "secondMailLoginForm",
                "cas-gateway",
            )
        )
        if _looks_like_login_page(response.text) or (is_login_url and login_markup) or (
            is_login_url and response.status_code in {400, 401, 403}
        ):
            message = _optional_text(soup, "#login-error-msg")
            code = _optional_text(soup, "#login-error-code")
            raise AuthError(message or _LOGIN_ERROR_MESSAGES.get(code) or fallback)

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        response = await self._request(method, url, json_body=json_body)
        try:
            value = response.json()
        except ValueError as exc:
            raise AuthError("统一身份认证接口返回了无效 JSON") from exc
        if not isinstance(value, dict):
            raise AuthError("统一身份认证接口未返回 JSON 对象")
        return value

    async def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        json_body: dict[str, object] | None = None,
        content: str | None = None,
        extra_headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
        raise_for_status: bool = True,
    ) -> httpx.Response:
        headers = {"Referer": self._login_referer}
        if method.upper() != "GET":
            headers["Origin"] = self.base_url
        if "protected" in url:
            headers.update(_protected_csrf_headers())
            headers["Sid-Language"] = "zh_CN"
        if extra_headers:
            headers.update(extra_headers)
        params = {str(int(time.time() * 1000)): ""} if method.upper() == "GET" else None
        response = await self.session.request(
            method,
            url,
            params=params,
            data=data,
            json=json_body,
            content=content,
            headers=headers,
            follow_redirects=follow_redirects,
        )
        if raise_for_status:
            response.raise_for_status()
        return response


def _parse_second_factor_page(response: httpx.Response) -> dict[str, str] | None:
    html = response.text
    if not any(
        marker in html
        for marker in ("secondSmsLoginForm", "secondMailLoginForm", "second-auth-tip", "cas-gateway")
    ):
        return None
    soup = BeautifulSoup(html, "html.parser")
    execution = _optional_text(soup, "#login-page-flowkey")
    user_object_id = _optional_text(soup, "#user-object-id")
    if not execution or not user_object_id:
        return None
    form = soup.select_one("#secondSmsLoginForm") or soup.find("form")
    action = form.get("action") if form else None
    return {
        "execution": execution,
        "user_object_id": user_object_id,
        "user_id": _optional_text(soup, "#second-auth-user-id"),
        "phone": _optional_text(soup, "#phone-number"),
        "email": _optional_text(soup, "#user-email-value"),
        "form_action": urljoin(str(response.url), action or "login"),
    }


def _optional_text(soup: BeautifulSoup, selector: str) -> str:
    node = soup.select_one(selector)
    if node is None:
        return ""
    return str(node.get("value") or node.get_text(strip=True) or "").strip()


def _json_truthy(value: object) -> bool:
    if value is None or value is False or value == 0 or value == "0" or value == "false":
        return False
    return bool(value)


def _response_code(value: dict[str, object]) -> int | None:
    try:
        return int(value.get("code"))
    except (TypeError, ValueError):
        return None


def _response_message(value: dict[str, object]) -> str:
    for container in (value, value.get("data")):
        if not isinstance(container, dict):
            continue
        for key in ("message", "msg", "errorMessage"):
            message = container.get(key)
            if message:
                return str(message).strip()
    return ""


def _sms_code_remains_valid(value: dict[str, object]) -> bool:
    message = _response_message(value)
    return "验证码" in message and "有效期内" in message and "重复发送" in message


def _mask_email(email: str) -> str:
    local, separator, domain = email.partition("@")
    if not separator:
        return "绑定邮箱"
    visible = local[:4]
    return f"{visible}****@{domain}"


def _aes_encrypt(plaintext: bytes, key: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _aes_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def _decrypt_url_crypto_response(value: str, aes_key: bytes) -> object:
    current = value
    for _ in range(4):
        try:
            parsed = json.loads(current)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None:
            if not isinstance(parsed, str):
                return parsed
            if parsed != current:
                current = parsed
                continue
        try:
            current = _aes_decrypt(base64.b64decode(current), aes_key).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return current
    try:
        return json.loads(current)
    except ValueError:
        return current


def _protected_csrf_headers() -> dict[str, str]:
    alphabet = string.ascii_letters + string.digits
    key = "".join(secrets.choice(alphabet) for _ in range(32))
    encoded = base64.b64encode(key.encode("ascii")).decode("ascii")
    midpoint = len(encoded) // 2
    mixed = encoded[:midpoint] + encoded + encoded[midpoint:]
    return {
        "Csrf-Key": key,
        "Csrf-Value": hashlib.md5(mixed.encode("ascii"), usedforsecurity=False).hexdigest(),
    }


def _cookie_value(session: httpx.AsyncClient, name: str) -> str:
    for cookie in session.cookies.jar:
        if cookie.name == name:
            return cookie.value
    return ""


def _browser_fingerprint(
    cookie_value: str,
    user_agent: str,
    group_id: str,
) -> dict[str, object]:
    values = {
        "fonts": json.dumps(_FINGERPRINT_FONTS, ensure_ascii=False, separators=(",", ":")),
        "deviceMemory": "16",
        "hardwareConcurrency": "10",
        "timezone": json.dumps("Asia/Shanghai"),
        "cpuClass": json.dumps("not available"),
        "platform": json.dumps("MacIntel"),
        "language": json.dumps("zh-CN"),
        "screenResolution": json.dumps(_FINGERPRINT_RESOLUTION, separators=(",", ":")),
    }

    def digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    return {
        "fonts": digest(values["fonts"]),
        "deviceMemory": digest(values["deviceMemory"]),
        "hardwareConcurrency": digest(values["hardwareConcurrency"]),
        "localgroupId": group_id,
        "timezone": values["timezone"],
        "cpuClass": digest(values["cpuClass"]),
        "platform": values["platform"],
        "language": values["language"],
        "screenResolution": values["screenResolution"],
        "fingerprint": digest("".join(values.values())),
        "cookieValue": cookie_value,
        "userAgent": user_agent,
        "platformAuthenticator": "support",
    }


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

    session: httpx.AsyncClient
    sso_ticket_url: str = "https://sso.bit.edu.cn/cas/v1/tickets"

    async def get_tgt(self, username: str, password: str) -> str:
        response = await self.session.post(
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

    async def get_service_ticket(self, tgt_url: str, service_url: str) -> str:
        response = await self.session.post(
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

    async def login_for_service(self, username: str, password: str, service_url: str) -> str:
        """Create a service session with CAS v1 tickets.

        Adapted from BIT-Login's core flow: request TGT, exchange it for a
        service ticket, then visit the service callback URL with that ticket.
        """

        tgt_url = await self.get_tgt(username, password)
        ticket = await self.get_service_ticket(tgt_url, service_url)
        callback_url = self.build_callback_url(service_url, ticket)
        response = await self.session.get(callback_url, follow_redirects=True)
        response.raise_for_status()
        if "sso.bit.edu.cn/cas/login" in str(response.url) or _looks_like_login_page(response.text):
            raise AuthError("BIT SSO ticket login stayed on the login form")
        return str(response.url)


@dataclass(slots=True)
class BitSsoPageClient:
    """Browser-like BIT SSO page login adapted from BIT101-Android."""

    session: httpx.AsyncClient
    base_url: str = "https://sso.bit.edu.cn"

    async def login_global_like_android(self, username: str, password: str) -> str:
        """Login to the bare SSO page like BIT101-Android does.

        BIT101-Android first creates a global SSO session without a service URL.
        Later requests to Lexue follow CAS redirects and reuse that SSO cookie
        to create the Lexue-specific session.
        """

        login_url = urljoin(self.base_url, "/cas/login")
        init_response = await self.session.get(login_url)
        init_response.raise_for_status()

        soup = BeautifulSoup(init_response.text, "html.parser")
        crypto = _required_text(soup, "#login-croypto", "login crypto")
        execution = _required_text(soup, "#login-page-flowkey", "login execution")

        response = await self.session.post(
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
            follow_redirects=True,
        )
        response.raise_for_status()
        return str(response.url)

    async def login(self, username: str, password: str) -> None:
        login_url = urljoin(self.base_url, "/cas/login")
        init_response = await self.session.get(login_url)
        init_response.raise_for_status()

        soup = BeautifulSoup(init_response.text, "html.parser")
        crypto = _required_text(soup, "#login-croypto", "login crypto")
        execution = _required_text(soup, "#login-page-flowkey", "login execution")
        encrypted_password = encrypt_sso_password(password, crypto)
        encrypted_payload = encrypt_sso_password("{}", crypto)

        response = await self.session.post(
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

    async def login_for_service(self, username: str, password: str, service_url: str) -> str:
        """Login through the SSO page created by the target service.

        Lexue should initiate CAS with its own `service` parameter. Visiting the
        service first gives us the exact login form/action and hidden fields.
        """

        init_response = await self.session.get(service_url, follow_redirects=True)
        init_response.raise_for_status()
        if not _looks_like_login_page(init_response.text):
            return str(init_response.url)

        login_url = str(init_response.url)
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

        response = await self.session.post(
            _login_form_action(soup, login_url),
            data=data,
            headers={"Referer": login_url},
            follow_redirects=True,
        )
        response.raise_for_status()
        if _looks_like_login_page(response.text) and "/cas/login" in str(response.url):
            raise AuthError("BIT SSO service login stayed on the login form")
        return str(response.url)


def new_session() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
        },
        follow_redirects=True,
        timeout=20.0,
    )


def _required_text(soup: BeautifulSoup, selector: str, label: str) -> str:
    node = soup.select_one(selector)
    if node is None:
        raise AuthError(f"BIT SSO page is missing {label}")
    value = str(node.get("value") or node.get_text(strip=True) or "").strip()
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
