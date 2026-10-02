# -*- coding: utf-8 -*-
"""权限校验，对齐椰奶 lib/common/common.js 的 checkPermission。"""

from __future__ import annotations

from astrbot.api.message_components import Plain

from .onebot import OneBot, OneBotError

ADMIN_ROLES = ("admin", "owner")


class Permission:
    """主人 / 群主 / 管理员权限判定。"""

    def __init__(self, plugin):
        self.plugin = plugin
        self._role_cache: dict[tuple[str, str], str] = {}

    # ---------------- 名单 ----------------

    @property
    def master_ids(self) -> set[str]:
        ids: set[str] = set()
        try:
            cfg = self.plugin.context.get_config()
            for item in cfg.get("admins_id", []) or []:
                text = str(item).strip()
                if text.isdigit():
                    ids.add(text)
        except Exception:  # noqa: BLE001
            pass
        for item in self.plugin.conf("master_qq", []) or []:
            text = str(item).strip()
            if text.isdigit():
                ids.add(text)
        return ids

    @property
    def white_ids(self) -> set[str]:
        return {
            str(i).strip()
            for i in (self.plugin.conf("white_qq", []) or [])
            if str(i).strip()
        }

    @property
    def black_ids(self) -> set[str]:
        return {
            str(i).strip()
            for i in (self.plugin.conf("black_qq", []) or [])
            if str(i).strip()
        }

    def is_master(self, user_id: str) -> bool:
        return str(user_id) in self.master_ids

    def is_white(self, user_id: str) -> bool:
        return str(user_id) in self.white_ids

    def is_black(self, user_id: str) -> bool:
        return str(user_id) in self.black_ids

    def group_allowed(self, group_id: str) -> bool:
        """群白名单留空表示全部生效，群黑名单优先。"""
        gid = str(group_id)
        black = {
            str(i).strip() for i in (self.plugin.conf("group_black_list", []) or [])
        }
        if gid in black:
            return False
        white = {
            str(i).strip()
            for i in (self.plugin.conf("group_white_list", []) or [])
            if str(i).strip()
        }
        if white and gid not in white:
            return False
        return True

    # ---------------- 角色 ----------------

    async def role_of(self, group_id: str, user_id: str, onebot: OneBot | None = None) -> str:
        if not group_id:
            return "member"
        key = (str(group_id), str(user_id))
        if onebot is None:
            return self._role_cache.get(key, "member")
        role = await onebot.role_of(group_id, user_id)
        self._role_cache[key] = role
        return role

    # ---------------- 校验 ----------------

    async def check(
        self,
        event,
        permission: str = "all",
        role: str = "all",
        group_id: str | None = None,
        onebot: OneBot | None = None,
        reply: bool = True,
    ) -> bool:
        """返回是否放行；不放行时按椰奶文案回复。

        permission: 执行者所需权限 all / admin / owner / master
        role: 机器人自身需要的群权限 all / admin / owner
        """
        group_id = str(group_id or event.get_group_id() or "")
        sender_id = str(event.get_sender_id())
        bot_id = str(event.get_self_id())
        onebot = onebot or OneBot(event)

        bot_role = ""
        if group_id:
            try:
                bot_role = await onebot.role_of(group_id, bot_id)
            except OneBotError:
                bot_role = ""

        message: str | None = None
        if role == "owner" and bot_role != "owner":
            message = "❎ Bot权限不足，需要群主权限"
        elif role == "admin" and bot_role not in ADMIN_ROLES:
            message = "❎ Bot权限不足，需要管理员权限"

        if not self.is_master(sender_id):
            sender_role = ""
            if group_id:
                sender_role = await self.role_of(group_id, sender_id, onebot)
            if permission == "master":
                message = "❎ 该命令仅限主人可用"
            elif permission == "owner" and sender_role != "owner":
                message = "❎ 该命令仅限群主可用"
            elif permission == "admin" and sender_role not in ADMIN_ROLES:
                message = "❎ 该命令仅限管理可用"

        if message is None:
            return True
        if reply:
            await reply_text(event, message)
        return False


async def reply_text(event, text: str) -> None:
    await event.send(event.chain_result([Plain(text=text)]))
