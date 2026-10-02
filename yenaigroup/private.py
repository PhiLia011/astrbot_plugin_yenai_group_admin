# -*- coding: utf-8 -*-
"""私聊群管，对齐椰奶 apps/groupAdmin/privateGroupAdmin.js。"""

from __future__ import annotations

import re

from .bannedwords import ReplyError
from .onebot import OneBot, OneBotError
from .render import reply_plain
from .utils import NUM_REG, UNIT_ALT, clean_at_tokens, parse_time_arg, raw_text

MUTE_RE = re.compile(rf"^禁言\s+(\d+)\s+(\d+)\s*({NUM_REG})?\s*({UNIT_ALT})?$")
UNMUTE_RE = re.compile(r"^解禁\s+(\d+)\s+(\d+)$")
MUTE_ALL_RE = re.compile(r"^全(?:体|员)(禁言|解禁)\s+(\d+)$")
KICK_RE = re.compile(r"^踢\s+(\d+)\s+(\d+)$")


async def _prepare(plugin, event, group_id: str, onebot: OneBot):
    """私聊命令统一走主人权限 + 机器人需为群管理。"""
    return await plugin.perm.check(event, "master", "admin", group_id=group_id, onebot=onebot)


async def _guard(plugin, onebot: OneBot, group_id: str, user_id: str) -> None:
    if plugin.perm.is_master(user_id):
        raise ReplyError("❎ 该命令对主人无效")
    info = await onebot.member_info(group_id, user_id)
    if not info:
        raise ReplyError("❎ 这个群没有这个人哦~")
    role = str(info.get("role") or "member")
    if role == "owner":
        raise ReplyError("❎ 权限不足，该命令对群主无效")
    if role == "admin":
        raise ReplyError("❎ 只有主人才能对管理执行该命令")
    if plugin.perm.is_white(user_id):
        raise ReplyError("❎ 该用户为白名单成员，不可操作")


async def cmd_mute(plugin, event):
    text = clean_at_tokens(raw_text(event))
    found = MUTE_RE.fullmatch(text)
    if not found:
        await reply_plain(event, "❎ 格式：-禁言 群号 QQ [时长][单位]")
        return
    group_id, user_id, num, unit = found.groups()
    onebot = OneBot(event)
    if not await _prepare(plugin, event, group_id, onebot):
        return
    seconds = parse_time_arg(num, unit)
    if seconds is None:
        seconds = int(plugin.conf("default_mute_seconds", 300))
    unit_text = unit or "秒"
    if seconds != 0:
        seconds = min(max(seconds, 1), int(plugin.conf("max_mute_seconds", 2592000)))
    try:
        await _guard(plugin, onebot, group_id, user_id)
        await onebot.set_ban(group_id, user_id, seconds)
        plugin.record_mute(group_id, user_id, seconds)
    except ReplyError as e:
        await reply_plain(event, str(e))
        return
    except OneBotError as e:
        await reply_plain(event, f"❎ {e}")
        return
    name = await onebot.member_name(group_id, user_id)
    await reply_plain(event, f"✅ 已将「{name}」禁言{num if num else seconds}{unit_text}")


async def cmd_unmute(plugin, event):
    text = clean_at_tokens(raw_text(event))
    found = UNMUTE_RE.fullmatch(text)
    if not found:
        await reply_plain(event, "❎ 格式：-解禁 群号 QQ")
        return
    group_id, user_id = found.groups()
    onebot = OneBot(event)
    if not await _prepare(plugin, event, group_id, onebot):
        return
    name = await onebot.member_name(group_id, user_id)
    try:
        await onebot.set_ban(group_id, user_id, 0)
        plugin.record_mute(group_id, user_id, 0)
    except OneBotError as e:
        await reply_plain(event, f"❎ {e}")
        return
    await reply_plain(event, f"✅ 已将「{name}」解除禁言")


async def cmd_mute_all(plugin, event):
    text = clean_at_tokens(raw_text(event))
    found = MUTE_ALL_RE.fullmatch(text)
    if not found:
        await reply_plain(event, "❎ 格式：-全体禁言 群号")
        return
    action, group_id = found.groups()
    onebot = OneBot(event)
    if not await _prepare(plugin, event, group_id, onebot):
        return
    enable = action == "禁言"
    try:
        await onebot.set_whole_ban(group_id, enable)
    except OneBotError as e:
        await reply_plain(event, f"❎ {e}")
        return
    info = await onebot.group_info(group_id)
    group_name = info.get("group_name") or group_id
    await reply_plain(
        event,
        f"✅ 已将群「{group_name}({group_id})」{'开启' if enable else '解除'}全体禁言",
    )


async def cmd_kick(plugin, event):
    text = clean_at_tokens(raw_text(event))
    found = KICK_RE.fullmatch(text)
    if not found:
        await reply_plain(event, "❎ 格式：-踢 群号 QQ")
        return
    group_id, user_id = found.groups()
    onebot = OneBot(event)
    if not await _prepare(plugin, event, group_id, onebot):
        return
    try:
        await _guard(plugin, onebot, group_id, user_id)
        await onebot.kick(group_id, user_id)
    except ReplyError as e:
        await reply_plain(event, str(e))
        return
    except OneBotError as e:
        await reply_plain(event, f"❎ {e}")
        return
    await reply_plain(event, f"✅ 已将「{user_id}」踢出群聊")
