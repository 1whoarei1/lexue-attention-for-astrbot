import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import httpx
import pytest


def _load_plugin_main():
    if "astrbot.api" not in sys.modules:
        astrbot = types.ModuleType("astrbot")
        api = types.ModuleType("astrbot.api")
        event = types.ModuleType("astrbot.api.event")
        star = types.ModuleType("astrbot.api.star")
        components = types.ModuleType("astrbot.api.message_components")
        command = types.ModuleType("astrbot.core.star.filter.command")
        path_mod = types.ModuleType("astrbot.core.utils.astrbot_path")

        api.AstrBotConfig = dict
        api.logger = types.SimpleNamespace(
            warning=lambda *a, **k: None,
            error=lambda *a, **k: None,
            exception=lambda *a, **k: None,
        )
        api.message_components = components
        event.AstrMessageEvent = object
        event.MessageChain = object
        star.Context = object
        star.Star = object
        star.register = lambda *a, **k: (lambda cls: cls)
        command.GreedyStr = str
        path_mod.get_astrbot_data_path = lambda: "."

        class _Filter:
            class PermissionType:
                ADMIN = "admin"

            @staticmethod
            def command_group(*args, **kwargs):
                class _Group:
                    def __call__(self, func):
                        return self

                    def command(self, *args, **kwargs):
                        return lambda func: func

                return _Group()

            @staticmethod
            def permission_type(*args, **kwargs):
                return lambda func: func

        api.event = event
        api.star = star
        api.filter = _Filter
        event.filter = _Filter

        sys.modules["astrbot"] = astrbot
        sys.modules["astrbot.api"] = api
        sys.modules["astrbot.api.event"] = event
        sys.modules["astrbot.api.star"] = star
        sys.modules["astrbot.api.message_components"] = components
        sys.modules["astrbot.core.star.filter.command"] = command
        sys.modules["astrbot.core.utils.astrbot_path"] = path_mod

    module_path = Path(__file__).resolve().parents[1] / "main.py"
    spec = importlib.util.spec_from_file_location("lexue_plugin_main_for_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_normalize_t2i_endpoint_defaults_to_astrbot():
    module = _load_plugin_main()

    assert module._normalize_t2i_endpoint("") == "astrbot"
    assert module._normalize_t2i_endpoint("default") == "astrbot"
    assert module._normalize_t2i_endpoint("astrbot") == "astrbot"
    assert module._normalize_t2i_endpoint("official") == "https://t2i.soulter.top/text2img"
    assert module._normalize_t2i_endpoint("http://127.0.0.1:8999/") == "http://127.0.0.1:8999/"


def test_config_t2i_endpoint_handles_old_config_without_field():
    module = _load_plugin_main()

    old_config = types.SimpleNamespace(enable_image_mode=True)

    assert module._config_t2i_endpoint(old_config) == "astrbot"


def test_render_options_clip_to_card_width():
    module = _load_plugin_main()

    options = module._render_options({"shown_count": 7, "hidden_count": 0, "events": [{}] * 7})

    assert "full_page" not in options
    assert options["clip"]["x"] == 0
    assert options["clip"]["y"] == 0
    assert options["clip"]["width"] == 760
    assert options["clip"]["height"] >= 1100


def test_image_url_from_t2i_json_accepts_relative_data_path():
    module = _load_plugin_main()

    url = module._image_url_from_t2i_json(
        "http://127.0.0.1:8999",
        {"code": 0, "message": "success", "data": {"id": "data/rendered.png"}},
    )

    assert url == "http://127.0.0.1:8999/text2img/data/rendered.png"


def test_image_url_from_t2i_json_accepts_text2img_base_url():
    module = _load_plugin_main()

    url = module._image_url_from_t2i_json(
        "http://127.0.0.1:8999/text2img",
        {"data": {"id": "data/rendered.png"}},
    )

    assert url == "http://127.0.0.1:8999/text2img/data/rendered.png"


def test_format_error_does_not_echo_secret_request_url():
    module = _load_plugin_main()
    request = httpx.Request("GET", "https://lexue.example/calendar.ics?token=secret")
    error = httpx.ConnectError("getaddrinfo failed", request=request)

    message = module._format_error(error)

    assert "secret" not in message
    assert "token=" not in message


@pytest.mark.asyncio
async def test_successful_interactive_login_persists_calendar_and_clears_password(monkeypatch):
    module = _load_plugin_main()
    plugin = module.LexueAttentionPlugin.__new__(module.LexueAttentionPlugin)
    plugin.config = {"password": "private-password"}
    plugin._last_error = "old error"
    plugin._login_lock = asyncio.Lock()
    plugin._plugin_config = lambda: types.SimpleNamespace(
        username="student",
        password="private-password",
        lexue_base_url="https://lexue.example",
    )
    saved: list[bool] = []
    plugin._save_config = lambda: saved.append(True)

    async def fake_subscription(*args, **kwargs):
        return "https://lexue.example/calendar.ics?token=durable"

    monkeypatch.setattr(module, "create_calendar_subscription", fake_subscription)
    event = types.SimpleNamespace(plain_result=lambda text: text)

    results = [item async for item in plugin.login_lexue(event)]

    assert plugin.config["calendar_url"].endswith("token=durable")
    assert plugin.config["password"] == ""
    assert saved == [True]
    assert "清除已保存密码" in results[0]


@pytest.mark.asyncio
async def test_sms_code_command_completes_pending_login_without_echoing_code():
    module = _load_plugin_main()
    plugin = module.LexueAttentionPlugin.__new__(module.LexueAttentionPlugin)
    plugin._pending_sms_code = None
    plugin._pending_sms_origin = ""

    class LoginEvent:
        unified_msg_origin = "qq:private:student"

        def __init__(self):
            self.sent: list[str] = []

        def plain_result(self, text):
            return text

        async def send(self, result):
            self.sent.append(result)

    login_event = LoginEvent()
    wait_task = asyncio.create_task(
        plugin._wait_for_sms_code(
            login_event,
            module.SmsCodeContext(masked_phone="138****8000"),
        )
    )
    await asyncio.sleep(0)

    code_event = types.SimpleNamespace(
        unified_msg_origin="qq:private:student",
        plain_result=lambda text: text,
    )
    results = [item async for item in plugin.submit_sms_code(code_event, "123456")]

    assert await wait_task == "123456"
    assert "/lexue code <验证码>" in login_event.sent[0]
    assert results == ["已接收验证码，正在完成乐学授权。"]
    assert "123456" not in results[0]
    assert plugin._pending_sms_code is None


@pytest.mark.asyncio
async def test_sms_code_command_requires_pending_login_in_same_session():
    module = _load_plugin_main()
    plugin = module.LexueAttentionPlugin.__new__(module.LexueAttentionPlugin)
    plugin._pending_sms_code = asyncio.get_running_loop().create_future()
    plugin._pending_sms_origin = "qq:private:owner"

    wrong_event = types.SimpleNamespace(
        unified_msg_origin="qq:group:other",
        plain_result=lambda text: text,
    )
    results = [item async for item in plugin.submit_sms_code(wrong_event, "123456")]

    assert results == ["请在发起 /lexue login 的同一会话中提交验证码。"]
    assert not plugin._pending_sms_code.done()
    plugin._pending_sms_code.cancel()
