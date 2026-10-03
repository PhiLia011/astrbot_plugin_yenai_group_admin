# -*- coding: utf-8 -*-
"""清理与排行：从未发言 / 多久没发言 / 不活跃排行 / 最近入群。"""

from __future__ import annotations

import asyncio
import datetime
import random
import re
import time as _time

from .onebot import OneBot, OneBotError
from .render import avatar_url, image_seg, reply_plain, text_seg
from .utils import NUM_REG, UNIT_ALT, chunked, clean_at_tokens, raw_text, unit_multiplier

PAGE_SIZE = 30


def _fields_supported(members: list[dict]) -> bool:
    """协议端是否返回 join_time / last_sent_time 这类非标准字段。"""
    for member in members[:20]:
        if "join_time" in member or "last_sent_time" in member:
            return True
    return False


def _fmt_ts(ts) -> str:
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "未知"


async def _members(plugin, event, onebot) -> list[dict] | None:
    members = await onebot.member_list(str(event.get_group_id()))
    if not members:
        await reply_plain(event, "❎ 获取群成员列表失败或群内无人")
        return None
    if not _fields_supported(members):
        await reply_plain(
            event,
            "❎ 当前协议端不支持此功能（缺少 join_time/last_sent_time 字段）",
        )
        return None
    return members


def _never_speak_list(members: list[dict], bot_id: str) -> list[dict]:
    result = []
    for member in members:
        if str(member.get("user_id")) == str(bot_id):
            continue
        if str(member.get("role") or "member") != "member":
            continue
        join_time = member.get("join_time")
        last_sent = member.get("last_sent_time")
        if join_time is None or last_sent is None:
            continue
        if int(join_time) == int(last_sent):
            result.append(member)
    result.sort(key=lambda m: int(m.get("join_time") or 0))
    return result


def _noactive_list(members: list[dict], bot_id: str, times: int, unit: str) -> list[dict] | None:
    unit_seconds = unit_multiplier(unit)
    if unit_seconds < 1:
        return None
    threshold = int(_time.time()) - int(times * unit_seconds)
    result = []
    for member in members:
        if str(member.get("user_id")) == str(bot_id):
            continue
        if str(member.get("role") or "member") != "member":
            continue
        last_sent = member.get("last_sent_time")
        if last_sent is None:
            continue
        if int(last_sent) < threshold:
            result.append(member)
    result.sort(key=lambda m: int(m.get("last_sent_time") or 0))
    return result


async def cmd_never_speak(plugin, event):
    text = clean_at_tokens(raw_text(event))
    view_only = "查看" in text
    if not await plugin.perm.check(event, "admin", "all" if view_only else "admin"):
        return
    onebot = OneBot(event)
    members = await _members(plugin, event, onebot)
    if members is None:
        return
    group_id = str(event.get_group_id())
    targets = _never_speak_list(members, str(event.get_self_id()))
    if not targets:
        await reply_plain(event, "✅ 本群暂无从未发言的人")
        return

    if "清理" in text:
        plugin.set_pending(
            group_id,
            str(event.get_sender_id()),
            {"type": "kick", "user_ids": [str(m.get("user_id")) for m in targets]},
        )
        await reply_plain(
            event,
            f'⚠ 本次共需清理「{len(targets)}」人，防止误触发\n请发送："-确认清理" 开始清理',
        )
        return

    found = re.search(rf"第({NUM_REG})页", text)
    page = plugin.to_int(found.group(1) if found else 1, 1)
    pages = chunked(targets, PAGE_SIZE)
    if page < 1 or page > len(pages):
        await reply_plain(event, "哪有那么多人辣o(´^｀)o")
        return
    current = pages[page - 1]
    header = (
        "以下为进群后从未发言过的人\n"
        f"当前为第{page}页，共{len(pages)}页，本页共{len(current)}人，总共{len(targets)}人"
    )
    if page < len(pages):
        header += f'\n可用 "-查看从未发言过的人第{page + 1}页" 翻页'
    items = [
        [
            image_seg(avatar_url(str(m.get("user_id")))),
            text_seg(f"\nQQ：{m.get('user_id')}\n"),
            text_seg(f"昵称：{m.get('card') or m.get('nickname')}\n"),
            text_seg(f"进群时间：{_fmt_ts(m.get('join_time'))}"),
        ]
        for m in current
    ]
    await plugin.send_list(event, header, items)


