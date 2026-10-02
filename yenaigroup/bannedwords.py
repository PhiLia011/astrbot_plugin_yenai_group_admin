# -*- coding: utf-8 -*-
"""违禁词模型，对齐椰奶 model/GroupBannedWords.js。"""

from __future__ import annotations

import re
from datetime import datetime

MATCH_TYPE_MAP = {1: "精确", 2: "模糊", 3: "正则"}
MATCH_TYPE_INDEX = {v: k for k, v in MATCH_TYPE_MAP.items()}
# 命令里可用 “正则1/正则2” 写法，存储时统一成 “正则”
REGEX_ALIASES = ("正则1", "正则2", "正则")

# 6 = 踢黑（椰奶文档支持但实现缺失，这里补齐）
PENALTY_TYPE_MAP = {1: "踢", 2: "禁", 3: "撤", 4: "踢撤", 5: "禁撤", 6: "踢黑"}
PENALTY_TYPE_INDEX = {v: k for k, v in PENALTY_TYPE_MAP.items()}

PENALTY_ACTION_TEXT = {
    1: "踢出群聊",
    2: "禁言",
    3: "撤回消息",
    4: "踢出群聊并撤回消息",
    5: "禁言并撤回消息",
    6: "踢出群聊并加入黑名单",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_match_type(match_type: str) -> str:
    """把命令输入的匹配模式归一化成 精确/模糊/正则。"""
    if match_type in REGEX_ALIASES:
        return "正则"
    if match_type not in MATCH_TYPE_INDEX:
        return "精确"
    return match_type


class ReplyError(Exception):
    """需要直接回复给用户的错误。"""


class BannedWords:
    """按群维护的违禁词表。"""

    def __init__(self, store, plugin):
        self.store = store
        self.plugin = plugin
        self._cache: dict[str, list[tuple[re.Pattern, dict]]] = {}

    # ---------------- 增删查 ----------------

    def add(
        self,
        group_id: str,
        words: str,
        match_type: str = "精确",
        penalty_type: str = "禁",
        added_by: str = "",
    ) -> dict:
        data = self.store.get(group_id)
        table = data.setdefault("bannedWords", {})
        if words in table:
            raise ReplyError(f"❎ 违禁词{words}已存在")
        raw_match_type = match_type
        raw_penalty_type = penalty_type
        match_type = normalize_match_type(match_type)
        if penalty_type not in PENALTY_TYPE_INDEX:
            penalty_type = "禁"
        table[words] = {
            "matchType": MATCH_TYPE_INDEX[match_type],
            "penaltyType": PENALTY_TYPE_INDEX[penalty_type],
            "date": _now(),
            "addedBy": str(added_by),
        }
        self._cache.pop(str(group_id), None)
        # 回复里保留用户输入的模式名（例如 “正则1”），与椰奶一致
        return {
            "words": words,
            "matchType": raw_match_type,
            "penaltyType": raw_penalty_type,
        }

    def delete(self, group_id: str, words: str) -> str:
        data = self.store.get(group_id)
        table = data.setdefault("bannedWords", {})
        if words not in table:
            raise ReplyError(f"❎ 违禁词{words}不存在")
        del table[words]
        self._cache.pop(str(group_id), None)
        return words

    def query(self, group_id: str, words: str) -> dict:
        item = self.store.banned_words(group_id).get(words)
        if not item:
            raise ReplyError(f"❎ 违禁词{words}不存在")
        return {
            **item,
            "words": words,
            "matchTypeText": MATCH_TYPE_MAP.get(item.get("matchType"), "未知"),
            "penaltyTypeText": PENALTY_TYPE_MAP.get(item.get("penaltyType"), "未知"),
        }

    def list_raw(self, group_id: str) -> dict:
        return self.store.banned_words(group_id)

    def set_mute_time(self, group_id: str, seconds: int) -> None:
        self.store.get(group_id)["muteTime"] = int(seconds)

    def mute_time(self, group_id: str) -> int:
        return self.store.mute_time(group_id)

    # ---------------- 匹配 ----------------

    def _compile(self, group_id: str) -> list[tuple[re.Pattern, dict]]:
        key = str(group_id)
        if key in self._cache:
            return self._cache[key]
        compiled: list[tuple[re.Pattern, dict]] = []
        for word, raw_item in self.store.banned_words(group_id).items():
            item = dict(raw_item)
            item["rawItem"] = word
            try:
                if item.get("matchType") == 2:
                    compiled.append((re.compile(re.escape(word)), item))
                elif item.get("matchType") == 3:
                    compiled.append((re.compile(word), item))
                else:
                    compiled.append((re.compile(f"^{re.escape(word)}$"), item))
            except re.error:
                continue
        self._cache[key] = compiled
        return compiled

    def match(self, group_id: str, text: str) -> dict | None:
        for pattern, item in self._compile(group_id):
            if pattern.search(text):
                return item
        return None

    # ---------------- 头衔屏蔽词 ----------------

    def title_words(self, group_id: str) -> list:
        return list(self.store.title_banned_words(group_id) or [])

    def add_title_words(self, group_id: str, words: list[str]) -> None:
        current = self.store.get(group_id).setdefault("TitleBannedWords", [])
        current.extend(words)

    def del_title_words(self, group_id: str, words: list[str]) -> None:
        data = self.store.get(group_id)
        current = data.setdefault("TitleBannedWords", [])
        data["TitleBannedWords"] = [w for w in current if w not in words]

    def title_filter_exact(self, group_id: str) -> bool:
        return bool(self.store.get(group_id).get("TitleFilterModeChange") or 0)

    def toggle_title_filter(self, group_id: str) -> bool:
        data = self.store.get(group_id)
        data["TitleFilterModeChange"] = 0 if data.get("TitleFilterModeChange") else 1
        return bool(data["TitleFilterModeChange"])
