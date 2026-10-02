# -*- coding: utf-8 -*-
"""通用工具：时间单位、中文数字、消息解析（对齐椰奶插件的实现）。"""

from __future__ import annotations

import re

from astrbot.api.message_components import At, Plain

# 与椰奶一致的匹配用正则片段
NUM_REG = r"[零一壹二两三四五六七八九十百千万亿\d]+"

# 时间单位表，与椰奶 constants/other.js 的 Time_unit 完全一致
TIME_UNIT: dict[str, float] = {
    "毫秒": 0.001,
    "秒": 1,
    "S": 1,
    "SECOND": 1,
    "分": 60,
    "分钟": 60,
    "M": 60,
    "MIN": 60,
    "MINUTE": 60,
    "时": 3600,
    "小时": 3600,
    "H": 3600,
    "HOUR": 3600,
    "天": 86400,
    "日": 86400,
    "D": 86400,
    "DAY": 86400,
    "周": 604800,
    "W": 604800,
    "WEEK": 604800,
    "月": 2592000,
    "MONTH": 2592000,
    "年": 31536000,
    "Y": 31536000,
    "YEAR": 31536000,
}

# 多字单位排在前面，避免 “分钟” 被 “分” 抢先匹配
UNIT_ALT = "|".join(
    sorted((re.escape(k) for k in TIME_UNIT), key=len, reverse=True),
)

_DIGITS_RE = re.compile(r"^\d+$")
_AT_TOKEN_RE = re.compile(r"@[^\s@]*\(\d+\)")

_CN_MAP = {
    "一": 1,
    "壹": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_CHARS_RE = re.compile("[" + "".join(_CN_MAP) + "]")


def translate_china_num(value: str | int | None):
    """把中文数字转成阿拉伯数字，行为对齐椰奶 tools/translateChinaNum.js。"""
    if value is None:
        return value
    text = str(value).strip()
    if text == "":
        return value
    if _DIGITS_RE.fullmatch(text):
        return int(text)

    split = text.split("亿")
    s_1_23 = split if len(split) > 1 else ["", text]
    s_23 = s_1_23[1]
    s_1 = s_1_23[0]
    split = s_23.split("万")
    s_2_3 = split if len(split) > 1 else ["", s_23]
    s_2 = s_2_3[0]
    s_3 = s_2_3[1]

    result_parts = []
    for item in (s_1, s_2, s_3):
        temp = item.replace("零", "")
        temp = _CN_CHARS_RE.sub(lambda m: str(_CN_MAP[m.group(0)]), temp)

        def _pick(pattern: str, default: str) -> str:
            found = re.search(pattern, temp)
            return found.group(0) if found else default

        num1 = _pick(r"\d(?=千)", "0")
        num2 = _pick(r"\d(?=百)", "0")
        found = re.search(r"\d?(?=十)", temp)
        if found is None:
            num3 = "0"
        elif found.group(0) == "":
            num3 = "1"
        else:
            num3 = found.group(0)
        num4 = _pick(r"\d$", "0")
        result_parts.append(num1 + num2 + num3 + num4)

    digits = "".join(result_parts)
    if not digits:
        return float("nan")
    return int(digits)


def unit_multiplier(unit: str | None) -> int | float:
    """把时间单位换算成秒的倍数，未识别时与椰奶一致回退到 60（分）。"""
    if unit is None or str(unit).strip() == "":
        # 椰奶 muteMember 的 unit 默认值是 “秒”
        return 1
    text = str(unit).strip()
    if text in TIME_UNIT:
        return TIME_UNIT[text]
    if text.upper() in TIME_UNIT:
        return TIME_UNIT[text.upper()]
    if _DIGITS_RE.fullmatch(text):
        return int(text)
    return 60


def parse_time_arg(num: str | None, unit: str | None) -> int | None:
    """解析 “数值 + 单位” 为秒数；未提供数值时返回 None。"""
    if num is None or str(num).strip() == "":
        return None
    value = translate_china_num(str(num).strip())
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    seconds = value * unit_multiplier(unit)
    return int(seconds)


def format_duration(seconds: float) -> str:
    """格式化时长为 “1天02小时03分04秒”，对齐椰奶 formatDuration 默认格式。"""
    total = int(seconds)
    if total < 0:
        total = 0
    day, rem = divmod(total, 86400)
    hour, rem = divmod(rem, 3600)
    minute, second = divmod(rem, 60)
    out = ""
    if day > 0:
        out += f"{day}天"
    if hour > 0:
        out += f"{hour:02d}小时"
    if minute > 0:
        out += f"{minute:02d}分"
    if second > 0:
        out += f"{second:02d}秒"
    return out


def clean_at_tokens(text: str) -> str:
    """去掉适配器写入 message_str 的 “@昵称(12345)” 文本。"""
    return re.sub(r"\s+", " ", _AT_TOKEN_RE.sub(" ", text or "")).strip()


def plain_text(event) -> str:
    """仅由 Plain 段拼出的纯文本（不含 @ 与图片）。"""
    parts = []
    for seg in event.get_messages():
        if isinstance(seg, Plain):
            parts.append(seg.text or "")
    return "".join(parts).strip()


def raw_text(event) -> str:
    """原始消息文本（含 @ 显示文本），用于违禁词匹配。"""
    return (event.get_message_str() or "").strip()


def has_wake_prefix(event, prefixes: list[str] | None = None) -> bool:
    """判断本条消息是否由全局触发词（默认 “-”）唤起。

    允许先 @ 机器人再使用触发词（“@bot -禁言 ...”）的写法。
    """
    prefixes = prefixes or ["-"]
    for seg in event.get_messages():
        if isinstance(seg, At):
            # 只有 @ 机器人本身才允许跳过，@ 其他人不算触发词开头
            if str(seg.qq) == str(event.get_self_id()):
                continue
            return False
        if isinstance(seg, Plain):
            text = (seg.text or "").lstrip()
            if not text:
                continue
            return any(text.startswith(p) for p in prefixes if p)
    return False


def at_targets(event, bot_id: str | None = None) -> list[str]:
    """取出被 @ 的用户，过滤 @全体成员 与机器人自身，保持顺序去重。"""
    out: list[str] = []
    for seg in event.get_messages():
        if not isinstance(seg, At):
            continue
        qq = str(seg.qq).strip()
        if not qq or qq == "all":
            continue
        if bot_id and qq == str(bot_id):
            continue
        if qq not in out:
            out.append(qq)
    return out


def extract_message_id(event) -> str:
    """取当前消息的 message_id（字符串）。"""
    return str(getattr(event.message_obj, "message_id", "") or "")


def chunked(items: list, size: int) -> list[list]:
    if size <= 0:
        size = 30
    return [items[i : i + size] for i in range(0, len(items), size)]
