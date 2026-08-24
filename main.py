import asyncio
import re
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api import message_components as Comp
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

_PLUGIN_ROOT = Path(__file__).resolve().parent
_SRC_PATH = _PLUGIN_ROOT / "src"
if _SRC_PATH.exists() and str(_SRC_PATH) not in sys.path:
    sys.path.insert(0, str(_SRC_PATH))

from lexue_attention.astrbot_adapter import (
    format_event_list,
    format_sync_summary,
    is_same_minute,
    normalize_plugin_config,
    parse_hhmm,
    validate_fetch_config,
)
from lexue_attention.auth import AuthError, SmsCodeContext
from lexue_attention.core import create_calendar_subscription, fetch_events, sync_events
from lexue_attention.lexue import LexueCalendarAuthExpired
from lexue_attention.mail_code import MailCodeConfig, MailCodeError, wait_for_sso_email_code

PLUGIN_NAME = "astrbot_plugin_lexue_attention"
PLUGIN_AUTHOR = "lexue-attention"
PLUGIN_DESC = "BIT 乐学 DDL 查询、同步和定时提醒插件。"
PLUGIN_VERSION = "1.5.3"
IMAGE_RENDER_COOLDOWN_MINUTES = 30
CUSTOM_T2I_IMAGE_TTL_DAYS = 7
DEFAULT_T2I_ENDPOINT = "astrbot"
OFFICIAL_T2I_ENDPOINT = "https://t2i.soulter.top/text2img"
ASTRBOT_T2I_ENDPOINT = "astrbot"
DDL_CARD_RENDER_WIDTH = 760
DDL_CARD_BASE_CLIP_HEIGHT = 246
DDL_CARD_EVENT_CLIP_HEIGHT = 140
DDL_CARD_EMPTY_CLIP_HEIGHT = 150
DDL_CARD_HIDDEN_CLIP_HEIGHT = 58

