# -*- coding: utf-8 -*-
"""投票禁言/踢人的状态机，对齐椰奶 apps/groupAdmin/groupVote.js。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VoteState:
    type: str  # "Ban" | "Kick"
    initiator: str
    support: int = 1
    oppose: int = 0
    users: list[str] = field(default_factory=list)

    @property
    def result_text(self) -> str:
        return f"支持票数：{self.support}\n反对票数：{self.oppose}"


class VoteManager:
    """进程内投票状态，键为 “群号 + 目标QQ”。"""

    def __init__(self):
        self._votes: dict[str, VoteState] = {}

    @staticmethod
    def key(group_id: str, user_id: str) -> str:
        return f"{group_id}{user_id}"

    def exists(self, group_id: str, user_id: str) -> bool:
        return self.key(group_id, user_id) in self._votes

    def create(self, group_id: str, user_id: str, vote_type: str, initiator: str) -> bool:
        key = self.key(group_id, user_id)
        if key in self._votes:
            return False
        self._votes[key] = VoteState(type=vote_type, initiator=str(initiator), users=[str(initiator)])
        return True

    def get(self, group_id: str, user_id: str) -> VoteState | None:
        return self._votes.get(self.key(group_id, user_id))

    def follow(self, group_id: str, user_id: str, supporter: str, support: bool) -> tuple[str, VoteState | None]:
        """返回 (状态码, 投票状态)。

        状态码：ok / no_vote / repeated
        """
        state = self.get(group_id, user_id)
        if state is None:
            return "no_vote", None
        if str(supporter) in state.users:
            return "repeated", state
        state.users.append(str(supporter))
        if support:
            state.support += 1
        else:
            state.oppose += 1
        return "ok", state

    def settle(self, group_id: str, user_id: str, min_num: int) -> tuple[bool, VoteState | None]:
        """结算并清除投票，返回 (是否通过, 状态)。"""
        key = self.key(group_id, user_id)
        state = self._votes.pop(key, None)
        if state is None:
            return False, None
        success = state.support > state.oppose and state.support >= int(min_num)
        return success, state

    def drop(self, group_id: str, user_id: str) -> VoteState | None:
        return self._votes.pop(self.key(group_id, user_id), None)
