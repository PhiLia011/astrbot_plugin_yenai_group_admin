# -*- coding: utf-8 -*-
"""椰奶群管（AstrBot 版）插件入口。

命令统一使用 AstrBot 全局触发词（默认 “-”），1:1 复刻 Yunzai 椰奶群管插件的群管功能。
注意：AstrBot 只识别插件主模块内的 handler，因此所有 @filter 装饰器都写在 main.py，
具体业务逻辑放在 yenaigroup/ 下的模块里。
"""

from __future__ import annotations

import asyncio
import datetime
import inspect
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import At, Plain
from astrbot.api.star import Context, Star, StarTools, register

from .yenaigroup import admin, clean, events, extra, words
from .yenaigroup.bannedwords import BannedWords
from .yenaigroup.permission import Permission
from .yenaigroup.render import send_list as render_send_list
from .yenaigroup.store import GroupStore
from .yenaigroup.tasks import MuteTaskScheduler
from .yenaigroup.utils import has_wake_prefix
from .yenaigroup.verify import VerifyManager
from .yenaigroup.vote import VoteManager

PLUGIN_NAME = "astrbot_plugin_yenai_group_admin"
AUTHOR = "Firefly"
DESC = "椰奶群管：禁言/踢人/违禁词/黑白名单/投票/入群验证/群公告/定时禁言等全套群管功能"
VERSION = "v1.2.0"
REPO = "https://github.com/PhiLia011/astrbot_plugin_yenai_group_admin"

PENDING_TTL = 180


