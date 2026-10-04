# -*- coding: utf-8 -*-
"""OneBot(aiocqhttp) 群管接口统一封装。"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from astrbot.api import logger

CALL_TIMEOUT = 20.0


class OneBotError(Exception):
    """OneBot 调用失败。"""


def get_client(event) -> Any | None:
    """从事件里取出可用的 aiocqhttp 客户端。"""
    candidate = getattr(event, "bot", None)
    if candidate is None:
        return None
    if hasattr(candidate, "call_action"):
        return candidate
    api = getattr(candidate, "api", None)
    if api is not None and hasattr(api, "call_action"):
        return api
    return None


class OneBot:
    """围绕事件对象的一层薄封装，所有群操作都走 OneBot call_action。"""

    def __init__(self, event):
        self.event = event
        self.client = get_client(event)

    async def call(self, action: str, **params) -> Any:
        client = self.client or get_client(self.event)
        if client is None:
            raise OneBotError("未获取到 OneBot 客户端（请确认平台适配器为 aiocqhttp）")
        try:
            result = client.call_action(action, **params)
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, timeout=CALL_TIMEOUT)
        except asyncio.TimeoutError as e:
            raise OneBotError(f"调用 {action} 超时") from e
        except Exception as e:  # noqa: BLE001 - 协议端异常种类繁多，统一转成提示
            raise OneBotError(f"调用 {action} 失败：{e}") from e
        if isinstance(result, dict):
            retcode = result.get("retcode")
            if retcode not in (None, 0):
                message = result.get("message") or result.get("msg") or result
                raise OneBotError(f"调用 {action} 被拒绝：{message}")
            if "data" in result and len(result) <= 4:
                return result["data"]
        return result

    async def try_call(self, actions, **params) -> Any:
        """依次尝试候选接口名，返回第一个成功的结果；全部失败抛出最后一个错误。"""
        if isinstance(actions, str):
            actions = [actions]
        last_error: Exception | None = None
        for action in actions:
            try:
                return await self.call(action, **params)
            except OneBotError as e:
                last_error = e
                logger.debug("[yenai群管] 接口 %s 调用失败：%s", action, e)
        raise last_error or OneBotError("接口调用失败")

    # ---------------- 基础资料 ----------------

    async def member_info(self, group_id: str, user_id: str, no_cache: bool = True) -> dict:
        try:
            info = await self.call(
                "get_group_member_info",
                group_id=int(group_id),
                user_id=int(user_id),
                no_cache=no_cache,
            )
            return info if isinstance(info, dict) else {}
        except OneBotError:
            return {}

    async def member_list(self, group_id: str) -> list[dict]:
        try:
            data = await self.call("get_group_member_list", group_id=int(group_id))
        except OneBotError:
            return []
        return data if isinstance(data, list) else []

    async def role_of(self, group_id: str, user_id: str) -> str:
        info = await self.member_info(group_id, user_id)
        return str(info.get("role") or "member")

    async def member_name(self, group_id: str, user_id: str) -> str:
        info = await self.member_info(group_id, user_id)
        return str(info.get("card") or info.get("nickname") or user_id)

    async def group_info(self, group_id: str) -> dict:
        try:
            data = await self.call("get_group_info", group_id=int(group_id))
            return data if isinstance(data, dict) else {}
        except OneBotError:
            return {}

    # ---------------- 群管操作 ----------------

    async def set_ban(self, group_id: str, user_id: str, duration: int) -> None:
        await self.call(
            "set_group_ban",
            group_id=int(group_id),
            user_id=int(user_id),
            duration=int(duration),
        )

    async def set_whole_ban(self, group_id: str, enable: bool) -> None:
        await self.call(
            "set_group_whole_ban",
            group_id=int(group_id),
            enable=bool(enable),
        )

    async def kick(self, group_id: str, user_id: str, reject_add_request: bool = False) -> None:
        await self.call(
            "set_group_kick",
            group_id=int(group_id),
            user_id=int(user_id),
            reject_add_request=bool(reject_add_request),
        )

    async def set_admin(self, group_id: str, user_id: str, enable: bool) -> None:
        await self.call(
            "set_group_admin",
            group_id=int(group_id),
            user_id=int(user_id),
            enable=bool(enable),
        )

    async def set_title(self, group_id: str, user_id: str, title: str) -> None:
        await self.call(
            "set_group_special_title",
            group_id=int(group_id),
            user_id=int(user_id),
            special_title=str(title),
        )

    async def set_card(self, group_id: str, user_id: str, card: str) -> None:
        await self.call(
            "set_group_card",
            group_id=int(group_id),
            user_id=int(user_id),
            card=str(card),
        )

    async def set_group_name(self, group_id: str, name: str) -> None:
        await self.call("set_group_name", group_id=int(group_id), group_name=str(name))

    async def recall(self, message_id: str) -> None:
        await self.call("delete_msg", message_id=int(message_id))

    async def set_essence(self, message_id: str) -> None:
        await self.try_call(("set_essence_msg", "_set_essence_msg"), message_id=int(message_id))

    async def delete_essence(self, message_id: str) -> None:
        await self.try_call(
            ("delete_essence_msg", "_delete_essence_msg"),
            message_id=int(message_id),
        )

    async def send_notice(self, group_id: str, content: str, image: str | None = None) -> None:
        params: dict[str, Any] = {"group_id": int(group_id), "content": content}
        if image:
            params["image"] = image
        await self.try_call(("_send_group_notice", "send_group_notice"), **params)

    async def get_notice(self, group_id: str) -> list[dict]:
        data = await self.try_call(
            ("_get_group_notice", "get_group_notice"),
            group_id=int(group_id),
        )
        if isinstance(data, dict):
            data = data.get("notices") or data.get("data") or []
        return data if isinstance(data, list) else []

    async def del_notice(self, group_id: str, notice_id: str) -> Any:
        return await self.try_call(
            ("_del_group_notice", "delete_group_notice"),
            group_id=int(group_id),
            notice_id=notice_id,
        )

    async def approve_add_request(
        self,
        flag: str,
        sub_type: str,
        approve: bool,
        reason: str = "",
    ) -> None:
        params: dict[str, Any] = {
            "flag": flag,
            "sub_type": sub_type or "add",
            "approve": bool(approve),
        }
        if not approve and reason:
            params["reason"] = reason
        await self.call("set_group_add_request", **params)

    # ---------------- 消息发送 ----------------

    async def send_forward(self, group_id: str, nodes: list[dict]) -> None:
        await self.call(
            "send_group_forward_msg",
            group_id=int(group_id),
            messages=nodes,
        )
