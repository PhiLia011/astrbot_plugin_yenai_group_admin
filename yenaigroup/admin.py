# -*- coding: utf-8 -*-
"""基础群管命令实现：禁言/解禁/全体/踢人/管理/头衔/禁言列表/加精/自闭/定时。"""

from __future__ import annotations

import re

from astrbot.api.message_components import At, Plain, Reply

from .bannedwords import ReplyError
from .onebot import OneBot, OneBotError
from .render import avatar_url, image_seg, reply_plain, text_seg
from .tasks import hhmm_to_cron
from .utils import (
    NUM_REG,
    UNIT_ALT,
    at_targets,
    clean_at_tokens,
    format_duration,
    parse_time_arg,
    raw_text,
)

ROLE_MAP = {"admin": "群管理", "owner": "群主", "member": "群员"}

_MUTE_RE = re.compile(rf"^(?:(\d+)\s*)?(?:({NUM_REG})?\s*({UNIT_ALT})?)?$")
_MUTE_AT_RE = re.compile(rf"^(?:({NUM_REG})?\s*({UNIT_ALT})?)?$")
_QQ_RE = re.compile(r"\d{5,}")


def strip_command(text: str, *names: str) -> str:
    """去掉命令名，返回参数部分。"""
    result = clean_at_tokens(text)
    for name in names:
        if result.startswith(name):
            result = result[len(name) :]
            break
    return result.strip()


def parse_mute_args(text: str, has_at: bool) -> tuple[str | None, str | None, str | None]:
    """解析参数为 (QQ, 时长数值, 单位)。"""
    text = text.strip()
    if text == "":
        return None, None, None
    if has_at:
        found = _MUTE_AT_RE.fullmatch(text)
        if not found:
            return None, None, None
        return None, found.group(1), found.group(2)
    found = _MUTE_RE.fullmatch(text)
    if not found:
        return None, None, None
    return found.group(1), found.group(2), found.group(3)


async def _target_names(onebot: OneBot, group_id: str, user_ids: list[str]) -> list[str]:
    names = []
    for user_id in user_ids:
        info = await onebot.member_info(group_id, user_id)
        names.append(str(info.get("card") or info.get("nickname") or user_id))
    return names


async def _ensure_not_protected(
    plugin,
    onebot: OneBot,
    group_id: str,
    user_id: str,
    is_more: bool,
    bot_id: str,
):
    """主人/群主/管理员/白名单保护检查，失败抛 ReplyError。"""
    if plugin.perm.is_master(user_id):
        raise ReplyError("❎ 该命令对主人无效")
    info = await onebot.member_info(group_id, user_id)
    if not info:
        raise ReplyError(f"❎ 这个群没有{user_id if is_more else '这个人'}哦~")
    role = str(info.get("role") or "member")
    if role == "owner":
        raise ReplyError("❎ 权限不足，该命令对群主无效")
    if role == "admin":
        bot_role = await onebot.role_of(group_id, bot_id)
        if bot_role != "owner":
            raise ReplyError("❎ 权限不足，需要群主权限")
    return info


