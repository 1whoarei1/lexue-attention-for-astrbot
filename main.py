import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
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
from lexue_attention.core import fetch_events, sync_events

PLUGIN_NAME = "astrbot_plugin_lexue_attention"
PLUGIN_AUTHOR = "lexue-attention"
PLUGIN_DESC = "BIT 乐学 DDL 查询、同步和定时提醒插件。"
PLUGIN_VERSION = "1.3.0"


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
        self._last_daily_key = ""
        self._last_error = ""

    async def initialize(self) -> None:
        self._restart_background_tasks()

    async def terminate(self) -> None:
        await self._cancel_background_tasks()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @lexue.command("help", alias={"帮助"})
    async def lexue_help(self, event: AstrMessageEvent):
        """查看乐学 DDL 插件帮助。"""
        yield event.plain_result(
            "lexue-attention 指令：\n"
            "/lexue bind 绑定当前会话用于主动推送\n"
            "/lexue account <账号> <密码> 设置 BIT 统一认证账号密码\n"
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
        yield event.plain_result("已保存账号和密码。建议优先使用 calendar_url，减少保存统一认证密码。")

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
            events = await asyncio.to_thread(fetch_events, config.fetch_options())
        except Exception as exc:
            logger.exception("lexue-attention fetch failed")
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
            config, now, result = await self._run_sync()
        except Exception as exc:
            logger.exception("lexue-attention manual sync failed")
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
            f"日历订阅：{'已设置' if config.calendar_url else '未设置'}",
            f"主动推送会话：{'已绑定' if push_session else '未绑定'}",
            f"每日推送：{'开启' if config.enable_daily_push else '关闭'} {config.daily_push_time}",
            f"自动同步：{'开启' if config.enable_interval_sync else '关闭'} {config.check_interval_minutes} 分钟",
            f"图片卡片：{'开启' if config.enable_image_mode else '关闭'}",
            f"最近错误：{self._last_error or '无'}",
        ]
        yield event.plain_result("\n".join(lines))

    async def _run_sync(self):
        config = self._plugin_config()
        validate_fetch_config(config)
        async with self._sync_lock:
            now = datetime.now(config.timezone)
            result = await asyncio.to_thread(
                sync_events,
                config.fetch_options(),
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

        try:
            from lexue_attention.astrbot_adapter import (
                DDL_CARD_TEMPLATE,
                build_ddl_card_context,
            )

            context = build_ddl_card_context(events, now, title=title, limit=limit)
            return await self.html_render(
                DDL_CARD_TEMPLATE,
                context,
                options={"type": "png", "full_page": True, "timeout": 10000},
            )
        except Exception:
            logger.exception("lexue-attention render ddl image failed")
            return ""

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
                logger.exception("lexue-attention interval sync failed")

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
                events = await asyncio.to_thread(fetch_events, config.fetch_options())
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
                logger.exception("lexue-attention daily push failed")

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


def _config_get(config: Any, key: str, default: Any = None) -> Any:
    if hasattr(config, "get"):
        return config.get(key, default)
    return default


def _format_error(exc: Exception) -> str:
    text = str(exc)
    if isinstance(exc, requests.exceptions.ConnectionError) and (
        "NameResolutionError" in text or "Failed to resolve" in text
    ):
        return (
            "AstrBot 运行环境无法解析乐学或统一认证域名。"
            "请在部署 AstrBot 的机器/容器里检查 DNS、网络、代理和校园网访问。"
        )
    return text