@register(PLUGIN_NAME, AUTHOR, DESC, VERSION, REPO)
class YenaiGroupAdminPlugin(Star):
    """椰奶群管插件主类。"""

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        # 注意不能写 config or {}：AstrBotConfig 为空时是 falsy，
        # 那样会把配置对象换成普通 dict，丢掉 save_config() 导致设置静默丢失
        self.config = config if config is not None else {}
        self.data_dir = Path(StarTools.get_data_dir())
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = GroupStore(self.data_dir)
        self.banned = BannedWords(self.store, self)
        self.perm = Permission(self)
        self.votes = VoteManager()
        self.verify = VerifyManager()
        self.tasks = MuteTaskScheduler(self)
        self.pending: dict[str, dict] = {}
        self._client = None
        self._bg: set[asyncio.Task] = set()
        self._wake_prefixes: list[str] = self._load_wake_prefixes()
        logger.info("[yenai群管] 插件已加载，数据目录：%s", self.data_dir)
        # 定时任务随插件启动恢复（无事件循环时静默跳过，交给加载完成钩子重试）
        try:
            self.tasks.ensure_started()
        except Exception as e:  # noqa: BLE001
            logger.debug("[yenai群管] 初始化定时任务失败，稍后重试：%s", e)

    # ================= 基础辅助 =================

    def _load_wake_prefixes(self) -> list[str]:
        try:
            prefixes = self.context.get_config().get("wake_prefix", ["-"])
            return [str(p) for p in prefixes if str(p)]
        except Exception:  # noqa: BLE001
            return ["-"]

    def conf(self, key: str, default=None):
        try:
            value = self.config.get(key, default)
        except Exception:  # noqa: BLE001
            value = default
        return default if value is None else value

    def set_conf(self, key: str, value) -> bool:
        """写入插件配置并落盘；返回是否真的保存成功，供调用方判断。"""
        self.config[key] = value
        saver = getattr(self.config, "save_config", None)
        if not callable(saver):
            logger.error(
                "[yenai群管] 插件配置不可保存（_conf_schema.json 未加载？），"
                "本次设置「%s」仅在内存生效，重启后会丢失",
                key,
            )
            return False
        try:
            saver()
        except Exception as e:  # noqa: BLE001
            logger.error("[yenai群管] 保存插件配置失败：%s", e)
            return False
        return True

    def qq_list(self, key: str) -> list[str]:
        return [str(i).strip() for i in (self.conf(key, []) or []) if str(i).strip()]

    def add_to_list(self, key: str, ids: list[str]) -> bool:
        current = self.qq_list(key)
        for item in ids:
            text = str(item).strip()
            if text and text not in current:
                current.append(text)
        return self.set_conf(key, current)

    def remove_from_list(self, key: str, ids: list[str]) -> bool:
        drop = {str(i).strip() for i in ids}
        return self.set_conf(key, [i for i in self.qq_list(key) if i not in drop])

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return task

    async def find_client(self):
        if self._client is not None:
            return self._client
        try:
            platforms = self.context.platform_manager.get_insts()
        except Exception:  # noqa: BLE001
            platforms = []
        for platform in platforms or []:
            getter = getattr(platform, "get_client", None)
            if callable(getter):
                try:
                    client = getter()
                    if inspect.isawaitable(client):
                        client = await client
                    if client is not None and hasattr(client, "call_action"):
                        self._client = client
                        return client
                except Exception:  # noqa: BLE001
                    continue
            for attr in ("client", "bot", "api"):
                client = getattr(platform, attr, None)
                if client is not None and hasattr(client, "call_action"):
                    self._client = client
                    return client
        return None

    def wake_ok(self, event) -> bool:
        if not self.conf("enabled", True):
            return False
        group_id = str(event.get_group_id() or "")
        if group_id and not self.perm.group_allowed(group_id):
            return False
        return has_wake_prefix(event, self._wake_prefixes)

    @staticmethod
    async def reply(event, text: str) -> None:
        await event.send(event.chain_result([Plain(text=text)]))

    async def send_group(self, event, text: str, at: str | None = None) -> None:
        chain = []
        if at:
            chain.append(At(qq=str(at)))
        chain.append(Plain(text=text))
        try:
            await self.context.send_message(
                event.unified_msg_origin,
                MessageChain(chain=chain),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[yenai群管] 主动发送消息失败：%s", e)

    async def send_list(self, event, title: str, items: list[list[dict]]) -> None:
        from .yenaigroup.onebot import OneBot

        await render_send_list(
            event,
            OneBot(event),
            title,
            items,
            forward=bool(self.conf("list_forward", True)),
        )

    # ---- 时间与本地禁言登记 ----

    @staticmethod
    def now_ts() -> int:
        return int(time.time())

    @staticmethod
    def format_ts(ts) -> str:
        try:
            return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError):
            return "未知"

    @staticmethod
    def to_int(value, default: int) -> int:
        from .yenaigroup.utils import translate_china_num

        try:
            return int(translate_china_num(value))
        except (TypeError, ValueError):
            return default

    def record_mute(self, group_id: str, user_id: str, seconds: int) -> None:
        data = self.store.get(group_id)
        table = data.setdefault("localMutes", {})
        key = str(user_id)
        if int(seconds) <= 0:
            table.pop(key, None)
        else:
            table[key] = self.now_ts() + int(seconds)
        self.spawn(self.store.save(group_id))

    def local_mutes(self, group_id: str) -> dict:
        table = self.store.get(group_id).setdefault("localMutes", {})
        return {str(k): int(v) for k, v in table.items() if str(v).isdigit()}

    @staticmethod
    def mute_expire(member: dict) -> int | None:
        value = member.get("shut_up_timestamp")
        if value is None:
            value = member.get("shutup_time")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    # ---- 二次确认 ----

    def set_pending(self, group_id: str, user_id: str, payload: dict) -> None:
        payload["expire"] = time.time() + PENDING_TTL
        self.pending[f"{group_id}:{user_id}"] = payload

    def pop_pending(self, group_id: str, user_id: str) -> dict | None:
        payload = self.pending.pop(f"{group_id}:{user_id}", None)
        if payload and payload.get("expire", 0) < time.time():
            return None
        return payload

    def verify_success_msg(self, group_id: str) -> str:
        data = self.store.get(group_id)
        return data.get("verifySuccessMsg") or self.conf(
            "verify_success_msg",
            "✅ 验证成功，欢迎入群",
        )

    # ================= 生命周期 =================

    @filter.on_astrbot_loaded()
    async def on_loaded(self):
        self.tasks.ensure_started()

    @filter.on_plugin_loaded()
    async def on_plugin_loaded(self, metadata=None):
        """插件被加载/热重载时恢复定时任务。"""
        self.tasks.ensure_started()

    async def terminate(self):
        await self.tasks.shutdown()
        for task in list(self._bg):
            task.cancel()
        logger.info("[yenai群管] 插件已卸载")

    # ================= 基础群管 =================

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^禁言(?!列表)")
    async def cmd_mute(self, event: AstrMessageEvent):
        """禁言群成员，支持自定义时长与单位。"""
        if not self.wake_ok(event):
            return
        await admin.cmd_mute(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^解禁(?!除)")
    async def cmd_unmute(self, event: AstrMessageEvent):
        """解除群成员禁言。"""
        if not self.wake_ok(event):
            return
        await admin.cmd_unmute(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^全(?:体|员)(?:禁言|解禁)")
    async def cmd_mute_all(self, event: AstrMessageEvent):
        """开启或关闭全体禁言。"""
        if not self.wake_ok(event):
            return
        await admin.cmd_mute_all(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^踢")
    async def cmd_kick(self, event: AstrMessageEvent):
        """踢出群成员，-踢黑 同时拉黑。"""
        if not self.wake_ok(event):
            return
        await admin.cmd_kick(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^(?:设置|取消)管理")
    async def cmd_set_admin(self, event: AstrMessageEvent):
        """设置或取消群管理员。"""
        if not self.wake_ok(event):
            return
        await admin.cmd_set_admin(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^(?:修改|设置)头衔")
    async def cmd_set_title(self, event: AstrMessageEvent):
        """修改群成员专属头衔。"""
        if not self.wake_ok(event):
            return
        await admin.cmd_set_title(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^(?:申请|我要)头衔")
    async def cmd_apply_title(self, event: AstrMessageEvent):
        """申请（更换）自己的专属头衔。"""
        if not self.wake_ok(event):
            return
        await admin.cmd_apply_title(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^(?:获取|查看)?禁言列表")
    async def cmd_mute_list(self, event: AstrMessageEvent):
        """查看本群禁言列表。"""
        if not self.wake_ok(event):
            return
        await admin.cmd_mute_list(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^解除全部禁言")
    async def cmd_release_all_mute(self, event: AstrMessageEvent):
        """解除本群全部禁言。"""
        if not self.wake_ok(event):
            return
        await admin.cmd_release_all_mute(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^发通知")
    async def cmd_send_notice(self, event: AstrMessageEvent):
        """发送 @全体成员 通知。"""
        if not self.wake_ok(event):
            return
        await admin.cmd_send_notice(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^(?:加|设|移)精")
    async def cmd_essence(self, event: AstrMessageEvent):
        """对引用的消息加精或移除精华。"""
        if not self.wake_ok(event):
            return
        await admin.cmd_essence(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^我要(?:自闭|禅定)")
    async def cmd_autistic(self, event: AstrMessageEvent):
        """自我禁言。"""
        if not self.wake_ok(event):
            return
        await admin.cmd_autistic(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^(?:定时禁言任务|(?:设置)?定时(?:禁言|解禁)|取消定时(?:禁言|解禁))")
    async def cmd_time_mute(self, event: AstrMessageEvent):
        """定时全体禁言/解禁任务管理。"""
        if not self.wake_ok(event):
            return
        await admin.cmd_time_mute(self, event)

    # ================= 清理与排行 =================

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^(?:查看|清理)从未发言")
    async def cmd_never_speak(self, event: AstrMessageEvent):
        """查看或清理从未发言的群成员。"""
        if not self.wake_ok(event):
            return
        await clean.cmd_never_speak(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^(?:查看|清理|获取).*(?:没|未)发言的人")
    async def cmd_noactive(self, event: AstrMessageEvent):
        """查看或清理指定时长未发言的群成员。"""
        if not self.wake_ok(event):
            return
        await clean.cmd_noactive(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^(?:查看|获取)?(?:不活跃|潜水)排行榜")
    async def cmd_inactive_rank(self, event: AstrMessageEvent):
        """不活跃排行榜。"""
        if not self.wake_ok(event):
            return
        await clean.cmd_inactive_rank(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^(?:查看|获取)?最近的?入群")
    async def cmd_recent_join(self, event: AstrMessageEvent):
        """最近的入群记录。"""
        if not self.wake_ok(event):
            return
        await clean.cmd_recent_join(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^确认清理")
    async def cmd_confirm_clean(self, event: AstrMessageEvent):
        """确认执行批量清理。"""
        if not self.wake_ok(event):
            return
        await clean.cmd_confirm_clean(self, event)

    # ================= 违禁词 =================

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^新增.*违禁词")
    async def cmd_word_add(self, event: AstrMessageEvent):
        """新增违禁词。"""
        if not self.wake_ok(event):
            return
        await words.cmd_add(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^删除违禁词")
    async def cmd_word_del(self, event: AstrMessageEvent):
        """删除违禁词。"""
        if not self.wake_ok(event):
            return
        await words.cmd_del(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^查看违禁词")
    async def cmd_word_query(self, event: AstrMessageEvent):
        """查询违禁词。"""
        if not self.wake_ok(event):
            return
        await words.cmd_query(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^违禁词列表")
    async def cmd_word_list(self, event: AstrMessageEvent):
        """违禁词列表。"""
        if not self.wake_ok(event):
            return
        await words.cmd_list(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^设置违禁词禁言时间")
    async def cmd_word_mute_time(self, event: AstrMessageEvent):
        """设置违禁词触发的禁言时长。"""
        if not self.wake_ok(event):
            return
        await words.cmd_mute_time(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^(?:增加|减少|查看)头衔屏蔽词")
    async def cmd_title_words(self, event: AstrMessageEvent):
        """头衔屏蔽词管理。"""
        if not self.wake_ok(event):
            return
        await words.cmd_title_words(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^切换头衔屏蔽词匹配")
    async def cmd_toggle_title_filter(self, event: AstrMessageEvent):
        """切换头衔屏蔽词匹配模式。"""
        if not self.wake_ok(event):
            return
        await words.cmd_toggle_title_filter(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^违禁词帮助")
    async def cmd_word_help(self, event: AstrMessageEvent):
        """违禁词帮助。"""
        if not self.wake_ok(event):
            return
        await self.reply(event, words.HELP_TEXT)

    # ================= 黑白名单 =================

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^群管(?:加|删)(?:白|黑)")
    async def cmd_list_edit(self, event: AstrMessageEvent):
        """群管黑白名单增删。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_list_edit(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^(?:开启|关闭)白名单")
    async def cmd_toggle_no_ban(self, event: AstrMessageEvent):
        """白名单自动解禁开关。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_toggle_no_ban(self, event)

    # ================= 投票 =================

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^(?:发起)?投票(?:禁言|踢人)")
    async def cmd_vote_start(self, event: AstrMessageEvent):
        """发起投票禁言/踢人。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_vote_start(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^(?:支持|反对)投票")
    async def cmd_vote_follow(self, event: AstrMessageEvent):
        """跟票。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_vote_follow(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^(?:启用|禁用)投票(?:禁言|踢人)")
    async def cmd_vote_switch(self, event: AstrMessageEvent):
        """投票功能开关。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_vote_switch(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^投票设置")
    async def cmd_vote_settings(self, event: AstrMessageEvent):
        """投票参数设置。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_vote_settings(self, event)

    # ================= 入群验证 =================

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^重新验证从未发言")
    async def cmd_verify_never_speak(self, event: AstrMessageEvent):
        """对从未发言的成员重新发起验证。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_verify_never_speak(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^重新验证(?!从未发言)")
    async def cmd_verify_again(self, event: AstrMessageEvent):
        """对指定成员重新发起验证。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_verify_again(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^绕过验证")
    async def cmd_verify_bypass(self, event: AstrMessageEvent):
        """让指定成员直接通过验证。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_verify_bypass(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^(?:开启|关闭)验证")
    async def cmd_verify_toggle(self, event: AstrMessageEvent):
        """开关本群入群验证。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_verify_toggle(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^验证状态")
    async def cmd_verify_status(self, event: AstrMessageEvent):
        """查看本群入群验证当前配置。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_verify_status(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^切换验证模式")
    async def cmd_verify_mode(self, event: AstrMessageEvent):
        """切换验证精确/模糊模式。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_verify_mode(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^切换验证类型")
    async def cmd_verify_type(self, event: AstrMessageEvent):
        """切换验证类型：算式 / 字母验证码。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_verify_type(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^设置验证超时时间")
    async def cmd_verify_time(self, event: AstrMessageEvent):
        """设置验证超时时间。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_verify_time(self, event)

    # ================= 群公告 / 通知 / 帮助 =================

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^发群?公告")
    async def cmd_announce_add(self, event: AstrMessageEvent):
        """发送群公告。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_announce_add(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^删群?公告")
    async def cmd_announce_del(self, event: AstrMessageEvent):
        """删除群公告。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_announce_del(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^查群?公告")
    async def cmd_announce_list(self, event: AstrMessageEvent):
        """查看群公告列表。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_announce_list(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^(?:开启|关闭)加群通知")
    async def cmd_toggle_add_notice(self, event: AstrMessageEvent):
        """开关本群加群申请通知。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_toggle_add_notice(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"^群管帮助")
    async def cmd_help(self, event: AstrMessageEvent):
        """查看群管指令帮助。"""
        if not self.wake_ok(event):
            return
        await extra.cmd_help(self, event)

    # ================= 私聊群管 =================

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.regex(r"^禁言\s+\d+\s+\d+")
    async def private_mute(self, event: AstrMessageEvent):
        """私聊禁言：-禁言 群号 QQ [时长][单位]"""
        if not self.wake_ok(event):
            return
        from .yenaigroup import private

        await private.cmd_mute(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.regex(r"^解禁\s+\d+\s+\d+")
    async def private_unmute(self, event: AstrMessageEvent):
        """私聊解禁：-解禁 群号 QQ"""
        if not self.wake_ok(event):
            return
        from .yenaigroup import private

        await private.cmd_unmute(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.regex(r"^全(?:体|员)(?:禁言|解禁)\s+\d+")
    async def private_mute_all(self, event: AstrMessageEvent):
        """私聊全体禁言：-全体禁言 群号"""
        if not self.wake_ok(event):
            return
        from .yenaigroup import private

        await private.cmd_mute_all(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.regex(r"^踢\s+\d+\s+\d+")
    async def private_kick(self, event: AstrMessageEvent):
        """私聊踢人：-踢 群号 QQ"""
        if not self.wake_ok(event):
            return
        from .yenaigroup import private

        await private.cmd_kick(self, event)

    # ================= 事件与自动处理 =================

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=300)
    async def on_notice(self, event: AstrMessageEvent):
        """入群/退群/禁言通知与加群申请处理。"""
        raw = getattr(event.message_obj, "raw_message", None)
        if not isinstance(raw, dict):
            return
        if raw.get("post_type") in ("notice", "request"):
            await events.handle_notice(self, event)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=200)
    async def on_verify_answer(self, event: AstrMessageEvent):
        """拦截入群验证的答案消息。"""
        raw = getattr(event.message_obj, "raw_message", None)
        if isinstance(raw, dict) and raw.get("post_type") != "message":
            return
        if await events.handle_verify_answer(self, event):
            event.stop_event()

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=-100)
    async def on_group_message_check(self, event: AstrMessageEvent):
        """黑名单与违禁词的自动处理。"""
        raw = getattr(event.message_obj, "raw_message", None)
        if isinstance(raw, dict) and raw.get("post_type") != "message":
            return
        if getattr(event, "_has_send_oper", False):
            return
        await events.handle_blacklist_message(self, event)
        # 黑名单踢人已经处理过这条消息时不再叠加违禁词处罚
        if getattr(event, "_has_send_oper", False):
            return
        await words.handle_auto_punish(self, event)
