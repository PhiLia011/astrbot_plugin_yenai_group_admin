# -*- coding: utf-8 -*-
"""入群验证状态机，对齐椰奶 apps/groupAdmin/groupVerify.js。

与椰奶的差异：验证会话会落盘到 ``<插件数据目录>/verify_sessions.json``。
椰奶把状态放在内存里，插件一重载/重启就全丢——待执行的超时踢人定时器随之被取消，
等于默默放过了所有没通过验证的人。落盘后重载会重新武装超时任务。
"""

from __future__ import annotations

import json
import random
import string
import time
from dataclasses import dataclass
from pathlib import Path

OPERATORS = ("+", "-")
# 字母验证码字符集：去掉 l/I、o/O 等肉眼易混的字母，减少用户看错
LETTER_CHARS = "".join(c for c in string.ascii_letters if c not in "lIoO")
LETTER_LENGTH = 5

SESSIONS_FILENAME = "verify_sessions.json"


@dataclass
class VerifySession:
    remain: int
    kind: str  # "math" 算式 / "letter" 字母验证码
    m: int
    n: int
    operator: str
    code: str
    task: object | None = None
    remind_task: object | None = None
    # 以下用于跨重载/重启恢复
    group_id: str = ""
    user_id: str = ""
    deadline: float = 0.0

    @property
    def question(self) -> str:
        if self.kind == "letter":
            return self.code
        return f"{self.m} {self.operator} {self.n}"

    def to_record(self) -> dict:
        return {
            "group_id": self.group_id,
            "user_id": self.user_id,
            "code": self.code,
            "kind": self.kind,
            "remain": int(self.remain),
            "m": int(self.m),
            "n": int(self.n),
            "operator": self.operator,
            "deadline": float(self.deadline),
        }

    @classmethod
    def from_record(cls, record: dict) -> VerifySession | None:
        try:
            return cls(
                remain=int(record["remain"]),
                kind=str(record["kind"]),
                m=int(record["m"]),
                n=int(record["n"]),
                operator=str(record["operator"]),
                code=str(record["code"]),
                group_id=str(record["group_id"]),
                user_id=str(record["user_id"]),
                deadline=float(record.get("deadline") or 0.0),
            )
        except (KeyError, TypeError, ValueError):
            return None


class VerifyManager:
    """验证状态，键为 “群号:QQ”；写入 data_dir 后重载可恢复。"""

    def __init__(self, data_dir: str | Path | None = None):
        self.sessions: dict[str, VerifySession] = {}
        self.path = (
            Path(data_dir) / SESSIONS_FILENAME if data_dir is not None else None
        )

    @staticmethod
    def key(group_id: str, user_id: str) -> str:
        return f"{group_id}:{user_id}"

    def find(self, group_id: str, user_id: str) -> VerifySession | None:
        return self.sessions.get(self.key(group_id, user_id))

    def create(
        self,
        group_id: str,
        user_id: str,
        times: int,
        range_min: int,
        range_max: int,
        kind: str = "math",
    ) -> VerifySession:
        if kind == "letter":
            code = "".join(random.choices(LETTER_CHARS, k=LETTER_LENGTH))
            session = VerifySession(
                remain=int(times),
                kind="letter",
                m=0,
                n=0,
                operator="",
                code=code,
            )
            return self._put(group_id, user_id, session)
        operator = random.choice(OPERATORS)
        low, high = int(range_min), int(range_max)
        if high < low:
            low, high = high, low
        m = random.randint(low, high)
        n = random.randint(low, high)
        while m == n and high > low:
            n = random.randint(low, high)
        if m < n:
            m, n = n, m
        code = str(m - n) if operator == "-" else str(m + n)
        session = VerifySession(
            remain=int(times),
            kind="math",
            m=m,
            n=n,
            operator=operator,
            code=code,
        )
        return self._put(group_id, user_id, session)

    def _put(self, group_id: str, user_id: str, session: VerifySession) -> VerifySession:
        session.group_id = str(group_id)
        session.user_id = str(user_id)
        self.sessions[self.key(group_id, user_id)] = session
        return session

    def check(self, group_id: str, user_id: str, message: str, mode: str) -> tuple[bool, VerifySession | None]:
        session = self.find(group_id, user_id)
        if session is None:
            return False, None
        text = (message or "").strip()
        # 群提示会把答案放在「」里，用户整段复制过来时也要能识别
        text = text.strip("「」『』“”\"'")
        # 字母验证码区分大小写，必须和群里给出的完全一致
        target = session.code
        if mode == "精确":
            ok = text == target
        else:
            ok = target in text
        return ok, session

    def consume_failure(self, group_id: str, user_id: str) -> int:
        session = self.find(group_id, user_id)
        if session is None:
            return 0
        session.remain -= 1
        return session.remain

    def drop(self, group_id: str, user_id: str) -> VerifySession | None:
        return self.sessions.pop(self.key(group_id, user_id), None)

    # ---------------- 持久化（跨重载/重启） ----------------

    def save(self) -> None:
        """把当前会话写入磁盘；不持久化 task 对象。"""
        if self.path is None:
            return
        records = [s.to_record() for s in self.sessions.values()]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
            tmp.replace(self.path)
        except Exception:  # noqa: BLE001 - 落盘失败不应影响验证流程
            from astrbot.api import logger

            logger.exception("[yenai群管][入群验证] 写入验证会话失败")

    def load(self) -> list[VerifySession]:
        """读取上次遗留的会话（不含 task）。读不到或损坏时返回空列表。"""
        if self.path is None or not self.path.exists():
            return []
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:  # noqa: BLE001
            return []
        if not isinstance(raw, list):
            return []
        sessions: list[VerifySession] = []
        for record in raw:
            if not isinstance(record, dict):
                continue
            session = VerifySession.from_record(record)
            if session is None or not session.group_id or not session.user_id:
                continue
            sessions.append(session)
        return sessions

    def adopt(self, session: VerifySession) -> VerifySession:
        """把恢复出来的会话放回内存。"""
        return self._put(session.group_id, session.user_id, session)

    @staticmethod
    def deadline_from_now(timeout: int) -> float:
        return time.time() + max(0, int(timeout))
