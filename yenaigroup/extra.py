# -*- coding: utf-8 -*-
"""黑白名单 / 投票 / 入群验证命令 / 群公告 / 加群通知 / 帮助。"""

from __future__ import annotations

import asyncio
import re

from astrbot.api.message_components import At, Plain

from .onebot import OneBot, OneBotError
from .render import reply_plain
from .utils import at_targets, clean_at_tokens, raw_text

HELP_TEXT = """椰奶群管 · 指令一览（全局触发词 -）

【基础群管】
-禁言 @某人 [时长][单位]    例：-禁言 @某人 10分 / -禁言 123456 1时
-解禁 @某人               解除禁言
-全体禁言 / -全员解禁       开关全体禁言
-踢人 @某人 / -踢黑 @某人    踢出群聊（踢黑同时拉黑）
-设置管理 @某人 / -取消管理 @某人
-修改头衔 @某人 内容 / -申请头衔 内容
-禁言列表 / -解除全部禁言
-发通知 内容 / -加精（引用消息）/ -移精
-我要自闭 [时长][单位]

【清理与排行】
-查看从未发言过的人 / -清理从未发言的人 / -确认清理
-查看 3天没发言的人 / -清理 3天没发言的人
-不活跃排行榜 [数量] / -查看最近的入群记录 [数量]

【定时】
-定时禁言 00:00 / -定时解禁 0 0 8 * * ? / -定时禁言任务 / -取消定时禁言

【违禁词】
-新增(模糊|精确|正则1|正则2)?(踢|禁|撤|踢撤|禁撤|踢黑)?违禁词 内容
-删除违禁词 内容 / -查看违禁词 内容 / -违禁词列表 / -设置违禁词禁言时间 秒
-增加头衔屏蔽词 词1,词2 / -减少头衔屏蔽词 词1 / -查看头衔屏蔽词 / -切换头衔屏蔽词匹配模式
-违禁词帮助

【黑白名单】
-群管加白名单 @某人 / -群管删白名单 @某人
-群管加黑名单 @某人 / -群管删黑名单 @某人
-开启白名单自动解禁 / -关闭白名单自动解禁

【投票】
-发起投票禁言 @某人 / -发起投票踢人 @某人
-支持投票 @某人 / -反对投票 @某人
-启用投票禁言 / -禁用投票踢人 / -投票设置超时时间 180

【入群验证】
-开启验证 / -关闭验证 / -切换验证模式 / -切换验证类型 / -设置验证超时时间 300
-重新验证 @某人 / -绕过验证 @某人 / -重新验证从未发言的人

【群公告与通知】
-发群公告 内容 / -查群公告 / -删群公告 序号
-开启加群通知 / -关闭加群通知"""


# ---------------- 黑白名单 ----------------

