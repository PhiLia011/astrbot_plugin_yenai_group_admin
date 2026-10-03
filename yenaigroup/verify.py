# -*- coding: utf-8 -*-
"""入群验证状态机，对齐椰奶 apps/groupAdmin/groupVerify.js。"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass

OPERATORS = ("+", "-")


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

    @property
    def question(self) -> str:
        if self.kind == "letter":
            return self.code
        return f"{self.m} {self.operator} {self.n}"


class VerifyManager:
    """进程内验证状态，键为 “群号:QQ”，重启丢失（与椰奶一致）。"""

    def __init__(self):
        self.sessions: dict[str, VerifySession] = {}

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
            code = "".join(random.choices(string.ascii_letters, k=5))
            session = VerifySession(
                remain=int(times),
                kind="letter",
                m=0,
                n=0,
                operator="",
                code=code,
            )
            self.sessions[self.key(group_id, user_id)] = session
            return session
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
        self.sessions[self.key(group_id, user_id)] = session
        return session

    def check(self, group_id: str, user_id: str, message: str, mode: str) -> tuple[bool, VerifySession | None]:
        session = self.find(group_id, user_id)
        if session is None:
            return False, None
        text = (message or "").strip()
        if mode == "精确":
            ok = text == session.code
        else:
            ok = session.code in text
        return ok, session

    def consume_failure(self, group_id: str, user_id: str) -> int:
        session = self.find(group_id, user_id)
        if session is None:
            return 0
        session.remain -= 1
        return session.remain

    def drop(self, group_id: str, user_id: str) -> VerifySession | None:
        return self.sessions.pop(self.key(group_id, user_id), None)