async def cmd_noactive(plugin, event):
    text = clean_at_tokens(raw_text(event))
    view_only = "查看" in text
    if not await plugin.perm.check(event, "admin", "all" if view_only else "admin"):
        return
    found = re.search(
        rf"(?:查看|清理|获取)({NUM_REG})个?({UNIT_ALT})(?:没|未)发言的人(?:第({NUM_REG})页)?",
        text,
    )
    if not found:
        await reply_plain(event, "❎ 命令格式有误")
        return
    times = plugin.to_int(found.group(1), 1)
    unit = found.group(2)
    page = plugin.to_int(found.group(3) or 1, 1)

    onebot = OneBot(event)
    members = await _members(plugin, event, onebot)
    if members is None:
        return
    targets = _noactive_list(members, str(event.get_self_id()), times, unit)
    if targets is None:
        await reply_plain(event, "❎ 暂不支持该时间单位")
        return
    if not targets:
        await reply_plain(event, f"✅ 暂时没有{times}{unit}没发言的人")
        return

    if "清理" in text:
        plugin.set_pending(
            str(event.get_group_id()),
            str(event.get_sender_id()),
            {"type": "kick", "user_ids": [str(m.get("user_id")) for m in targets]},
        )
        await reply_plain(
            event,
            f'⚠ 本次共需清理「{len(targets)}」人\n请发送："-确认清理" 开始清理',
        )
        return

    pages = chunked(targets, PAGE_SIZE)
    if page < 1 or page > len(pages):
        await reply_plain(event, "❎ 页数超过最大值")
        return
    current = pages[page - 1]
    header = (
        f"以下为{times}{unit}没发言过的人\n"
        f"当前为第{page}页，共{len(pages)}页，本页共{len(current)}人，总共{len(targets)}人"
    )
    if page < len(pages):
        header += f'\n可用 "-查看{times}{unit}没发言过的人第{page + 1}页" 翻页'
    items = [
        [
            image_seg(avatar_url(str(m.get("user_id")))),
            text_seg(f"\nQQ：{m.get('user_id')}\n"),
            text_seg(f"昵称：{m.get('card') or m.get('nickname')}\n"),
            text_seg(f"最后发言时间：{_fmt_ts(m.get('last_sent_time'))}"),
        ]
        for m in current
    ]
    await plugin.send_list(event, header, items)


async def cmd_inactive_rank(plugin, event):
    text = clean_at_tokens(raw_text(event))
    found = re.search(rf"({NUM_REG})", text)
    num = plugin.to_int(found.group(1) if found else 10, 10)
    onebot = OneBot(event)
    members = await _members(plugin, event, onebot)
    if members is None:
        return
    ranked = sorted(members, key=lambda m: int(m.get("last_sent_time") or 0))[: max(1, num)]
    items = []
    for index, member in enumerate(ranked, 1):
        items.append(
            [
                text_seg(f"第{index}名：\n"),
                image_seg(avatar_url(str(member.get("user_id")))),
                text_seg(f"\nQQ：{member.get('user_id')}\n"),
                text_seg(f"昵称：{member.get('card') or member.get('nickname')}\n"),
                text_seg(f"最后发言时间：{_fmt_ts(member.get('last_sent_time'))}"),
            ],
        )
    await plugin.send_list(event, f"不活跃排行榜top1 - top{num}", items)


async def cmd_recent_join(plugin, event):
    text = clean_at_tokens(raw_text(event))
    found = re.search(rf"({NUM_REG})", text)
    num = plugin.to_int(found.group(1) if found else 10, 10)
    onebot = OneBot(event)
    members = await _members(plugin, event, onebot)
    if members is None:
        return
    ranked = sorted(members, key=lambda m: int(m.get("join_time") or 0), reverse=True)[: max(1, num)]
    items = []
    for member in ranked:
        items.append(
            [
                image_seg(avatar_url(str(member.get("user_id")))),
                text_seg(f"\nQQ：{member.get('user_id')}\n"),
                text_seg(f"昵称：{member.get('card') or member.get('nickname')}\n"),
                text_seg(f"入群时间：{_fmt_ts(member.get('join_time'))}\n"),
                text_seg(f"最后发言时间：{_fmt_ts(member.get('last_sent_time'))}"),
            ],
        )
    await plugin.send_list(event, f"最近的{num}条入群记录", items)


async def cmd_confirm_clean(plugin, event):
    group_id = str(event.get_group_id())
    sender_id = str(event.get_sender_id())
    pending = plugin.pop_pending(group_id, sender_id)
    if not pending or pending.get("type") != "kick" or not pending.get("user_ids"):
        await reply_plain(event, "❎ 已取消")
        return
    onebot = OneBot(event)
    await reply_plain(event, "⚠ 开始清理，这可能需要一点时间")
    items = []
    for user_id in pending["user_ids"]:
        try:
            await onebot.kick(group_id, user_id)
            items.append([text_seg(f"成功清理：{user_id}")])
        except OneBotError as e:
            items.append([text_seg(f"清理 {user_id} 失败：{e}")])
        await asyncio.sleep(random.uniform(1, 3))
    await plugin.send_list(event, "以下为每次清理的结果", items)
