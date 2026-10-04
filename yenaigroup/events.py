# -*- coding: utf-8 -*-
"""事件处理：入群/退群/禁言通知、加群申请、入群验证流程、群消息自动处理。"""

from __future__ import annotations

import asyncio

from astrbot.api import logger

from .onebot import OneBot, OneBotError
from .render import reply_plain
from .utils import extract_message_id

ADMIN_ROLES = ("owner", "admin")


def _raw(event) -> dict:
    raw = getattr(event.message_obj, "raw_message", None)
    return raw if isinstance(raw, dict) else {}


def _get(raw: dict, key: str, default=None):
    try:
        value = raw.get(key, default)
    except AttributeError:
        value = getattr(raw, key, default)
    return value


async def handle_notice(plugin, event) -> None:
    """处理 OneBot notice 事件（入群/退群/禁言）。"""
    raw = _raw(event)
    if not raw:
        return
    post_type = _get(raw, "post_type")
    if post_type == "request":
        await _handle_request(plugin, event, raw)
        return
    if post_type != "notice":
        return

    notice_type = str(_get(raw, "notice_type") or "")
    group_id = str(_get(raw, "group_id") or event.get_group_id() or "")
    user_id = str(_get(raw, "user_id") or "")
    if not group_id or not user_id or not plugin.perm.group_allowed(group_id):
        return
    if not await _enabled(plugin, event, group_id):
        return

    if notice_type == "group_increase":
        await _on_increase(plugin, event, group_id, user_id)
    elif notice_type == "group_decrease":
        _on_decrease(plugin, group_id, user_id)
    elif notice_type == "group_ban":
        await _on_ban(plugin, event, group_id, user_id, raw)


async def _enabled(plugin, event, group_id: str) -> bool:
    onebot = OneBot(event)
    try:
        role = await onebot.role_of(group_id, str(event.get_self_id()))
    except OneBotError:
        return False
    return role in ADMIN_ROLES


async def _on_increase(plugin, event, group_id: str, user_id: str) -> None:
    onebot = OneBot(event)
    bot_id = str(event.get_self_id())
    if user_id == bot_id:
        return
    # 黑名单入群直接踢出
    if plugin.perm.is_black(user_id):
        try:
            await onebot.kick(group_id, user_id, reject_add_request=True)
            await plugin.send_group(event, f"⚠ 检测到黑名单{user_id}入群，已自动踢出")
            logger.info("[yenai群管] 已踢出黑名单成员 %s（群 %s）", user_id, group_id)
        except OneBotError as e:
            logger.warning("[yenai群管] 踢出黑名单成员失败：%s", e)
        return
    enabled, reason = verify_enabled(plugin, group_id)
    if not enabled:
        # 明确记一条日志，避免「新成员进群后毫无反应」无从排查
        logger.info(
            "[yenai群管][入群验证] 群 %s 未开启验证（%s），跳过 %s；"
            "在该群发送「-开启验证」可启用，或从 verify_group_black_list 中移除（-验证状态 可查看）",
            group_id,
            reason,
            user_id,
        )
        return
    if plugin.perm.is_master(user_id) or plugin.perm.is_white(user_id):
        return
    delay = int(plugin.conf("verify_delay", 2))
    if delay > 0:
        await asyncio.sleep(delay)
    await start_verify(plugin, event, group_id, user_id)


def _on_decrease(plugin, group_id: str, user_id: str) -> None:
    session = plugin.verify.drop(group_id, user_id)
    if session is None:
        return
    for task in (session.task, session.remind_task):
        if task is not None:
            task.cancel()
    logger.info("[yenai群管] %s 退群，验证流程结束", user_id)


async def _on_ban(plugin, event, group_id: str, user_id: str, raw: dict) -> None:
    """白名单用户被禁言时自动解禁（对应椰奶 noBan）。"""
    if not plugin.conf("auto_unban_white", False):
        return
    if not plugin.perm.is_white(user_id):
        return
    operator_id = str(_get(raw, "operator_id") or "")
    bot_id = str(event.get_self_id())
    if plugin.perm.is_master(operator_id) or operator_id == bot_id:
        return
    duration = _get(raw, "duration") or 0
    if int(duration) == 0:
        return
    onebot = OneBot(event)
    try:
        await onebot.set_ban(group_id, user_id, 0)
        plugin.record_mute(group_id, user_id, 0)
        await plugin.send_group(event, "已解除白名单用户的禁言")
    except OneBotError as e:
        logger.warning("[yenai群管] 白名单自动解禁失败：%s", e)