async def cmd_mute(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    onebot = OneBot(event)
    group_id = str(event.get_group_id())
    targets = at_targets(event, event.get_self_id())
    qq, num, unit = parse_mute_args(strip_command(raw_text(event), "禁言"), bool(targets))
    if qq:
        targets = [qq]
    if not targets:
        await reply_plain(event, "❎ 请输入正确的用户ID")
        return

    seconds = parse_time_arg(num, unit)
    if seconds is None:
        seconds = int(plugin.conf("default_mute_seconds", 300))
    unit_text = unit or "秒"
    if seconds != 0:
        seconds = min(seconds, int(plugin.conf("max_mute_seconds", 2592000)))
        if seconds < 1:
            seconds = 1

    names = []
    try:
        for user_id in targets:
            await _ensure_not_protected(
                plugin,
                onebot,
                group_id,
                user_id,
                len(targets) > 1,
                str(event.get_self_id()),
            )
            if plugin.perm.is_white(user_id) and not plugin.perm.is_master(str(event.get_sender_id())) and seconds != 0:
                raise ReplyError(f"❎ {user_id if len(targets) > 1 else '该用户'}为白名单成员，不可操作")
            await onebot.set_ban(group_id, user_id, seconds)
            plugin.record_mute(group_id, user_id, seconds)
        names = await _target_names(onebot, group_id, targets)
    except ReplyError as e:
        await reply_plain(event, str(e))
        return
    except OneBotError as e:
        await reply_plain(event, f"❎ {e}")
        return

    if seconds == 0:
        await reply_plain(event, f"✅ 已将「{'，'.join(names)}」解除禁言")
    else:
        await reply_plain(event, f"✅ 已将「{'，'.join(names)}」禁言{seconds if num is None else num}{unit_text}")


async def cmd_unmute(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    onebot = OneBot(event)
    group_id = str(event.get_group_id())
    targets = at_targets(event, event.get_self_id())
    rest = strip_command(raw_text(event), "解禁")
    found = re.fullmatch(r"(\d+)?\s*", rest or "")
    if found and found.group(1):
        targets = [found.group(1)]
    if not targets:
        await reply_plain(event, "❎ 请输入正确的用户ID")
        return

    names = await _target_names(onebot, group_id, targets)
    try:
        for user_id in targets:
            await onebot.set_ban(group_id, user_id, 0)
            plugin.record_mute(group_id, user_id, 0)
    except OneBotError as e:
        await reply_plain(event, f"❎ {e}")
        return
    await reply_plain(event, f"✅ 已将「{'，'.join(names)}」解除禁言")


async def cmd_mute_all(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    onebot = OneBot(event)
    group_id = str(event.get_group_id())
    enable = "解禁" not in raw_text(event)
    try:
        await onebot.set_whole_ban(group_id, enable)
    except OneBotError:
        await reply_plain(event, "❎ 未知错误")
        return
    await reply_plain(event, f"✅ 已{'开启' if enable else '关闭'}全体禁言")


async def cmd_kick(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    onebot = OneBot(event)
    group_id = str(event.get_group_id())
    text = raw_text(event)
    block = "踢黑" in clean_at_tokens(text).replace(" ", "")
    targets = at_targets(event, event.get_self_id())
    if not targets:
        rest = strip_command(text, "踢黑", "踢人", "踢")
        found = _QQ_RE.search(rest)
        if found:
            targets = [found.group(0)]
    if not targets:
        await reply_plain(event, "❎ 请输入正确的QQ号")
        return

    try:
        for user_id in targets:
            await _ensure_not_protected(
                plugin,
                onebot,
                group_id,
                user_id,
                len(targets) > 1,
                str(event.get_self_id()),
            )
            if plugin.perm.is_white(user_id) and not plugin.perm.is_master(str(event.get_sender_id())):
                raise ReplyError(f"❎ {user_id if len(targets) > 1 else '该用户'}是白名单成员，不可操作")
            await onebot.kick(group_id, user_id, reject_add_request=block)
    except ReplyError as e:
        await reply_plain(event, str(e))
        return
    except OneBotError as e:
        await reply_plain(event, f"❎ {e}")
        return

    if block:
        plugin.add_to_list("black_qq", targets)
    await reply_plain(event, f"✅ 已将「{'，'.join(targets)}」踢出群聊")


async def cmd_set_admin(plugin, event):
    if not await plugin.perm.check(event, "master", "owner"):
        return
    onebot = OneBot(event)
    group_id = str(event.get_group_id())
    add = "设置管理" in clean_at_tokens(raw_text(event)).replace(" ", "")
    targets = at_targets(event, event.get_self_id())
    if not targets:
        found = _QQ_RE.search(strip_command(raw_text(event), "设置管理", "取消管理"))
        if found:
            targets = [found.group(0)]
    if not targets or not all(_QQ_RE.fullmatch(t) for t in targets):
        await reply_plain(event, "❎ 请输入正确的QQ号")
        return

    names = []
    try:
        for user_id in targets:
            info = await onebot.member_info(group_id, user_id)
            if not info:
                raise ReplyError("❎ 这个群没有这个人哦~")
            await onebot.set_admin(group_id, user_id, add)
            names.append(str(info.get("card") or info.get("nickname") or user_id))
    except ReplyError as e:
        await reply_plain(event, str(e))
        return
    except OneBotError as e:
        await reply_plain(event, f"❎ {e}")
        return

    if add:
        await reply_plain(event, f"✅ 已经把「{'，'.join(names)}」设置为管理啦！！")
    else:
        await reply_plain(event, f"✅ 已取消「{'，'.join(names)}」的管理啦！！")


async def cmd_set_title(plugin, event):
    if not await plugin.perm.check(event, "master", "owner"):
        return
    onebot = OneBot(event)
    group_id = str(event.get_group_id())
    targets = at_targets(event, event.get_self_id())
    if not targets:
        await reply_plain(event, "请艾特要修改的人哦~")
        return
    title = strip_command(raw_text(event), "修改头衔", "设置头衔")
    title = re.sub(r"^[:：\s]+", "", title).strip()
    try:
        await onebot.set_title(group_id, targets[0], title)
    except OneBotError:
        await reply_plain(event, "❎ 未知错误")
        return
    name = await onebot.member_name(group_id, targets[0])
    await reply_plain(event, f"✅ 已经将「{name}」的头衔设置为「{title}」")


async def cmd_apply_title(plugin, event):
    if not await plugin.perm.check(event, "all", "owner"):
        return
    onebot = OneBot(event)
    group_id = str(event.get_group_id())
    title = strip_command(raw_text(event), "申请头衔", "我要头衔")
    title = re.sub(r"^[:：\s]+", "", title).strip()
    if not plugin.perm.is_master(str(event.get_sender_id())) and not plugin.conf("title_filter_off", False):
        words = [w for w in plugin.banned.title_words(group_id) if w]
        if words:
            exact = plugin.banned.title_filter_exact(group_id)
            if exact:
                hit = title in words
            else:
                hit = any(word in title for word in words)
            if hit:
                await reply_plain(event, "❎ 包含违禁词")
                return
    try:
        await onebot.set_title(group_id, str(event.get_sender_id()), title)
    except OneBotError:
        await reply_plain(event, "❎ 未知错误")
        return
    await reply_plain(event, f"✅ 已将你的头衔更换为「{title}」")


async def cmd_mute_list(plugin, event):
    onebot = OneBot(event)
    group_id = str(event.get_group_id())
    members = await onebot.member_list(group_id)
    if not members:
        await reply_plain(event, "❎ 获取群成员列表失败")
        return
    now = plugin.now_ts()
    muted = []
    seen = set()
    for member in members:
        expire = plugin.mute_expire(member)
        if expire is None or expire <= now:
            continue
        user_id = str(member.get("user_id"))
        seen.add(user_id)
        muted.append(
            [
                image_seg(avatar_url(user_id)),
                text_seg(f"\n昵称：{member.get('card') or member.get('nickname')}\n"),
                text_seg(f"QQ：{user_id}\n"),
                text_seg(f"群身份：{ROLE_MAP.get(str(member.get('role')), '群员')}\n"),
                text_seg(f"禁言剩余时间：{format_duration(expire - now)}\n"),
                text_seg(f"禁言到期时间：{plugin.format_ts(expire)}"),
            ],
        )
    # 协议端未返回禁言字段时，用插件本地记录的禁言补齐
    for user_id, expire in plugin.local_mutes(group_id).items():
        if user_id in seen or expire <= now:
            continue
        muted.append(
            [
                image_seg(avatar_url(user_id)),
                text_seg(f"\nQQ：{user_id}\n"),
                text_seg(f"禁言剩余时间：{format_duration(expire - now)}\n"),
                text_seg(f"禁言到期时间：{plugin.format_ts(expire)}"),
            ],
        )
    if not muted:
        await reply_plain(event, "❎ 该群没有被禁言的人")
        return
    await plugin.send_list(event, "禁言列表", muted)


async def cmd_release_all_mute(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    onebot = OneBot(event)
    group_id = str(event.get_group_id())
    members = await onebot.member_list(group_id)
    now = plugin.now_ts()
    targets = [
        str(m.get("user_id"))
        for m in members
        if (plugin.mute_expire(m) or 0) > now
    ]
    if not targets:
        await reply_plain(event, "❎ 该群没有被禁言的人")
        return
    try:
        for user_id in targets:
            await onebot.set_ban(group_id, user_id, 0)
            plugin.record_mute(group_id, user_id, 0)
    except OneBotError as e:
        await reply_plain(event, f"❎ {e}")
        return
    await reply_plain(event, "✅ 已将全部禁言解除")


async def cmd_send_notice(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    pieces = []
    removed = False
    for seg in event.get_messages():
        if isinstance(seg, At):
            continue
        if isinstance(seg, Plain) and not removed:
            text = seg.text or ""
            text = re.sub(r"^\s*[-]?\s*发通知", "", text, count=1)
            removed = True
            if text.strip():
                pieces.append(Plain(text=text.strip()))
            continue
        pieces.append(seg)
    if not pieces:
        await reply_plain(event, "❎ 通知不能为空")
        return
    await event.send(event.chain_result([At(qq="all"), Plain(text=" "), *pieces]))


async def cmd_essence(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    onebot = OneBot(event)
    source_id = None
    for seg in event.get_messages():
        if isinstance(seg, Reply):
            source_id = str(seg.id or "")
    if not source_id:
        await reply_plain(event, "请对要加精的消息进行引用")
        return
    text = clean_at_tokens(raw_text(event)).replace(" ", "")
    try:
        if "移精" in text:
            await onebot.delete_essence(source_id)
        else:
            await onebot.set_essence(source_id)
    except OneBotError as e:
        await reply_plain(event, f"❎ {e}")
        return
    await reply_plain(event, "✅ 操作成功")


async def cmd_autistic(plugin, event):
    onebot = OneBot(event)
    group_id = str(event.get_group_id())
    if not await plugin.perm.check(event, "all", "admin", reply=False):
        return
    sender_id = str(event.get_sender_id())
    sender_role = await onebot.role_of(group_id, sender_id)
    if plugin.perm.is_master(sender_id) or (
        sender_role == "admin" and await onebot.role_of(group_id, str(event.get_self_id())) != "owner"
    ):
        await reply_plain(event, "别自闭啦~~")
        return
    rest = strip_command(raw_text(event), "我要自闭", "我要禅定")
    found = re.match(rf"^({NUM_REG})?\s*个?\s*({UNIT_ALT})?$", rest or "")
    num = found.group(1) if found else None
    unit = (found.group(2) if found else None) or "分"
    seconds = parse_time_arg(num or "5", unit) or 300
    try:
        await onebot.set_ban(group_id, sender_id, seconds)
        plugin.record_mute(group_id, sender_id, seconds)
    except OneBotError as e:
        await reply_plain(event, f"❎ {e}")
        return
    await reply_plain(event, "那我就不手下留情了~")


async def cmd_time_mute(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    text = clean_at_tokens(raw_text(event)).replace(" ", "")
    group_id = str(event.get_group_id())
    task_type = "禁言" in text
    if "任务" in text:
        items = plugin.tasks.describe()
        if not items:
            await reply_plain(event, "目前还没有定时禁言任务")
            return
        await plugin.send_list(event, "定时禁言任务", items)
        return
    if "取消" in text:
        await plugin.tasks.delete_task(group_id, task_type)
        await reply_plain(event, f"已取消本群定时{'禁言' if task_type else '解禁'}")
        return

    found = re.search(r"定时(?:禁言|解禁)(.*)$", clean_at_tokens(raw_text(event)))
    cron_text = (found.group(1) if found else "").strip()
    if not cron_text:
        await reply_plain(
            event,
            f'格式不对\n示范：-定时{"禁言" if task_type else "解禁"}00:00 或 -定时{"禁言" if task_type else "解禁"} + cron表达式',
        )
        return
    match = re.match(r"^(\d{1,2})\s*[:：]\s*(\d{1,2})$", cron_text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            await reply_plain(event, "❎ 时间格式不正确")
            return
        cron = hhmm_to_cron(str(hour), str(minute))
    else:
        cron = cron_text
    try:
        ok = await plugin.tasks.set_task(group_id, cron, task_type, str(event.get_self_id()))
    except ValueError as e:
        await reply_plain(event, f"❎ cron表达式错误：{e}")
        return
    if ok:
        await reply_plain(event, "✅设置定时禁言成功，可发【-定时禁言任务】查看")
    else:
        await reply_plain(event, f"❎ 该群定时{'禁言' if task_type else '解禁'}已存在不可重复设置")
