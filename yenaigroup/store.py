# -*- coding: utf-8 -*-
"""数据存储：每群一个 JSON + 全局任务文件，语义对齐椰奶的 config/group/*.json。"""

from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
from typing import Any

from astrbot.api import logger

DEFAULT_GROUP_DATA: dict[str, Any] = {
    "bannedWords": {},
    "muteTime": 300,
    "TitleBannedWords": [],
    "TitleFilterModeChange": 0,
    "groupAddNotice": False,
    "verifyEnabled": False,
    "verifyMode": "",
    "verifyTime": 0,
    "verifySuccessMsg": "",
}


class GroupStore:
    """每群 JSON 存储：<data_dir>/groups/<群号>.json。"""

    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "groups"
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    def _path(self, group_id: str) -> Path:
        return self.root / f"{int(group_id)}.json"

    def _write_sync(self, group_id: str, data: dict) -> None:
        path = self._path(group_id)
        tmp = path.with_name(path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as e:  # noqa: BLE001
            logger.error("[yenai群管] 写入群配置失败 %s: %s", path, e)

    def _read(self, group_id: str) -> dict:
        path = self._path(group_id)
        data: dict = {}
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
            except Exception as e:  # noqa: BLE001
                logger.error("[yenai群管] 读取群配置失败 %s: %s", path, e)
        changed = False
        for key, value in DEFAULT_GROUP_DATA.items():
            if key not in data:
                data[key] = copy.deepcopy(value)
                changed = True
        if changed:
            self._write_sync(group_id, data)
        return data

    def get(self, group_id: str) -> dict:
        key = str(int(group_id))
        if key not in self._cache:
            self._cache[key] = self._read(key)
        return self._cache[key]

    def exists(self, group_id: str) -> bool:
        """该群是否已有配置文件（避免给无关群凭空建文件）。"""
        try:
            key = str(int(group_id))
        except (TypeError, ValueError):
            return False
        return key in self._cache or self._path(key).exists()

    async def save(self, group_id: str) -> None:
        key = str(int(group_id))
        data = self._cache.get(key)
        if data is None:
            return
        async with self._lock:
            await asyncio.to_thread(self._write_sync, key, data)

    # ---- 便捷访问 ----

    def banned_words(self, group_id: str) -> dict:
        return self.get(group_id).setdefault("bannedWords", {})

    def mute_time(self, group_id: str) -> int:
        try:
            return int(self.get(group_id).get("muteTime") or 300)
        except (TypeError, ValueError):
            return 300

    def title_banned_words(self, group_id: str) -> list:
        return self.get(group_id).setdefault("TitleBannedWords", [])


class TaskStore:
    """定时禁言/解禁任务的持久化（mute_tasks.json）。"""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "mute_tasks.json"
        self._lock = asyncio.Lock()

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as e:  # noqa: BLE001
            logger.error("[yenai群管] 读取定时任务失败: %s", e)
            return []

    def _save_sync(self, tasks: list[dict]) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            logger.error("[yenai群管] 写入定时任务失败: %s", e)

    async def save(self, tasks: list[dict]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_sync, tasks)
