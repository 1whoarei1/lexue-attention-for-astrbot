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
<form id="secondMailLoginForm" action="/cas/login">
  <span id="login-page-flowkey">flow-2</span>
  <span id="user-object-id">user-object</span>
  <span id="second-auth-user-id">student-id</span>
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
async def test_v4_second_factor_submits_email_code():
    requests: list[httpx.Request] = []
    login_posts = 0

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
            assert "username=student-id" in form
            assert "type=mailLogin" in form
            assert "password=123456" in form
            return httpx.Response(
                302,
                headers={"Location": "https://lexue.example/login/index.php?ticket=ST-MAIL"},
            )
        if request.url.path.endswith("/findMail"):
            assert json.loads(request.content) == {"userId": "user-object"}
            return httpx.Response(200, json={"code": 200, "data": "student@bit.edu.cn"})
        if request.url.path.endswith("/sendMailCode4SecondAuth"):
            assert json.loads(request.content) == {
                "type": "DEFAULT",
                "mbemail": "student@bit.edu.cn",
                "businessNo": "2025031701",
            }
            return httpx.Response(200, json={"code": 200, "data": {"result": True}})
        if request.url.path.endswith("/checkTokenResult"):
            assert json.loads(request.content) == {
                "email": "student@bit.edu.cn",
                "token": "123456",
                "deleteFlag": False,
            }
            return httpx.Response(200, json={"code": 200})
        return httpx.Response(200, text='<script>var M = {"sesskey":"ok"};</script>')

    seen_contexts: list[tuple[str, str]] = []

    async def sms_callback(context):
        seen_contexts.append((context.channel, context.masked_phone))
        return "123456"

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as session:
        final_url = await BitSsoV4Client(session, "https://sso.example").login_for_service(
            "student",
            "password",
            "https://lexue.example/login/index.php",
            sms_code_callback=sms_callback,
        )

    assert final_url.endswith("ticket=ST-MAIL")
    assert seen_contexts == [("email", "stud****@bit.edu.cn")]
    assert any(request.url.path.endswith("/sendMailCode4SecondAuth") for request in requests)


@pytest.mark.asyncio
async def test_v4_second_factor_uses_page_email_when_lookup_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/findMail")
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as session:
        client = BitSsoV4Client(session, "https://sso.example")
        email = await client._second_factor_email(
            {"email": " student@bit.edu.cn ", "user_object_id": "user-object"}
        )

    assert email == "student@bit.edu.cn"


@pytest.mark.asyncio
async def test_v4_second_factor_without_callback_does_not_send_email_code():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/cas/login":
            return httpx.Response(200, text=LOGIN_HTML)
        if "findCaptchaCount" in request.url.path:
            return httpx.Response(200, json={"code": 200, "data": {"captchaInvisible": False}})
        if request.method == "POST" and request.url.path == "/cas/login":
            return httpx.Response(200, text=SECOND_FACTOR_HTML)
        if request.url.path.endswith("/findMail"):
            return httpx.Response(200, json={"code": 200, "data": "student@bit.edu.cn"})
        raise AssertionError("email code must not be sent without an interactive callback")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as session:
        with pytest.raises(SmsVerificationRequired, match="/lexue login"):
            await BitSsoV4Client(session, "https://sso.example").login_for_service(
                "student",
                "password",
                "https://lexue.example/login/index.php",
            )

    assert not any(request.url.path.endswith("/sendMailCode4SecondAuth") for request in requests)


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
