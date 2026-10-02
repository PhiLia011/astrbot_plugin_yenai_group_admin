# -*- coding: utf-8 -*-
"""定时禁言/解禁任务，对齐椰奶 model/GroupAdmin.js 的 setMuteTask 系列。"""

from __future__ import annotations

import inspect
from typing import Any

from astrbot.api import logger

from .onebot import get_client
from .store import TaskStore


def normalize_cron(expr: str) -> str:
    """把 Quartz 风格表达式归一化成 APScheduler 可解析的形式。"""
    parts = [("*" if p == "?" else p) for p in str(expr).strip().split()]
    return " ".join(parts)


def build_trigger(expr: str):
    """构造 CronTrigger；表达式非法时抛 ValueError。"""
    from apscheduler.triggers.cron import CronTrigger

    text = normalize_cron(expr)
    parts = text.split()
    if len(parts) == 5:
        return CronTrigger.from_crontab(text)
    if len(parts) in (6, 7):
        second, minute, hour, day, month, day_of_week = parts[:6]
        return CronTrigger(
            second=second,
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
        )
    raise ValueError("cron 表达式需要 5 段或 6 段")


def hhmm_to_cron(hour: str, minute: str) -> str:
    return f"0 {int(minute)} {int(hour)} * * ?"


class MuteTaskScheduler:
    """整群禁言/解禁的定时任务管理器。"""

    def __init__(self, plugin):
        self.plugin = plugin
        self.store = TaskStore(plugin.data_dir)
        self.tasks: list[dict] = self.store.load()
        self._scheduler: Any = None
        self._jobs: list[tuple[dict, Any]] = []
        self._started = False

    # ---------------- 生命周期 ----------------

    def ensure_started(self) -> None:
        if self._started:
            return
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
        except Exception as e:  # noqa: BLE001
            logger.error("[yenai群管] 未安装 apscheduler，定时禁言不可用：%s", e)
            return
        tz = None
        try:
            cfg = self.plugin.context.get_config()
            tz_name = cfg.get("timezone") or None
            if tz_name:
                from zoneinfo import ZoneInfo

                tz = ZoneInfo(str(tz_name))
        except Exception:  # noqa: BLE001
            tz = None
        try:
            self._scheduler = AsyncIOScheduler(timezone=tz) if tz else AsyncIOScheduler()
            self._scheduler.start()
        except Exception as e:  # noqa: BLE001
            logger.error("[yenai群管] 启动定时任务调度器失败：%s", e)
            self._scheduler = None
            return
        self._started = True
        self._restore()

    def _restore(self) -> None:
        """恢复持久化的定时任务（对应椰奶 loadRedisMuteTask）。"""
        kept: list[dict] = []
        for task in self.tasks:
            if self._schedule(task):
                kept.append(task)
        if len(kept) != len(self.tasks):
            self.tasks = kept
            self.plugin.spawn(self.store.save(self.tasks))

    def _schedule(self, task: dict) -> bool:
        if self._scheduler is None:
            return False
        try:
            trigger = build_trigger(task.get("cron", ""))
        except (ValueError, TypeError, KeyError) as e:
            logger.error("[yenai群管] 定时任务表达式非法 %s：%s", task.get("cron"), e)
            return False
        job = self._scheduler.add_job(
            self._run,
            trigger=trigger,
            args=[task],
            misfire_grace_time=60,
            coalesce=True,
            max_instances=1,
        )
        self._jobs.append((task, job))
        return True

    async def shutdown(self) -> None:
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception as e:  # noqa: BLE001
                logger.debug("[yenai群管] 关闭调度器异常：%s", e)
        self._scheduler = None
        self._started = False
        self._jobs.clear()

    # ---------------- 执行 ----------------

    async def _run(self, task: dict) -> None:
        group_id = str(task.get("group") or "")
        enable = bool(task.get("type"))
        client = await self.plugin.find_client()
        if client is None:
            logger.error("[yenai群管] 定时任务执行失败：未获取到 OneBot 客户端")
            return
        try:
            result = client.call_action(
                "set_group_whole_ban",
                group_id=int(group_id),
                enable=enable,
            )
            if inspect.isawaitable(result):
                await result
            logger.info(
                "[yenai群管] 定时任务已执行：群 %s %s全体禁言",
                group_id,
                "开启" if enable else "解除",
            )
        except Exception as e:  # noqa: BLE001
            logger.error("[yenai群管] 定时任务执行失败（群 %s）：%s", group_id, e)

    # ---------------- 增删查 ----------------

    def find(self, group_id: str, task_type: bool) -> dict | None:
        for task in self.tasks:
            if str(task.get("group")) == str(group_id) and bool(task.get("type")) == bool(task_type):
                return task
        return None

    async def set_task(self, group_id: str, cron: str, task_type: bool, bot_id: str) -> bool:
        self.ensure_started()
        if self.find(group_id, task_type):
            return False
        try:
            build_trigger(cron)
        except (ValueError, TypeError) as e:
            raise ValueError(str(e)) from e
        task = {"cron": cron, "group": str(group_id), "type": bool(task_type), "botId": str(bot_id)}
        self.tasks.append(task)
        if not self._schedule(task):
            self.tasks.remove(task)
            return False
        await self.store.save(self.tasks)
        return True

    async def delete_task(self, group_id: str, task_type: bool) -> bool:
        target = self.find(group_id, task_type)
        if target is None:
            return False
        for task, job in list(self._jobs):
            if task is target:
                try:
                    job.remove()
                except Exception as e:  # noqa: BLE001
                    logger.debug("[yenai群管] 移除定时任务异常：%s", e)
                self._jobs.remove((task, job))
        self.tasks.remove(target)
        await self.store.save(self.tasks)
        return True

    def describe(self) -> list[list[dict]]:
        """按群汇总，生成 “群号 + 禁言时间 + 解禁时间” 的列表项。"""
        from .render import group_avatar_url, image_seg, text_seg

        grouped: dict[str, dict] = {}
        for task in self.tasks:
            item = grouped.setdefault(str(task.get("group")), {})
            if task.get("type"):
                item["mute"] = task.get("cron")
            else:
                item["unmute"] = task.get("cron")
        result = []
        for group_id, item in grouped.items():
            entry = [
                image_seg(group_avatar_url(group_id)),
                text_seg(f"\n群号：{group_id}"),
            ]
            if item.get("mute"):
                entry.append(text_seg(f'\n禁言时间："{item["mute"]}"'))
            if item.get("unmute"):
                entry.append(text_seg(f'\n解禁时间："{item["unmute"]}"'))
            result.append(entry)
        return result