async def cmd_list_edit(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    text = clean_at_tokens(raw_text(event))
    operation = "add" if "加" in text else "del"
    key = "black_qq" if "黑" in text else "white_qq"
    type_name = "黑" if "黑" in text else "白"
    targets = at_targets(event, event.get_self_id())
    if not targets:
        found = re.search(r"(\d+)", text)
        if found:
            targets = [found.group(1)]
    if not targets:
        await reply_plain(event, f"❎ 请艾特或输入需要加{type_name}的QQ")
        return
    current = plugin.qq_list(key)
    qq = targets[0]
    if qq in current and operation == "add":
        await reply_plain(event, f"❎ 此人已在群管{type_name}名单内")
        return
    if qq not in current and operation == "del":
        await reply_plain(event, f"❎ 此人未在群管{type_name}名单中")
        return
    if operation == "add":
        plugin.add_to_list(key, [qq])
    else:
        plugin.remove_from_list(key, [qq])
    await reply_plain(
        event,
        f"✅ 群管{type_name}名单已{'加入' if operation == 'add' else '删除'}{qq}",
    )


async def cmd_toggle_no_ban(plugin, event):
    if not await plugin.perm.check(event, "master"):
        return
    enable = "开启" in clean_at_tokens(raw_text(event))
    current = bool(plugin.conf("auto_unban_white", False))
    if current and enable:
        await reply_plain(event, "❎ 白名单自动解禁已处于开启状态")
        return
    if not current and not enable:
        await reply_plain(event, "❎ 白名单自动解禁已处于关闭状态")
        return
    if not plugin.set_conf("auto_unban_white", enable):
        await reply_plain(event, "❎ 配置保存失败，请查看 AstrBot 日志")
        return
    await reply_plain(event, f"✅ 已{'开启' if enable else '关闭'}白名单自动解禁")


# ---------------- 投票 ----------------

async def cmd_vote_start(plugin, event):
    if not await plugin.perm.check(event, "all", "admin"):
        return
    text = clean_at_tokens(raw_text(event))
    is_ban = "禁言" in text
    if is_ban and not plugin.conf("vote_ban_enabled", True):
        await reply_plain(event, "❎ 该功能已被禁用，请发送 -启用投票禁言 来启用该功能。")
        return
    if not is_ban and not plugin.conf("vote_kick_enabled", False):
        await reply_plain(event, "❎ 该功能已被禁用，请发送 -启用投票踢人 来启用该功能。")
        return

    group_id = str(event.get_group_id())
    targets = at_targets(event, event.get_self_id())
    if not targets:
        found = re.search(r"(\d{5,})", text)
        if found:
            targets = [found.group(0)]
    if not targets:
        await reply_plain(event, "❎ 请艾特或输入被投票人的QQ")
        return
    target = targets[0]
    sender_id = str(event.get_sender_id())
    if sender_id == target:
        await reply_plain(event, "❎ 您不能对自己进行投票")
        return
    if plugin.perm.is_master(target):
        await reply_plain(event, "❎ 该命令对主人无效")
        return
    if plugin.votes.exists(group_id, target):
        await reply_plain(event, "❎ 已有相同投票，请勿重复发起")
        return

    onebot = OneBot(event)
    info = await onebot.member_info(group_id, target)
    if not info:
        await reply_plain(event, "❎ 该群没有这个人")
        return
    role = str(info.get("role") or "member")
    if role == "owner":
        await reply_plain(event, "❎ 权限不足，该命令对群主无效")
        return
    if role == "admin":
        vote_admin = bool(plugin.conf("vote_admin", False))
        bot_role = await onebot.role_of(group_id, str(event.get_self_id()))
        if not vote_admin or bot_role != "owner":
            await reply_plain(event, "❎ 该命令对管理员无效或Bot权限不足，需要群主权限")
            return

    out_time = int(plugin.conf("vote_out_time", 180))
    min_num = int(plugin.conf("vote_min_num", 4))
    ban_time = int(plugin.conf("vote_ban_time", 3600))
    veto = bool(plugin.conf("vote_veto", True))
    plugin.votes.create(group_id, target, "Ban" if is_ban else "Kick", sender_id)

    lines = [
        f"({target})的{'禁言' if is_ban else '踢出'}投票已发起\n",
        f"发起人：{sender_id}\n",
        "请支持者发送：\n",
        f"「-支持投票{target}」\n",
        "不支持者请发送：\n",
        f"「-反对投票{target}」\n",
        f"超时时间：{out_time}秒\n",
        f"禁言时间：{ban_time}秒\n" if is_ban else "投票成功将会被移出群聊\n",
        f"规则：支持票大于反对票且参与人高于{min_num}人即可成功投票",
        "\n管理员拥有一票权" if veto else "",
    ]
    await event.send(event.chain_result([At(qq=target), Plain(text="".join(lines))]))
    plugin.spawn(
        _vote_timer(plugin, event, group_id, target, out_time, min_num, ban_time, is_ban),
    )


async def _vote_timer(plugin, event, group_id, target, out_time, min_num, ban_time, is_ban):
    try:
        if out_time > 60:
            await asyncio.sleep(out_time - 60)
            state = plugin.votes.get(group_id, target)
            if state is not None:
                await plugin.send_group(
                    event,
                    f"{target} 的{'禁言' if is_ban else '踢出'}投票仅剩一分钟结束\n"
                    f"当前票数：\n支持票数：{state.support}\n反对票数：{state.oppose}\n"
                    f"发起人：{state.initiator}",
                )
            await asyncio.sleep(60)
        else:
            await asyncio.sleep(out_time)
    except asyncio.CancelledError:
        return

    success, state = plugin.votes.settle(group_id, target, min_num)
    if state is None:
        return
    msg = (
        "投票结束，投票结果：\n"
        f"支持票数：{state.support}\n反对票数：{state.oppose}\n"
    )
    if success:
        msg += "支持票数大于反对票\n投票成功。" + ("禁言目标" if is_ban else "踢出目标")
        onebot = OneBot(event)
        try:
            if is_ban:
                await onebot.set_ban(group_id, target, ban_time)
                plugin.record_mute(group_id, target, ban_time)
            else:
                await onebot.kick(group_id, target)
        except OneBotError as e:
            msg += f"\n执行失败：{e}"
    else:
        msg += f"反对票数大于支持票数或支持票数小于{min_num}，投票失败。"
    await plugin.send_group(event, msg)


async def cmd_vote_follow(plugin, event):
    if not await plugin.perm.check(event, "all", "admin"):
        return
    text = clean_at_tokens(raw_text(event))
    support = "支持" in text
    group_id = str(event.get_group_id())
    targets = at_targets(event, event.get_self_id())
    if not targets:
        found = re.search(r"(\d{5,})", text)
        if found:
            targets = [found.group(0)]
    if not targets:
        await reply_plain(event, "❎ 请艾特或输入需要进行跟票的QQ")
        return
    target = targets[0]
    sender_id = str(event.get_sender_id())
    if sender_id == target:
        await reply_plain(event, "❎ 您不能对自己进行投票")
        return
    if plugin.perm.is_master(target):
        await reply_plain(event, "❎ 该命令对主人无效")
        return
    state = plugin.votes.get(group_id, target)
    if state is None:
        await reply_plain(event, "❎ 未找到对应投票")
        return

    onebot = OneBot(event)
    if plugin.conf("vote_veto", True):
        sender_role = await onebot.role_of(group_id, sender_id)
        if sender_role in ("admin", "owner"):
            state = plugin.votes.drop(group_id, target)
            if support:
                await reply_plain(event, "投票结束，管理员介入，执行操作。")
                ban_time = int(plugin.conf("vote_ban_time", 3600))
                try:
                    if state and state.type == "Ban":
                        await onebot.set_ban(group_id, target, ban_time)
                        plugin.record_mute(group_id, target, ban_time)
                    else:
                        await onebot.kick(group_id, target)
                except OneBotError as e:
                    await reply_plain(event, f"❎ 执行失败：{e}")
            else:
                await reply_plain(event, "投票取消，管理员介入。")
            return

    code, state = plugin.votes.follow(group_id, target, sender_id, support)
    if code == "no_vote":
        await reply_plain(event, "❎ 未找到对应投票")
        return
    if code == "repeated":
        await reply_plain(event, "❎ 你已参与过投票，请勿重复参与")
        return
    await reply_plain(
        event,
        f"投票成功，当前票数\n支持：{state.support} 反对：{state.oppose}",
    )


async def cmd_vote_switch(plugin, event):
    if not await plugin.perm.check(event, "master"):
        return
    text = clean_at_tokens(raw_text(event))
    enable = "启用" in text
    is_ban = "禁言" in text
    key = "vote_ban_enabled" if is_ban else "vote_kick_enabled"
    name = "禁言" if is_ban else "踢人"
    current = bool(plugin.conf(key, is_ban))
    if current and enable:
        await reply_plain(event, f"❎ 投票{name}功能已处于启用状态")
        return
    if not current and not enable:
        await reply_plain(event, f"❎ 投票{name}功能已处于禁用状态")
        return
    if not plugin.set_conf(key, enable):
        await reply_plain(event, "❎ 配置保存失败，请查看 AstrBot 日志")
        return
    await reply_plain(event, f"✅ 已{'启用' if enable else '禁用'}投票{name}功能")


async def cmd_vote_settings(plugin, event):
    if not await plugin.perm.check(event, "master"):
        return
    text = clean_at_tokens(raw_text(event))
    found = re.fullmatch(r"投票设置(超时时间|最低票数|禁言时间)(\d+)", text)
    if not found:
        await reply_plain(
            event,
            "投票配置参数:\n\n-启用/禁用投票禁言|踢人\n\n"
            "超时时间: 投票限时，单位:秒\n最低票数: 投票成功的最低票数\n"
            "禁言时间: 禁言的时长，单位:秒\n\n例: -投票设置禁言时间8600",
        )
        return
    name = found.group(1)
    value = int(found.group(2))
    key = {
        "超时时间": "vote_out_time",
        "最低票数": "vote_min_num",
        "禁言时间": "vote_ban_time",
    }[name]
    if int(plugin.conf(key, 0)) == value:
        await reply_plain(event, f"❎ 当前{name}已经是{value}了")
        return
    if not plugin.set_conf(key, value):
        await reply_plain(event, "❎ 配置保存失败，请查看 AstrBot 日志")
        return
    await reply_plain(event, f"✅ 已把{name}设置成{value}了")


# ---------------- 入群验证命令 ----------------

def _group_of(event, plugin):
    group_id = str(event.get_group_id())
    return group_id, plugin.store.get(group_id)


async def cmd_verify_toggle(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    group_id, data = _group_of(event, plugin)
    enable = "开启" in clean_at_tokens(raw_text(event))
    current = bool(data.get("verifyEnabled"))
    if current and enable:
        await reply_plain(event, "❎ 本群验证已处于开启状态")
        return
    if not current and not enable:
        await reply_plain(event, "❎ 本群暂未开启验证")
        return
    data["verifyEnabled"] = enable
    await plugin.store.save(group_id)
    await reply_plain(event, f"✅ 已{'开启' if enable else '关闭'}本群验证")


async def cmd_verify_mode(plugin, event):
    if not await plugin.perm.check(event, "master"):
        return
    group_id, data = _group_of(event, plugin)
    current = data.get("verifyMode") or plugin.conf("verify_mode", "精确")
    value = "精确" if current == "模糊" else "模糊"
    data["verifyMode"] = value
    await plugin.store.save(group_id)
    await reply_plain(event, f"✅ 已切换验证模式为{value}验证")


async def cmd_verify_type(plugin, event):
    if not await plugin.perm.check(event, "master"):
        return
    from .events import verify_type

    group_id, data = _group_of(event, plugin)
    current = verify_type(plugin, group_id)
    value = "字母验证码" if current == "算式" else "算式"
    data["verifyType"] = value
    await plugin.store.save(group_id)
    if value == "字母验证码":
        await reply_plain(
            event,
            "✅ 已切换验证类型为字母验证码\n验证码将私聊下发，发送失败时自动回退算式验证",
        )
    else:
        await reply_plain(event, "✅ 已切换验证类型为算式验证")


async def cmd_verify_time(plugin, event):
    if not await plugin.perm.check(event, "master"):
        return
    found = re.search(r"(\d+)", clean_at_tokens(raw_text(event)))
    if not found:
        await reply_plain(event, "❎ 请输入超时时间（秒）")
        return
    group_id, data = _group_of(event, plugin)
    seconds = int(found.group(1))
    data["verifyTime"] = seconds
    await plugin.store.save(group_id)
    await reply_plain(event, f"✅ 已将验证超时时间设置为{seconds}秒")
    if seconds < 60:
        await reply_plain(event, "建议至少一分钟(60秒)哦ε(*´･ω･)з")


async def cmd_verify_again(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    from .events import start_verify

    group_id, data = _group_of(event, plugin)
    if not data.get("verifyEnabled"):
        await reply_plain(event, "当前群未开启验证哦~")
        return
    targets = at_targets(event, event.get_self_id())
    if not targets:
        found = re.search(r"(\d{5,})", clean_at_tokens(raw_text(event)))
        if found:
            targets = [found.group(0)]
    if not targets:
        await reply_plain(event, "❎ 请艾特要重新验证的人")
        return
    target = targets[0]
    if plugin.perm.is_master(target):
        await reply_plain(event, "❎ 该命令对机器人主人无效")
        return
    onebot = OneBot(event)
    info = await onebot.member_info(group_id, target)
    if not info:
        await reply_plain(event, "❎ 目标群成员不存在")
        return
    if str(info.get("role") or "member") in ("owner", "admin"):
        await reply_plain(event, "❎ 该命令对群主或管理员无效")
        return
    if plugin.verify.find(group_id, target):
        await reply_plain(event, "❎ 目标群成员处于验证状态")
        return
    await start_verify(plugin, event, group_id, target)


async def cmd_verify_bypass(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    group_id, data = _group_of(event, plugin)
    if not data.get("verifyEnabled"):
        await reply_plain(event, "当前群未开启验证哦~")
        return
    targets = at_targets(event, event.get_self_id())
    if not targets:
        found = re.search(r"(\d{5,})", clean_at_tokens(raw_text(event)))
        if found:
            targets = [found.group(0)]
    if not targets:
        await reply_plain(event, "❎ 请输入正确的QQ号")
        return
    target = targets[0]
    session = plugin.verify.drop(group_id, target)
    if session is None:
        await reply_plain(event, "❎ 目标群成员当前无需验证")
        return
    for task in (session.task, session.remind_task):
        if task is not None:
            task.cancel()
    await reply_plain(event, plugin.verify_success_msg(group_id))


async def cmd_verify_never_speak(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    from .clean import _members, _never_speak_list
    from .events import start_verify

    onebot = OneBot(event)
    members = await _members(plugin, event, onebot)
    if members is None:
        return
    group_id = str(event.get_group_id())
    targets = _never_speak_list(members, str(event.get_self_id()))
    if not targets:
        await reply_plain(event, "✅ 本群暂无从未发言的人")
        return
    for member in targets:
        await start_verify(plugin, event, group_id, str(member.get("user_id")))
        await asyncio.sleep(2)


# ---------------- 群公告 / 加群通知 ----------------

async def cmd_announce_add(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    content = re.sub(r"^[-]?\s*发群?公告", "", clean_at_tokens(raw_text(event))).strip()
    if not content:
        await reply_plain(event, "❎ 公告不能为空")
        return
    onebot = OneBot(event)
    try:
        await onebot.send_notice(str(event.get_group_id()), content)
    except OneBotError as e:
        await reply_plain(event, f"❎ 发送失败\n{e}")


async def cmd_announce_list(plugin, event):
    onebot = OneBot(event)
    try:
        notices = await onebot.get_notice(str(event.get_group_id()))
    except OneBotError as e:
        await reply_plain(event, f"❎ {e}")
        return
    if not notices:
        await reply_plain(event, "❎ 本群暂无群公告")
        return
    lines = []
    for index, notice in enumerate(notices, 1):
        content = notice.get("message") or notice.get("content") or ""
        if isinstance(content, list):
            text = "".join(
                str(seg.get("data", {}).get("text", ""))
                for seg in content
                if isinstance(seg, dict) and seg.get("type") == "text"
            )
        else:
            text = str(content)
        lines.append(f"{index}. {text}")
    await reply_plain(event, "\n".join(lines))


async def cmd_announce_del(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    found = re.search(r"(\d+)", clean_at_tokens(raw_text(event)))
    if not found:
        await reply_plain(event, "❎ 序号不可为空")
        return
    index = int(found.group(1))
    onebot = OneBot(event)
    try:
        notices = await onebot.get_notice(str(event.get_group_id()))
    except OneBotError as e:
        await reply_plain(event, f"❎ {e}")
        return
    if index < 1 or index > len(notices):
        await reply_plain(event, "❎ 序号超出范围")
        return
    notice = notices[index - 1]
    notice_id = str(notice.get("notice_id") or notice.get("id") or "")
    if not notice_id:
        await reply_plain(event, "❎ 未获取到公告ID")
        return
    try:
        await onebot.del_notice(str(event.get_group_id()), notice_id)
    except OneBotError as e:
        await reply_plain(event, f"❎ 删除失败\n{e}")
        return
    content = notice.get("message") or notice.get("content") or ""
    await reply_plain(event, f"✅ 已删除「{content}」")


async def cmd_toggle_add_notice(plugin, event):
    if not await plugin.perm.check(event, "admin", "admin"):
        return
    group_id, data = _group_of(event, plugin)
    enable = "开启" in clean_at_tokens(raw_text(event))
    current = bool(data.get("groupAddNotice"))
    if current and enable:
        await reply_plain(event, "❎ 本群加群申请通知已处于开启状态")
        return
    if not current and not enable:
        await reply_plain(event, "❎ 本群暂未开启加群申请通知")
        return
    data["groupAddNotice"] = enable
    await plugin.store.save(group_id)
    await reply_plain(
        event,
        f"✅ 已{'开启' if enable else '关闭'}「{group_id}」的加群申请通知",
    )


async def cmd_help(plugin, event):
    await reply_plain(event, HELP_TEXT)
