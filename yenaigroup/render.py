# -*- coding: utf-8 -*-
"""列表类消息渲染：优先合并转发，协议端不支持时降级纯文本。"""

from __future__ import annotations

from astrbot.api import logger
from astrbot.api.message_components import Plain


def text_seg(text: str) -> dict:
    return {"type": "text", "data": {"text": str(text)}}


def image_seg(url: str) -> dict:
    return {"type": "image", "data": {"file": str(url)}}


def avatar_url(user_id: str, size: int = 100) -> str:
    return f"https://q1.qlogo.cn/g?b=qq&s={size}&nk={user_id}"


def group_avatar_url(group_id: str, size: int = 100) -> str:
    return f"https://p.qlogo.cn/gh/{group_id}/{group_id}/{size}"


def node(name: str, uin: str, content: list[dict]) -> dict:
    return {
        "type": "node",
        "data": {"name": str(name), "uin": str(uin), "content": content},
    }


def segments_to_text(segments: list[dict]) -> str:
    out = []
    for seg in segments:
        if seg.get("type") == "text":
            out.append(str(seg.get("data", {}).get("text", "")))
        elif seg.get("type") == "image":
            out.append(f"[图片]{seg.get('data', {}).get('file', '')}")
    return "".join(out)


async def reply_plain(event, text: str) -> None:
    await event.send(event.chain_result([Plain(text=text)]))


async def send_list(
    event,
    onebot,
    title: str,
    items: list[list[dict]],
    forward: bool = True,
) -> None:
    """发送列表结果。items 为若干条消息段数组（对应椰奶的合并转发节点）。"""
    group_id = str(event.get_group_id() or "")
    if forward and group_id:
        bot_id = str(event.get_self_id())
        try:
            name = await onebot.member_name(group_id, bot_id) or "群管"
        except Exception:  # noqa: BLE001
            name = "群管"
        nodes = [node(name, bot_id, [text_seg(title)])] if title else []
        for item in items:
            nodes.append(node(name, bot_id, item))
        try:
            await onebot.send_forward(group_id, nodes)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("[yenai群管] 合并转发失败，降级为文本：%s", e)

    lines = [title] if title else []
    for index, item in enumerate(items, 1):
        text = segments_to_text(item).strip()
        if text:
            lines.append(f"{index}、{text}")
    await reply_plain(event, "\n".join(lines))