async def _handle_request(plugin, event, raw: dict) -> None:
    """加群申请：黑名单直接拒绝。"""
    if str(_get(raw, "request_type") or "") != "group":
        return
    if str(_get(raw, "sub_type") or "") != "add":
        return
    group_id = str(_get(raw, "group_id") or "")
    user_id = str(_get(raw, "user_id") or "")
    if not group_id or not user_id or not plugin.perm.is_black(user_id):
        return
    onebot = OneBot(event)
    flag = str(_get(raw, "flag") or "")
    try:
        await onebot.approve_add_request(flag, "add", False, "黑名单用户")
        logger.info("[yenai群管] 已拒绝黑名单 %s 的入群申请（群 %s）", user_id, group_id)
    except OneBotError as e:
        logger.warning("[yenai群管] 拒绝入群申请失败：%s", e)


# ---------------- 入群验证 ----------------

def verify_config(plugin, group_id: str) -> dict:
    data = plugin.store.get(group_id)
    return {
        "mode": data.get("verifyMode") or plugin.conf("verify_mode", "精确"),
        "time": int(data.get("verifyTime") or plugin.conf("verify_time", 300)),
        "times": int(plugin.conf("verify_times", 7)),
        "range_min": int(plugin.conf("verify_range_min", 10)),
        "range_max": int(plugin.conf("verify_range_max", 100)),
        "kind": "letter" if verify_type(plugin, group_id) == "字母验证码" else "math",
        "remind": bool(plugin.conf("verify_remind_last_minute", True)),
    }


def verify_type(plugin, group_id: str) -> str:
    """验证题目类型：优先取本群设置，其次取全局配置。"""
    data = plugin.store.get(group_id)
    return data.get("verifyType") or plugin.conf("verify_type", "算式")


def verify_group_black_list(plugin) -> set[str]:
    """不做入群验证的群号集合。"""
    raw = plugin.conf("verify_group_black_list", []) or []
    return {str(g).strip() for g in raw if str(g).strip()}


def verify_enabled(plugin, group_id: str) -> tuple[bool, str]:
    """本群是否开启入群验证，返回 (是否开启, 判定来源)。

    优先级：群黑名单 > 本群显式开关 > 全局默认。
    默认是「不在黑名单里的群全部开启」，可用「-关闭验证」按群关掉。
    """
    group_id = str(group_id)
    if group_id in verify_group_black_list(plugin):
        return False, "群在 verify_group_black_list 黑名单中"
    if plugin.store.exists(group_id):
        data = plugin.store.get(group_id)
        explicit = data.get("verifyDisabled")
        if explicit is not None:
            return (not bool(explicit)), "本群设置（-开启验证 / -关闭验证）"
        if data.get("verifyEnabled") is True:
            # 兼容旧版本留下的显式开启
            return True, "本群设置"
    default_on = bool(plugin.conf("verify_enabled_default", True))
    source = "全局默认 verify_enabled_default" + ("" if default_on else "（已关闭）")
    return default_on, source


async def start_verify(plugin, event, group_id: str, user_id: str) -> None:
    config = verify_config(plugin, group_id)
    kind = config["kind"]
    session = None
    fallback_note = ""
    if kind == "letter":
        # 字母验证码需要私聊下发，发不出去（非好友被拦截 / 协议端不建临时会话）时回退为算式验证
        probe = plugin.verify.create(
            group_id, user_id, config["times"], config["range_min"], config["range_max"], kind="letter",
        )
        try:
            # 带上 group_id 走群临时会话，非好友也能送达
            await OneBot(event).send_private(
                user_id,
                f"【入群验证】你的验证码是：{probe.code}\n"
                f"请在「{config['time']}」秒内将它发送到群里完成验证（不区分大小写）",
                group_id=group_id,
            )
        except OneBotError as e:
            plugin.verify.drop(group_id, user_id)
            logger.warning("[yenai群管][入群验证] 私发验证码失败，回退算式验证：%s", e)
            kind = "math"
            fallback_note = "（验证码私聊发送失败，已自动改用算式题）\n"
        except Exception as e:  # noqa: BLE001 - 任何意外都不能把用户卡死在验证流程里
            plugin.verify.drop(group_id, user_id)
            logger.warning("[yenai群管][入群验证] 私发验证码异常，回退算式验证：%s", e)
            kind = "math"
            fallback_note = "（验证码私聊发送失败，已自动改用算式题）\n"
        else:
            session = probe
    if session is None:
        session = plugin.verify.create(
            group_id,
            user_id,
            config["times"],
            config["range_min"],
            config["range_max"],
            kind=kind,
        )
    logger.info("[yenai群管][入群验证] 答案：%s（群 %s / %s）", session.code, group_id, user_id)

    session.task = plugin.spawn(_verify_timeout(plugin, event, group_id, user_id, config["time"]))
    if config["remind"] and config["time"] >= 120:
        session.remind_task = plugin.spawn(
            _verify_remind(plugin, event, group_id, user_id, config["time"]),
        )
    if session.kind == "letter":
        await plugin.send_group(
            event,
            f" 欢迎！\n验证码已私聊发送给你\n请在「{config['time']}」秒内\n"
            f"将验证码发到本群（不区分大小写）\n否则将会被移出群聊",
            at=user_id,
        )
    else:
        await plugin.send_group(
            event,
            f" 欢迎！\n{fallback_note}请在「{config['time']}」秒内发送\n"
            f"「{session.question}」的运算结果\n否则将会被移出群聊",
            at=user_id,
        )


