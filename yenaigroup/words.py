# -*- coding: utf-8 -*-
"""违禁词命令与自动处罚，对齐椰奶 apps/groupAdmin/groupBannedWords.js。"""

from __future__ import annotations

import re

from .bannedwords import (
    MATCH_TYPE_MAP,
    PENALTY_ACTION_TEXT,
    PENALTY_TYPE_MAP,
    ReplyError,
)
from .onebot import OneBot, OneBotError
from .render import reply_plain, text_seg
from .utils import clean_at_tokens, extract_message_id, raw_text

ADD_RE = re.compile(
    r"^新增(模糊|精确|正则1|正则2|正则)?(踢撤|禁撤|踢黑|踢|禁|撤)?违禁词(.*)$",
    re.S,
)

HELP_TEXT = "\n".join(
    [
        "该命令匹配正则：",
        "^-新增(模糊|精确|正则1|正则2|正则)?(踢|禁|撤|踢撤|禁撤|踢黑)?违禁词",
        "-------------------",
        "支持的模式：模糊，精确，正则1，正则2",
        "支持的处理方式：踢，禁，撤，踢撤，禁撤，踢黑",
        "-------------------",
        "命令示例：",
        '"-新增违禁词123" --- 默认添加精确禁违禁词',
        '"-新增正则1违禁词^123456$" --- 该种方法需将"\\"转义，如：\\d+\\d+\\d+，默认添加正则为正则1',
        '"-新增正则2违禁词/^123456$/" --- 该种方法无需转义',
        '"-新增模糊踢违禁词123" --- 添加模糊匹配处理方法为踢出群聊的正则',
    ],
)

REGEX_ERROR_TEXT = "\n".join(
    [
        "❎ 正则表达式错误",
        "-------------------",
        "使用示例：",
        "-新增正则违禁词^123456$",
        '该种方法需将"\\"转义，如：\\d+\\d+\\d+',
        "-------------------",
        "-新增正则2违禁词/^123456$/",
        "改种方法无需转义，如：/^123456$/",
    ],
)


def _mask(word: str) -> str:
    if not word:
        return ""
    text = str(word)
    hidden = max(0, len(text) - 2)
    return text[:2] + "*" * hidden


