import json

import httpx
import pytest

from lexue_attention.auth import (
    BitSsoV4Client,
    CaptchaRequired,
    SmsVerificationRequired,
    _aes_encrypt,
    _decrypt_url_crypto_response,
)


LOGIN_HTML = """
<form action="/cas/login">
  <span id="login-page-flowkey">flow-1</span>
  <span id="login-croypto">MDEyMzQ1Njc4OWFiY2RlZg==</span>
</form>
"""

SECOND_FACTOR_HTML = """
<form id="secondSmsLoginForm" action="/cas/login">
  <span id="login-page-flowkey">flow-2</span>
  <span id="user-object-id">user-object</span>
</form>
"""


@pytest.mark.asyncio
async def test_v4_password_login_follows_lexue_service_ticket():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/cas/login":
            return httpx.Response(200, text=LOGIN_HTML)
        if "findCaptchaCount" in request.url.path:
            return httpx.Response(200, json={"code": 200, "data": {"captchaInvisible": False}})
        if request.method == "POST" and request.url.path == "/cas/login":
            form = request.content.decode("utf-8")
            assert "type=UsernamePassword" in form
            return httpx.Response(
                302,
                headers={"Location": "https://lexue.example/login/index.php?ticket=ST-1"},
            )
        return httpx.Response(200, text='<script>var M = {"sesskey":"ok"};</script>')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as session:
        final_url = await BitSsoV4Client(session, "https://sso.example").login_for_service(
            "student",
            "password",
            "https://lexue.example/login/index.php",
        )

    assert final_url == "https://lexue.example/login/index.php?ticket=ST-1"
    assert [request.url.path for request in requests] == [
        "/cas/login",
        "/cas/api/protected/user/findCaptchaCount/student",
        "/cas/login",
        "/login/index.php",
    ]


@pytest.mark.asyncio
async def test_v4_second_factor_submits_sms_code(monkeypatch):
    requests: list[httpx.Request] = []
    login_posts = 0

    async def fake_phone(self, user_object_id: str):
        assert user_object_id == "user-object"
        return {"tel": "opaque-phone", "maskTel": "138****8000"}

    monkeypatch.setattr(BitSsoV4Client, "_second_factor_phone", fake_phone)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_posts
        requests.append(request)
        if request.method == "GET" and request.url.path == "/cas/login":
            return httpx.Response(200, text=LOGIN_HTML)
        if "findCaptchaCount" in request.url.path:
            return httpx.Response(200, json={"code": 200, "data": {"captchaInvisible": False}})
        if request.method == "POST" and request.url.path == "/cas/login":
            login_posts += 1
            if login_posts == 1:
                return httpx.Response(200, text=SECOND_FACTOR_HTML)
            form = request.content.decode("utf-8")
            assert "type=smsLogin" in form
            assert "password=123456" in form
            return httpx.Response(
                302,
                headers={"Location": "https://lexue.example/login/index.php?ticket=ST-SMS"},
            )
        if request.url.path.endswith("/sendSmsCode"):
            assert json.loads(request.content) == {"phone": "opaque-phone", "businessNo": "0008"}
            return httpx.Response(200, json={"code": 200})
        if request.url.path.endswith("/checkToken"):
            assert json.loads(request.content) == {
                "phone": "opaque-phone",
                "token": "123456",
                "delete": False,
                "trustDevice": False,
            }
            return httpx.Response(200, json={"code": 200})
        return httpx.Response(200, text='<script>var M = {"sesskey":"ok"};</script>')

    seen_masked_phone: list[str] = []

    async def sms_callback(context):
        seen_masked_phone.append(context.masked_phone)
        return "123456"

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as session:
        final_url = await BitSsoV4Client(session, "https://sso.example").login_for_service(
            "student",
            "password",
            "https://lexue.example/login/index.php",
            sms_code_callback=sms_callback,
        )

    assert final_url.endswith("ticket=ST-SMS")
    assert seen_masked_phone == ["138****8000"]
    assert any(request.url.path.endswith("/sendSmsCode") for request in requests)


@pytest.mark.asyncio
async def test_v4_second_factor_without_callback_does_not_send_sms(monkeypatch):
    requests: list[httpx.Request] = []

    async def fake_phone(self, user_object_id: str):
        return {"tel": "opaque-phone", "maskTel": "138****8000"}

    monkeypatch.setattr(BitSsoV4Client, "_second_factor_phone", fake_phone)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/cas/login":
            return httpx.Response(200, text=LOGIN_HTML)
        if "findCaptchaCount" in request.url.path:
            return httpx.Response(200, json={"code": 200, "data": {"captchaInvisible": False}})
        if request.method == "POST" and request.url.path == "/cas/login":
            return httpx.Response(200, text=SECOND_FACTOR_HTML)
        raise AssertionError("SMS endpoint must not be called without an interactive callback")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as session:
        with pytest.raises(SmsVerificationRequired, match="/lexue login"):
            await BitSsoV4Client(session, "https://sso.example").login_for_service(
                "student",
                "password",
                "https://lexue.example/login/index.php",
            )

    assert not any(request.url.path.endswith("/sendSmsCode") for request in requests)


@pytest.mark.asyncio
async def test_v4_captcha_requirement_is_explicit():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cas/login":
            return httpx.Response(200, text=LOGIN_HTML)
        return httpx.Response(
            200,
            json={"code": 200, "data": {"captchaInvisible": True, "captchaUrl": "captcha"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as session:
        with pytest.raises(CaptchaRequired, match="图形验证码"):
            await BitSsoV4Client(session, "https://sso.example").login_for_service(
                "student",
                "password",
                "https://lexue.example/login/index.php",
            )


def test_url_crypto_response_decrypts_wrapped_json():
    key = b"0123456789abcdef"
    encrypted = _aes_encrypt(b'{"code":200,"data":{"tel":"opaque"}}', key)
    wrapped = json.dumps(__import__("base64").b64encode(encrypted).decode("ascii"))

    assert _decrypt_url_crypto_response(wrapped, key) == {
        "code": 200,
        "data": {"tel": "opaque"},
    }