async def _verify_timeout(plugin, event, group_id: str, user_id: str, timeout: int) -> None:
    try:
        await asyncio.sleep(timeout)
    except asyncio.CancelledError:
        return
    if plugin.verify.drop(group_id, user_id) is None:
        return
    await plugin.send_group(event, "\n验证超时，移出群聊，请重新申请", at=user_id)
    onebot = OneBot(event)
    try:
        await onebot.kick(group_id, user_id)
    except OneBotError as e:
        logger.warning("[yenai群管][入群验证] 超时踢出失败：%s", e)


async def _verify_remind(plugin, event, group_id: str, user_id: str, timeout: int) -> None:
    try:
        await asyncio.sleep(max(1, timeout - 60))
    except asyncio.CancelledError:
        return
    session = plugin.verify.find(group_id, user_id)
    if session is None:
        return
    if session.kind == "letter":
        text = " \n验证仅剩最后一分钟\n请发送私聊收到的验证码\n否则将会被移出群聊"
    else:
        text = (
            f" \n验证仅剩最后一分钟\n请发送「{session.question}」的运算结果\n否则将会被移出群聊"
        )
    await plugin.send_group(event, text, at=user_id)


async def handle_verify_answer(plugin, event) -> bool:
    """拦截验证用户的答案，返回是否已消费该消息。"""
    group_id = str(event.get_group_id() or "")
    if not group_id:
        return False
    sender_id = str(event.get_sender_id())
    session = plugin.verify.find(group_id, sender_id)
    if session is None:
        return False
    config = verify_config(plugin, group_id)
    ok, session = plugin.verify.check(
        group_id,
        sender_id,
        event.get_message_str(),
        config["mode"],
    )
    if session is None:
        return False
    if ok:
        plugin.verify.drop(group_id, sender_id)
        for task in (session.task, session.remind_task):
            if task is not None:
                task.cancel()
        await plugin.send_group(event, plugin.verify_success_msg(group_id))
        return True

    remain = plugin.verify.consume_failure(group_id, sender_id)
    if remain > 0:
        onebot = OneBot(event)
        message_id = extract_message_id(event)
        if message_id:
            try:
                await onebot.recall(message_id)
            except OneBotError:
                pass
        if session.kind == "letter":
            hint = "请发送私聊收到的验证码（不区分大小写）"
        else:
            hint = f"请发送「{session.question}」的运算结果"
        await plugin.send_group(
            event,
            f"\n❎ 验证失败\n你还有「{remain}」次机会\n{hint}",
            at=sender_id,
        )
        return True

    plugin.verify.drop(group_id, sender_id)
    for task in (session.task, session.remind_task):
        if task is not None:
            task.cancel()
    await plugin.send_group(event, "\n验证失败，请重新申请", at=sender_id)
    onebot = OneBot(event)
    try:
        await onebot.kick(group_id, sender_id)
    except OneBotError as e:
        logger.warning("[yenai群管][入群验证] 验证失败踢出异常：%s", e)
    return True


# ---------------- 群消息自动处理 ----------------

async def handle_blacklist_message(plugin, event) -> None:
    """黑名单用户在群里发言即踢出（对应椰奶 GroupWhiteListCtrl.accept）。"""
    if not plugin.conf("enabled", True):
        return
    group_id = str(event.get_group_id() or "")
    if not group_id or not plugin.perm.group_allowed(group_id):
        return
    sender_id = str(event.get_sender_id())
    if not plugin.perm.is_black(sender_id):
        return
    onebot = OneBot(event)
    if await onebot.role_of(group_id, str(event.get_self_id())) not in ADMIN_ROLES:
        return
    try:
        await onebot.kick(group_id, sender_id)
        await reply_plain(event, f"⚠ 已踢出黑名单用户{sender_id}")
    except OneBotError as e:
        logger.warning("[yenai群管] 踢出黑名单用户失败：%s", e)