async def cmd_add(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    text = clean_at_tokens(raw_text(event))
    found = ADD_RE.fullmatch(text)
    if not found:
        await reply_plain(event, HELP_TEXT)
        return
    match_type = found.group(1) or "精确"
    penalty_type = found.group(2) or "禁"
    words = (found.group(3) or "").strip()
    if not words:
        await reply_plain(event, HELP_TEXT)
        return

    if match_type.startswith("正则"):
        pattern = words
        if match_type == "正则2" and len(pattern) > 1 and pattern.startswith("/") and pattern.endswith("/"):
            pattern = pattern[1:-1]
        try:
            re.compile(pattern)
        except re.error:
            await reply_plain(event, REGEX_ERROR_TEXT)
            return
        words = pattern

    group_id = str(event.get_group_id())
    try:
        result = plugin.banned.add(
            group_id,
            words,
            match_type,
            penalty_type,
            str(event.get_sender_id()),
        )
    except ReplyError as e:
        await reply_plain(event, str(e))
        return
    await plugin.store.save(group_id)
    await reply_plain(
        event,
        "✅ 成功添加屏蔽词\n"
        f"屏蔽词：{result['words']}\n"
        f"匹配模式：{result['matchType']}\n"
        f"处理方式：{result['penaltyType']}",
    )


async def cmd_del(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    words = clean_at_tokens(raw_text(event))
    words = re.sub(r"^删除违禁词", "", words).strip()
    if not words:
        await reply_plain(event, "需要删除的屏蔽词为空")
        return
    group_id = str(event.get_group_id())
    try:
        removed = plugin.banned.delete(group_id, words)
    except ReplyError as e:
        await reply_plain(event, str(e))
        return
    await plugin.store.save(group_id)
    await reply_plain(event, f"✅ 成功删除：{removed}")


async def cmd_query(plugin, event):
    words = clean_at_tokens(raw_text(event))
    words = re.sub(r"^查看违禁词", "", words).strip()
    if not words:
        await reply_plain(event, "需要查询的屏蔽词为空")
        return
    try:
        item = plugin.banned.query(str(event.get_group_id()), words)
    except ReplyError as e:
        await reply_plain(event, str(e))
        return
    await reply_plain(
        event,
        "✅ 查询屏蔽词\n"
        f"屏蔽词：{item['words']}\n"
        f"匹配模式：{item['matchTypeText']}\n"
        f"处理方式：{item['penaltyTypeText']}\n"
        f"添加人：{item.get('addedBy') or '未知'}\n"
        f"添加时间：{item.get('date') or '未知'}",
    )


async def cmd_list(plugin, event):
    group_id = str(event.get_group_id())
    table = plugin.banned.list_raw(group_id)
    if not table:
        await reply_plain(event, "❎ 没有违禁词")
        return
    items = []
    for word, item in table.items():
        items.append(
            [
                text_seg("屏蔽词："),
                text_seg(word),
                text_seg(f"\n匹配模式：{MATCH_TYPE_MAP.get(item.get('matchType'), '未知')}\n"),
                text_seg(f"处理方式：{PENALTY_TYPE_MAP.get(item.get('penaltyType'), '未知')}\n"),
                text_seg(f"添加人：{item.get('addedBy') or '未知'}\n"),
                text_seg(f"添加时间：{item.get('date') or '未知'}"),
            ],
        )
    await plugin.send_list(event, "违禁词列表", items)


async def cmd_mute_time(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    found = re.search(r"(\d+)", clean_at_tokens(raw_text(event)))
    if not found:
        await reply_plain(event, "❎ 请输入禁言时间（秒）")
        return
    group_id = str(event.get_group_id())
    seconds = int(found.group(1))
    plugin.banned.set_mute_time(group_id, seconds)
    await plugin.store.save(group_id)
    await reply_plain(event, f"✅ 群{group_id}违禁词禁言时间已设置为{seconds}s")


async def cmd_title_words(plugin, event):
    group_id = str(event.get_group_id())
    text = clean_at_tokens(raw_text(event))
    current = plugin.banned.title_words(group_id)
    if "查看" in text:
        await reply_plain(event, f"现有的头衔屏蔽词如下：{chr(10).join(current)}")
        return
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    words = [w.strip() for w in re.sub(r"^#?(增加|减少)头衔屏蔽词", "", text).split(",")]
    words = [w for w in dict.fromkeys(words) if w]
    if not words:
        await reply_plain(event, "❎ 请输入要增加或删除的屏蔽词")
        return
    existing = [w for w in words if w in current]
    fresh = [w for w in words if w not in current]
    if "增加" in text:
        if fresh:
            plugin.banned.add_title_words(group_id, fresh)
            await plugin.store.save(group_id)
            await reply_plain(event, f"✅ 成功添加：{'，'.join(fresh)}")
        if existing:
            await reply_plain(event, f"❎ 以下词已存在：{'，'.join(existing)}")
    else:
        if existing:
            plugin.banned.del_title_words(group_id, existing)
            await plugin.store.save(group_id)
            await reply_plain(event, f"✅ 成功删除：{'，'.join(existing)}")
        if fresh:
            await reply_plain(event, f"❎ 以下词未在屏蔽词中：{'，'.join(fresh)}")


async def cmd_toggle_title_filter(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    group_id = str(event.get_group_id())
    exact = plugin.banned.toggle_title_filter(group_id)
    await plugin.store.save(group_id)
    await reply_plain(event, f"✅ 已修改匹配模式为{'精确' if exact else '模糊'}匹配")


async def handle_auto_punish(plugin, event):
    """群消息自动违禁词处理。"""
    if not plugin.conf("enabled", True):
        return
    group_id = str(event.get_group_id() or "")
    if not group_id or not plugin.perm.group_allowed(group_id):
        return
    if not plugin.store.exists(group_id):
        return
    if not plugin.store.banned_words(group_id):
        return
    text = raw_text(event)
    if not text:
        return
    sender_id = str(event.get_sender_id())
    if sender_id == str(event.get_self_id()):
        return
    if plugin.perm.is_master(sender_id) or plugin.perm.is_white(sender_id):
        return
    item = plugin.banned.match(group_id, text)
    if not item:
        return

    onebot = OneBot(event)
    sender_info = await onebot.member_info(group_id, sender_id)
    if str(sender_info.get("role") or "member") in ("owner", "admin"):
        return
    if await onebot.role_of(group_id, str(event.get_self_id())) not in ("owner", "admin"):
        return

    penalty = int(item.get("penaltyType") or 0)
    mute_seconds = plugin.banned.mute_time(group_id)
    try:
        if penalty in (1, 4, 6):
            await onebot.kick(group_id, sender_id)
        if penalty in (2, 5):
            await onebot.set_ban(group_id, sender_id, mute_seconds)
            plugin.record_mute(group_id, sender_id, mute_seconds)
        if penalty in (3, 4, 5):
            message_id = extract_message_id(event)
            if message_id:
                await onebot.recall(message_id)
        if penalty == 6:
            plugin.add_to_list("black_qq", [sender_id])
    except OneBotError as e:
        await reply_plain(event, f"❎ 违禁词处理失败：{e}")
        return

    action_text = PENALTY_ACTION_TEXT.get(penalty, "未知")
    if penalty in (2, 5):
        action_text = f"禁言{mute_seconds}秒" + ("并撤回消息" if penalty == 5 else "")
    sender_name = str(sender_info.get("card") or sender_info.get("nickname") or sender_id)
    await reply_plain(
        event,
        f"触发违禁词：{_mask(str(item.get('rawItem') or ''))}\n"
        f"触发者：{sender_name}({sender_id})\n"
        f"执行：{action_text}",
    )