DDL_CARD_TEMPLATE = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <style>
    * { box-sizing: border-box; }
    html {
      width: 760px;
      margin: 0;
      padding: 0;
      background: #f5f7fa;
    }
    body {
      width: 760px;
      margin: 0;
      padding: 24px;
      background: #f5f7fa;
      color: #172033;
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", Arial, sans-serif;
      letter-spacing: 0;
    }
    .panel {
      width: 100%;
      border: 1px solid #dfe5ee;
      border-radius: 8px;
      background: #ffffff;
      overflow: hidden;
    }
    .header {
      padding: 22px 24px 18px;
      border-bottom: 1px solid #e6ebf2;
    }
    .header-row {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 18px;
    }
    .title {
      margin: 0;
      font-size: 30px;
      line-height: 1.2;
      font-weight: 800;
    }
    .subtitle {
      margin-top: 8px;
      font-size: 14px;
      line-height: 1.45;
      color: #667085;
    }
    .total {
      min-width: 112px;
      padding: 10px 12px;
      border: 1px solid #d7dde7;
      border-radius: 8px;
      text-align: center;
      background: #f8fafc;
    }
    .total-number {
      display: block;
      font-size: 28px;
      line-height: 1;
      font-weight: 800;
      color: #172033;
    }
    .total-label {
      display: block;
      margin-top: 5px;
      font-size: 13px;
      color: #667085;
    }
    .metrics {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 18px;
    }
    .metric {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      min-height: 30px;
      padding: 5px 10px;
      border: 1px solid #d7dde7;
      border-radius: 6px;
      font-size: 13px;
      color: #475467;
      background: #ffffff;
    }
    .metric strong {
      font-size: 15px;
      color: #172033;
    }
    .metric.expired strong { color: #b42318; }
    .metric.critical strong { color: #c2410c; }
    .metric.today strong { color: #a16207; }
    .metric.soon strong { color: #175cd3; }
    .metric.later strong { color: #087443; }
    .content { padding: 16px; }
    .empty {
      padding: 34px 24px;
      border: 1px dashed #cbd5e1;
      border-radius: 8px;
      text-align: center;
      color: #667085;
      font-size: 18px;
      background: #f8fafc;
    }
    .event {
      display: grid;
      grid-template-columns: 1fr 172px;
      gap: 16px;
      min-height: 112px;
      padding: 16px 16px 16px 20px;
      border: 1px solid #e1e7ef;
      border-left-width: 6px;
      border-radius: 8px;
      background: #ffffff;
    }
    .event + .event { margin-top: 10px; }
    .event.expired { border-left-color: #dc2626; }
    .event.critical { border-left-color: #f97316; }
    .event.today { border-left-color: #eab308; }
    .event.soon { border-left-color: #2563eb; }
    .event.later { border-left-color: #16a34a; }
    .course {
      display: inline-flex;
      max-width: 100%;
      min-height: 24px;
      align-items: center;
      padding: 3px 8px;
      border-radius: 5px;
      background: #f1f5f9;
      color: #334155;
      font-size: 13px;
      line-height: 1.35;
      font-weight: 700;
      overflow-wrap: anywhere;
    }
    .event-title {
      margin-top: 9px;
      font-size: 22px;
      line-height: 1.28;
      font-weight: 800;
      color: #111827;
      overflow-wrap: anywhere;
    }
    .remaining {
      margin-top: 9px;
      font-size: 15px;
      line-height: 1.35;
      color: #475467;
    }
    .side {
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      justify-content: space-between;
      gap: 12px;
    }
    .status {
      min-width: 86px;
      padding: 6px 10px;
      border-radius: 6px;
      text-align: center;
      font-size: 14px;
      line-height: 1;
      font-weight: 800;
    }
    .status.expired { color: #b42318; background: #fee4e2; }
    .status.critical { color: #c2410c; background: #ffedd5; }
    .status.today { color: #a16207; background: #fef3c7; }
    .status.soon { color: #175cd3; background: #dbeafe; }
    .status.later { color: #087443; background: #dcfce7; }
    .due { text-align: right; }
    .due-date {
      font-size: 17px;
      line-height: 1.25;
      font-weight: 800;
      color: #172033;
    }
    .due-time {
      margin-top: 5px;
      font-size: 28px;
      line-height: 1;
      font-weight: 800;
      color: #172033;
    }
    .hidden {
      margin-top: 12px;
      padding: 10px 12px;
      border-radius: 6px;
      background: #f1f5f9;
      color: #475467;
      font-size: 14px;
      text-align: center;
    }
  </style>
</head>
<body>
  <section class="panel">
    <header class="header">
      <div class="header-row">
        <div>
          <h1 class="title">{{ title|e }}</h1>
          <div class="subtitle">{{ generated_at|e }} 更新{% if hidden_count > 0 %}，已显示 {{ shown_count }} / {{ total_count }} 个{% endif %}</div>
        </div>
        <div class="total">
          <span class="total-number">{{ total_count }}</span>
          <span class="total-label">DDL</span>
        </div>
      </div>
      <div class="metrics">
        {% for metric in metrics %}
        <span class="metric {{ metric.tone|e }}">{{ metric.label|e }} <strong>{{ metric.value }}</strong></span>
        {% endfor %}
      </div>
    </header>
    <main class="content">
      {% if events %}
        {% for item in events %}
        <article class="event {{ item.tone|e }}">
          <div>
            {% if item.course %}
            <div class="course">{{ item.course|e }}</div>
            {% endif %}
            <div class="event-title">{{ item.title|e }}</div>
            <div class="remaining">{{ item.remaining|e }}</div>
          </div>
          <div class="side">
            <div class="status {{ item.tone|e }}">{{ item.status_label|e }}</div>
            <div class="due">
              <div class="due-date">{{ item.due_date|e }} {{ item.weekday|e }}</div>
              <div class="due-time">{{ item.due_time|e }}</div>
            </div>
          </div>
        </article>
        {% endfor %}
        {% if hidden_count > 0 %}
        <div class="hidden">还有 {{ hidden_count }} 个 DDL 未显示，可调大 max_events。</div>
        {% endif %}
      {% else %}
        <div class="empty">暂无 DDL</div>
      {% endif %}
    </main>
  </section>
</body>
</html>
"""


@filter.command_group("lexue", alias={"乐学", "ddl"})
def lexue():
    pass


@register(PLUGIN_NAME, PLUGIN_AUTHOR, PLUGIN_DESC, PLUGIN_VERSION)
class LexueAttentionPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self._sync_task: asyncio.Task | None = None
        self._daily_task: asyncio.Task | None = None
        self._sync_lock = asyncio.Lock()
        self._login_lock = asyncio.Lock()
        self._pending_sms_code: asyncio.Future[str] | None = None
        self._pending_sms_origin = ""
        self._last_daily_key = ""
        self._last_error = ""
        self._image_render_disabled_until: datetime | None = None

    async def initialize(self) -> None:
        self._restart_background_tasks()

    async def terminate(self) -> None:
        if self._pending_sms_code is not None and not self._pending_sms_code.done():
            self._pending_sms_code.cancel()
        await self._cancel_background_tasks()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @lexue.command("help", alias={"帮助"})
    async def lexue_help(self, event: AstrMessageEvent):
        """查看乐学 DDL 插件帮助。"""
        yield event.plain_result(
            "lexue-attention 指令：\n"
            "/lexue bind 绑定当前会话用于主动推送\n"
            "/lexue account <账号> <密码> 设置 BIT 统一认证账号密码\n"
            "/lexue login 登录统一认证；配置邮箱后可自动取验证码并持久化乐学授权\n"
            "/lexue code <验证码> 提交登录邮箱验证码\n"
            "/lexue calendar <ics地址> 设置乐学日历订阅地址\n"
            "/lexue daily <HH:MM> 设置每日 DDL 推送时间\n"
            "/lexue interval <分钟> 设置自动同步间隔\n"
            "/lexue fetch 主动获取 DDL 列表\n"
            "/lexue sync 主动同步并发送新增、变更和提醒\n"
            "/lexue status 查看当前配置状态"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @lexue.command("bind", alias={"绑定"})
    async def bind_session(self, event: AstrMessageEvent):
        """绑定当前会话用于主动推送。"""
        self.config["push_session"] = event.unified_msg_origin
        self._save_config()
        yield event.plain_result("已绑定当前会话，后续定时 DDL 会推送到这里。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @lexue.command("account", alias={"账号"})
    async def set_account(self, event: AstrMessageEvent, username: str, password: str):
        """设置 BIT 统一认证账号和密码。"""
        self.config["username"] = username.strip()
        self.config["password"] = password
        self._save_config()
        yield event.plain_result("已保存统一认证账号和密码。请在插件配置页填写邮箱账号密码，再发送 /lexue login。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @lexue.command("login", alias={"登录", "授权"})
    async def login_lexue(self, event: AstrMessageEvent):
        """登录统一认证并持久化乐学日历授权。"""
        config = self._plugin_config()
        if not config.username or not config.password:
            yield event.plain_result("请先使用 /lexue account <账号> <密码> 保存统一认证账号密码。")
            return
        if self._login_lock.locked():
            yield event.plain_result("已有乐学登录正在进行；如已收到邮件，请使用 /lexue code <验证码>。")
            return

        login_error = ""
        calendar_url = ""
        async with self._login_lock:
            try:
                calendar_url = await create_calendar_subscription(
                    config.username,
                    config.password,
                    config.lexue_base_url,
                    sms_code_callback=self._verification_code_callback(config, event),
                )
            except Exception as exc:
                login_error = _format_error(exc)
                self._last_error = login_error
                logger.error("lexue-attention interactive login failed: %s", login_error)

        if login_error:
            yield event.plain_result(f"乐学授权失败：{login_error}")
            return

        self.config["calendar_url"] = calendar_url
        self.config["auth_method"] = "android"
        self._save_config()
        self._last_error = ""
        yield event.plain_result(
            "乐学授权成功，已持久化日历订阅权限。"
            "统一认证密码将保留，用于订阅失效后自动重新授权。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @lexue.command("code", alias={"验证码"})
    async def submit_sms_code(self, event: AstrMessageEvent, code: str):
        """提交统一认证邮箱验证码。"""
        value = code.strip()
        if not re.fullmatch(r"\d{4,8}", value):
            yield event.plain_result("验证码应为 4-8 位数字。")
            return

        pending = self._pending_sms_code
        if pending is None or pending.done():
            yield event.plain_result("当前没有等待验证码的乐学登录，请先发送 /lexue login。")
            return
        if str(event.unified_msg_origin) != self._pending_sms_origin:
            yield event.plain_result("请在发起 /lexue login 的同一会话中提交验证码。")
            return

        pending.set_result(value)
        yield event.plain_result("已接收验证码，正在完成乐学授权。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @lexue.command("calendar", alias={"日历"})
    async def set_calendar(self, event: AstrMessageEvent, calendar_url: GreedyStr):
        """设置乐学 iCalendar 订阅地址。"""
        self.config["calendar_url"] = str(calendar_url).strip()
        self._save_config()
        yield event.plain_result("已保存乐学日历订阅地址。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @lexue.command("daily", alias={"每日"})
    async def set_daily_time(self, event: AstrMessageEvent, hhmm: str):
        """设置每日 DDL 推送时间，例如 /lexue daily 08:30。"""
        try:
            parse_hhmm(hhmm)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        self.config["daily_push_time"] = hhmm.strip()
        self.config["enable_daily_push"] = True
        self._save_config()
        self._restart_background_tasks()
        yield event.plain_result(f"已设置每日 DDL 推送时间为 {hhmm.strip()}。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @lexue.command("interval", alias={"间隔"})
    async def set_interval(self, event: AstrMessageEvent, minutes: int):
        """设置自动同步间隔分钟数，最小 5 分钟。"""
        if minutes < 5:
            yield event.plain_result("自动同步间隔不能小于 5 分钟。")
            return

        self.config["check_interval_minutes"] = minutes
        self.config["enable_interval_sync"] = True
        self._save_config()
        self._restart_background_tasks()
        yield event.plain_result(f"已设置自动同步间隔为 {minutes} 分钟。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @lexue.command("fetch", alias={"list", "列表", "查看"})
    async def fetch_ddl(self, event: AstrMessageEvent):
        """主动获取并发送当前 DDL 列表，不更新提醒状态。"""
        config = self._plugin_config()
        try:
            validate_fetch_config(config)
            config, events = await self._fetch_events_with_reauth(config, event)
        except Exception as exc:
            logger.error("lexue-attention fetch failed: %s", _format_error(exc))
            yield event.plain_result(f"获取 DDL 失败：{_format_error(exc)}")
            return

        now = datetime.now(config.timezone)
        text = format_event_list(events, now, title="当前 DDL", limit=config.max_events)
        image_url = await self._render_event_image(
            events,
            now,
            title="当前 DDL",
            limit=config.max_events,
            enabled=getattr(config, "enable_image_mode", True),
        )
        if image_url:
            yield event.image_result(image_url)
            return
        yield event.plain_result(text)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @lexue.command("sync", alias={"同步", "更新"})
    async def sync_ddl(self, event: AstrMessageEvent):
        """主动同步 DDL，更新本地状态并发送新增、变更和提醒。"""
        try:
            config, now, result = await self._run_sync(event)
        except Exception as exc:
            logger.error("lexue-attention manual sync failed: %s", _format_error(exc))
            yield event.plain_result(f"同步 DDL 失败：{_format_error(exc)}")
            return

        text = format_sync_summary(result, now, max_events=config.max_events)
        image_url = await self._render_event_image(
            result.events,
            now,
            title="同步完成",
            limit=config.max_events,
            enabled=getattr(config, "enable_image_mode", True),
        )
        if image_url:
            yield event.image_result(image_url)
            return
        yield event.plain_result(text)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @lexue.command("status", alias={"状态"})
    async def status(self, event: AstrMessageEvent):
        """查看插件配置状态。"""
        config = self._plugin_config()
        push_session = self._push_session()
        lines = [
            "lexue-attention 状态",
            f"账号：{'已设置' if config.username else '未设置'}",
            f"密码：{'已设置' if config.password else '未设置'}",
            f"邮箱账号：{'已设置' if config.mail_username else '未设置'}",
            f"邮箱密码：{'已设置' if config.mail_password else '未设置'}",
            f"邮箱自动取码：{'开启' if config.enable_mail_auto_code else '关闭'}",
            f"日历订阅：{'已设置' if config.calendar_url else '未设置'}",
            f"持久授权：{'已建立' if config.calendar_url else '未建立'}",
            f"主动推送会话：{'已绑定' if push_session else '未绑定'}",
            f"每日推送：{'开启' if config.enable_daily_push else '关闭'} {config.daily_push_time}",
            f"自动同步：{'开启' if config.enable_interval_sync else '关闭'} {config.check_interval_minutes} 分钟",
            f"图片卡片：{'开启' if config.enable_image_mode else '关闭'}",
            f"文转图服务器：{_format_t2i_endpoint(_config_t2i_endpoint(config))}",
            f"图片渲染冷却：{self._format_image_cooldown()}",
            f"最近错误：{self._last_error or '无'}",
        ]
        yield event.plain_result("\n".join(lines))

    async def _wait_for_sms_code(
        self,
        event: AstrMessageEvent,
        context: SmsCodeContext,
        *,
        announce: bool = True,
    ) -> str:
        pending = asyncio.get_running_loop().create_future()
        self._pending_sms_code = pending
        self._pending_sms_origin = str(event.unified_msg_origin)
        if announce:
            await event.send(
                event.plain_result(
                    f"统一身份认证邮箱验证码已发送至 {context.masked_phone}。"
                    "请在 3 分钟内发送 /lexue code <验证码>。"
                )
            )
        try:
            return await asyncio.wait_for(pending, timeout=180)
        except TimeoutError as exc:
            raise AuthError("等待邮箱验证码超时，请重新发送 /lexue login") from exc
        finally:
            if self._pending_sms_code is pending:
                self._pending_sms_code = None
                self._pending_sms_origin = ""

    def _verification_code_callback(self, config, event: AstrMessageEvent | None = None):
        async def callback(context: SmsCodeContext) -> str:
            if config.enable_mail_auto_code and config.mail_username and config.mail_password:
                if event is not None:
                    await event.send(event.plain_result("验证码邮件已发送，正在从校内邮箱自动读取。"))
                try:
                    code = await wait_for_sso_email_code(
                        MailCodeConfig(
                            username=config.mail_username,
                            password=config.mail_password,
                        ),
                        requested_at=context.requested_at or datetime.now().timestamp(),
                    )
                except MailCodeError as exc:
                    if event is None:
                        raise AuthError(f"邮箱自动取码失败：{exc}") from exc
                    await event.send(
                        event.plain_result(
                            f"邮箱自动取码失败：{exc}。请在 3 分钟内发送 /lexue code <验证码>。"
                        )
                    )
                    return await self._wait_for_sms_code(event, context, announce=False)
                if event is not None:
                    await event.send(event.plain_result("已自动获取邮箱验证码，正在完成乐学授权。"))
                return code

            if event is not None:
                return await self._wait_for_sms_code(event, context)
            raise AuthError("未配置邮箱自动取码，后台任务无法完成二次验证")

        return callback

    async def _refresh_calendar_subscription(self, config, event: AstrMessageEvent | None = None):
        if not config.username or not config.password:
            raise AuthError("乐学日历授权已失效，且未保存统一认证账号或密码")

        async with self._login_lock:
            current = self._plugin_config()
            if current.calendar_url and current.calendar_url != config.calendar_url:
                return current
            if event is not None:
                await event.send(event.plain_result("乐学日历授权已失效，正在自动重新授权。"))
            calendar_url = await create_calendar_subscription(
                current.username,
                current.password,
                current.lexue_base_url,
                sms_code_callback=self._verification_code_callback(current, event),
            )
            self.config["calendar_url"] = calendar_url
            self.config["auth_method"] = "android"
            self._save_config()
            return self._plugin_config()

    async def _fetch_events_with_reauth(self, config, event: AstrMessageEvent | None = None):
        callback = self._verification_code_callback(config, event)
        try:
            return config, await fetch_events(config.fetch_options(callback))
        except LexueCalendarAuthExpired:
            refreshed = await self._refresh_calendar_subscription(config, event)
            return refreshed, await fetch_events(
                refreshed.fetch_options(self._verification_code_callback(refreshed, event))
            )

    async def _run_sync(self, event: AstrMessageEvent | None = None):
        config = self._plugin_config()
        validate_fetch_config(config)
        async with self._sync_lock:
            now = datetime.now(config.timezone)
            callback = self._verification_code_callback(config, event)
            try:
                result = await sync_events(
                    config.fetch_options(callback),
                    config.state_path,
                    now,
                    config.reminder_milestones_hours,
                )
            except LexueCalendarAuthExpired:
                config = await self._refresh_calendar_subscription(config, event)
                result = await sync_events(
                    config.fetch_options(self._verification_code_callback(config, event)),
                    config.state_path,
                    now,
                    config.reminder_milestones_hours,
                )

        self._last_error = ""
        return config, now, result

    async def _render_event_image(
        self,
        events,
        now: datetime,
        *,
        title: str,
        limit: int,
        enabled: bool,
    ) -> str:
        if not enabled:
            return ""
        if self._image_render_disabled_until and datetime.now(now.tzinfo) < self._image_render_disabled_until:
            return ""

        try:
            context = build_ddl_card_context(events, now, title=title, limit=limit)
            config = self._plugin_config()
            image_url = await self._render_html_to_image(DDL_CARD_TEMPLATE, context, _config_t2i_endpoint(config))
            self._image_render_disabled_until = None
            return image_url
        except Exception as exc:
            self._image_render_disabled_until = datetime.now(now.tzinfo) + timedelta(
                minutes=IMAGE_RENDER_COOLDOWN_MINUTES
            )
            self._last_error = _format_error(exc)
            logger.warning(
                "lexue-attention render ddl image failed, fallback to text for %s minutes: %s",
                IMAGE_RENDER_COOLDOWN_MINUTES,
                _format_error(exc),
            )
            return ""

    async def _render_html_to_image(self, template: str, context: dict[str, Any], t2i_endpoint: str) -> str:
        endpoint = _normalize_t2i_endpoint(t2i_endpoint)
        options = _render_options(context)
        if endpoint == ASTRBOT_T2I_ENDPOINT:
            return await self.html_render(template, context, options=options)
        return await self._render_with_custom_t2i(endpoint, template, context, options)

    async def _render_with_custom_t2i(
        self,
        endpoint: str,
        template: str,
        context: dict[str, Any],
        options: dict[str, Any],
    ) -> str:
        base_url = endpoint.rstrip("/")
        payload = {
            "tmpl": template,
            "tmpldata": context,
            "json": False,
            "options": options,
        }
        render_url = f"{base_url}/generate" if base_url.endswith("/text2img") else f"{base_url}/text2img/generate"
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.post(render_url, json=payload)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "").lower()
            if "application/json" in content_type:
                data = response.json()
                image_url = _image_url_from_t2i_json(base_url, data)
                if image_url:
                    return await self._download_custom_t2i_image(client, image_url)
                raise RuntimeError(f"自定义文转图服务返回异常：{data}")

            if not content_type.startswith("image/"):
                raise RuntimeError(f"自定义文转图服务返回了非图片内容：{content_type or 'unknown'}")

            suffix = ".jpg" if "jpeg" in content_type or "jpg" in content_type else ".png"
            output_path = self._custom_t2i_image_dir() / f"ddl_card_{uuid.uuid4().hex}{suffix}"
            await asyncio.to_thread(output_path.write_bytes, response.content)
            return str(output_path)

    async def _download_custom_t2i_image(self, client: httpx.AsyncClient, image_url: str) -> str:
        response = await client.get(image_url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        suffix = ".jpg" if "jpeg" in content_type or "jpg" in content_type else ".png"
        output_path = self._custom_t2i_image_dir() / f"ddl_card_{uuid.uuid4().hex}{suffix}"
        await asyncio.to_thread(output_path.write_bytes, response.content)
        return str(output_path)

    def _custom_t2i_image_dir(self) -> Path:
        output_dir = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME / "rendered_images"
        output_dir.mkdir(parents=True, exist_ok=True)
        cutoff = datetime.now().timestamp() - CUSTOM_T2I_IMAGE_TTL_DAYS * 24 * 60 * 60
        for old_file in output_dir.glob("ddl_card_*"):
            try:
                if old_file.is_file() and old_file.stat().st_mtime < cutoff:
                    old_file.unlink()
            except OSError:
                logger.warning("lexue-attention cannot remove old rendered image %s", old_file)
        return output_dir

    async def _send_to_bound_session(self, text: str = "", image_url: str = "") -> None:
        session = self._push_session()
        if not session:
            return
        if not text and not image_url:
            return

        chain = MessageChain()
        if image_url:
            if image_url.startswith("http"):
                chain.chain.append(Comp.Image.fromURL(image_url))
            else:
                chain.chain.append(Comp.Image.fromFileSystem(image_url))
        if text:
            chain.message(text)
        ok = await self.context.send_message(session, chain)
        if not ok:
            logger.warning("lexue-attention cannot find platform for session %s", session)

    async def _send_event_list_to_bound_session(
        self,
        events,
        now: datetime,
        *,
        title: str,
        limit: int,
        enable_image_mode: bool,
    ) -> None:
        text = format_event_list(events, now, title=title, limit=limit)
        image_url = await self._render_event_image(
            events,
            now,
            title=title,
            limit=limit,
            enabled=enable_image_mode,
        )
        if image_url:
            await self._send_to_bound_session(image_url=image_url)
            return
        await self._send_to_bound_session(text=text)

    async def _send_sync_notifications_to_bound_session(self) -> None:
        config, now, result = await self._run_sync()
        sent = False
        for title, events in (
            ("新增 DDL", result.new_events),
            ("变更 DDL", result.changed_events),
        ):
            if not events:
                continue
            await self._send_event_list_to_bound_session(
                events,
                now,
                title=title,
                limit=config.max_events,
                enable_image_mode=getattr(config, "enable_image_mode", True),
            )
            sent = True

        for reminder in result.reminders:
            image_url = await self._render_event_image(
                [reminder.event],
                now,
                title="DDL 提醒",
                limit=1,
                enabled=getattr(config, "enable_image_mode", True),
            )
            if image_url:
                await self._send_to_bound_session(image_url=image_url)
            else:
                await self._send_to_bound_session(text="DDL 提醒\n" + reminder.text)
            sent = True

        if not sent:
            return

    async def _interval_loop(self) -> None:
        while True:
            config = self._plugin_config()
            await asyncio.sleep(config.check_interval_minutes * 60)
            session = self._push_session()
            if not session or not config.enable_interval_sync:
                continue
            try:
                await self._send_sync_notifications_to_bound_session()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = _format_error(exc)
                logger.error("lexue-attention interval sync failed: %s", _format_error(exc))

    async def _daily_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            config = self._plugin_config()
            session = self._push_session()
            if not session or not config.enable_daily_push:
                continue

            now = datetime.now(config.timezone)
            current_key = now.strftime("%Y-%m-%d %H:%M")
            if current_key == self._last_daily_key:
                continue
            try:
                if not is_same_minute(now, config.daily_push_time):
                    continue
                self._last_daily_key = current_key
                config, events = await self._fetch_events_with_reauth(config)
                await self._send_event_list_to_bound_session(
                    events,
                    now,
                    title="今日 DDL 推送",
                    limit=config.max_events,
                    enable_image_mode=getattr(config, "enable_image_mode", True),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = _format_error(exc)
                logger.error("lexue-attention daily push failed: %s", _format_error(exc))

    def _restart_background_tasks(self) -> None:
        for task in (self._sync_task, self._daily_task):
            if task and not task.done():
                task.cancel()
        self._sync_task = asyncio.create_task(self._interval_loop())
        self._daily_task = asyncio.create_task(self._daily_loop())

    async def _cancel_background_tasks(self) -> None:
        tasks = [task for task in (self._sync_task, self._daily_task) if task and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _plugin_config(self):
        data_dir = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        data_dir.mkdir(parents=True, exist_ok=True)
        return normalize_plugin_config(self.config, data_dir / "state.json")

    def _push_session(self) -> str:
        return str(_config_get(self.config, "push_session", "") or "").strip()

    def _save_config(self) -> None:
        save_config = getattr(self.config, "save_config", None)
        if callable(save_config):
            save_config()

    def _format_image_cooldown(self) -> str:
        if not self._image_render_disabled_until:
            return "无"
        remaining = self._image_render_disabled_until - datetime.now(self._image_render_disabled_until.tzinfo)
        minutes = int(remaining.total_seconds() // 60)
        if minutes < 0:
            return "无"
        return f"约 {minutes + 1} 分钟"


def _config_get(config: Any, key: str, default: Any = None) -> Any:
    if hasattr(config, "get"):
        return config.get(key, default)
    return default


def _config_t2i_endpoint(config: Any) -> str:
    return str(getattr(config, "t2i_endpoint", DEFAULT_T2I_ENDPOINT) or DEFAULT_T2I_ENDPOINT)


def _render_options(context: dict[str, Any]) -> dict[str, Any]:
    events = context.get("events") or []
    shown_count = _safe_int(context.get("shown_count"), len(events))
    hidden_count = _safe_int(context.get("hidden_count"), 0)
    content_height = DDL_CARD_EMPTY_CLIP_HEIGHT if shown_count <= 0 else shown_count * DDL_CARD_EVENT_CLIP_HEIGHT
    if hidden_count > 0:
        content_height += DDL_CARD_HIDDEN_CLIP_HEIGHT
    return {
        "type": "png",
        "timeout": 10000,
        "clip": {
            "x": 0,
            "y": 0,
            "width": DDL_CARD_RENDER_WIDTH,
            "height": DDL_CARD_BASE_CLIP_HEIGHT + content_height,
        },
    }


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_t2i_endpoint(value: str) -> str:
    endpoint = str(value or "").strip()
    lowered = endpoint.lower()
    if not endpoint or lowered in {"default", "astrbot", "builtin", "internal"}:
        return ASTRBOT_T2I_ENDPOINT
    if lowered == "official":
        return OFFICIAL_T2I_ENDPOINT
    return endpoint


def _format_t2i_endpoint(value: str) -> str:
    endpoint = _normalize_t2i_endpoint(value)
    if endpoint == OFFICIAL_T2I_ENDPOINT:
        return "官方服务器"
    if endpoint == ASTRBOT_T2I_ENDPOINT:
        return "AstrBot 全局配置"
    return endpoint


def _image_url_from_t2i_json(base_url: str, data: Any) -> str:
    if not isinstance(data, dict):
        return ""

    payload = data.get("data", {})
    candidates: list[Any] = []
    if isinstance(payload, dict):
        candidates.extend([payload.get("url"), payload.get("image"), payload.get("id"), payload.get("path")])
    candidates.extend([data.get("url"), data.get("image"), data.get("id"), data.get("path")])

    for value in candidates:
        if not isinstance(value, str) or not value.strip():
            continue
        path = value.strip().replace("\\", "/")
        if path.startswith(("http://", "https://")):
            return path
        if path.startswith("data/"):
            return f"{base_url}/{path}" if base_url.endswith("/text2img") else f"{base_url}/text2img/{path}"
        return f"{base_url}/data/{path.lstrip('/')}" if base_url.endswith("/text2img") else f"{base_url}/text2img/data/{path.lstrip('/')}"
    return ""


def build_ddl_card_context(events, now: datetime, *, title: str, limit: int) -> dict[str, Any]:
    sorted_events = sorted(events, key=lambda item: item.due_at)
    visible_events = sorted_events[:limit]
    cards = [_event_card(event, now) for event in visible_events]
    counts = _status_counts(sorted_events, now)
    return {
        "title": title,
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "total_count": len(sorted_events),
        "shown_count": len(visible_events),
        "hidden_count": max(0, len(sorted_events) - len(visible_events)),
        "events": cards,
        "metrics": [
            {"label": "已过期", "value": counts["expired"], "tone": "expired"},
            {"label": "马上截止", "value": counts["critical"], "tone": "critical"},
            {"label": "今日截止", "value": counts["today"], "tone": "today"},
            {"label": "3 天内", "value": counts["soon"], "tone": "soon"},
            {"label": "待办", "value": counts["later"], "tone": "later"},
        ],
    }


def _event_card(event, now: datetime) -> dict[str, str]:
    due_at = _to_timezone(event.due_at, now)
    status = _event_status(due_at, now)
    return {
        "title": _clean_title(event.title),
        "course": _clean_course(event.course),
        "due_date": due_at.strftime("%m 月 %d 日"),
        "due_time": due_at.strftime("%H:%M"),
        "weekday": _weekday(due_at),
        "remaining": _format_compact_remaining(due_at, now),
        "status_label": status["label"],
        "tone": status["tone"],
    }


def _event_status(due_at: datetime, now: datetime) -> dict[str, str]:
    total_minutes = int((due_at - now).total_seconds() // 60)
    if total_minutes < 0:
        return {"tone": "expired", "label": "已过期"}
    if total_minutes <= 6 * 60:
        return {"tone": "critical", "label": "马上截止"}
    if due_at.date() == now.date() or total_minutes <= 24 * 60:
        return {"tone": "today", "label": "今日截止"}
    if total_minutes <= 72 * 60:
        return {"tone": "soon", "label": "3 天内"}
    return {"tone": "later", "label": "待办"}


def _status_counts(events, now: datetime) -> dict[str, int]:
    counts = {"expired": 0, "critical": 0, "today": 0, "soon": 0, "later": 0}
    for event in events:
        tone = _event_status(_to_timezone(event.due_at, now), now)["tone"]
        counts[tone] += 1
    return counts


def _format_compact_remaining(due_at: datetime, now: datetime) -> str:
    delta = due_at - now
    total_minutes = int(delta.total_seconds() // 60)
    prefix = "剩余"
    if total_minutes < 0:
        prefix = "已过期"
        total_minutes = abs(total_minutes)

    days, rem = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem, 60)
    if days:
        return f"{prefix} {days} 天 {hours} 小时"
    if hours:
        return f"{prefix} {hours} 小时 {minutes} 分钟"
    return f"{prefix} {minutes} 分钟"


def _clean_title(value: str) -> str:
    text = value.strip()
    text = re.sub(r"[（(]\s*截止时间[^）)]*[）)]", "", text)
    text = re.sub(r"^请在此提交[:：]?", "", text)
    text = re.sub(r"\s*(已到期|已截止|已过期)\s*$", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -:：") or value.strip()


def _clean_course(value: str) -> str:
    course = value.strip()
    if not course:
        return ""
    course = re.sub(r"^\d{4}-\d{4}[-\s]*第?\d学期[-\s-]*", "", course)
    course = re.sub(r"^\d{4}-\d{4}\s*第[一二三四五六七八九十\d]+学期\s*", "", course)
    course = re.sub(r"[_-]\d+$", "", course)
    parts = [part.strip() for part in re.split(r"--+| - ", course) if part.strip()]
    if len(parts) > 1 and parts[-1].endswith("老师"):
        course = " ".join(parts[:-1])
    course = re.sub(r"\s+", " ", course)
    return course.strip(" -_") or value.strip()


def _weekday(value: datetime) -> str:
    names = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    return names[value.weekday()]


def _to_timezone(value: datetime, now: datetime) -> datetime:
    tz = now.tzinfo
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _format_error(exc: Exception) -> str:
    text = str(exc)
    if "All endpoints failed" in text and "HTTP 502" in text:
        return (
            "AstrBot HTML 转图服务当前不可用（HTTP 502）。"
            "插件已回退纯文本；可稍后重试或在 AstrBot 中配置可用的 t2i 服务。"
        )
    if isinstance(exc, httpx.ConnectError) and (
        "name or service not known" in text.lower()
        or "name resolution" in text.lower()
        or "getaddrinfo failed" in text.lower()
    ):
        return (
            "AstrBot 运行环境无法解析乐学或统一认证域名。"
            "请在部署 AstrBot 的机器/容器里检查 DNS、网络、代理和校园网访问。"
        )
    if isinstance(exc, httpx.HTTPStatusError):
        return f"远程服务返回 HTTP {exc.response.status_code}。"
    if isinstance(exc, httpx.RequestError):
        return f"网络请求失败：{type(exc).__name__}。"
    return text
