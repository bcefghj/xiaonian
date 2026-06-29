"""写操作两步确认（Human-in-the-loop）。

借鉴美团 DPT-Agent 的两步确认范式 + LangGraph 人在环：
危险/写操作（删除、移动、执行命令、下单、发消息）先生成 preview，
登记到这里等待用户确认，确认后才真正执行。
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass
class PendingAction:
    id: str
    kind: str
    summary: str
    payload: Dict[str, Any]
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending | approved | rejected | done
    result: Optional[str] = None


class ConfirmRegistry:
    """登记待确认的写操作。执行器在确认后回调真正的执行函数。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: Dict[str, PendingAction] = {}
        self._executors: Dict[str, Callable[[Dict[str, Any]], str]] = {}

    def request(
        self,
        kind: str,
        summary: str,
        payload: Dict[str, Any],
        executor: Callable[[Dict[str, Any]], str],
    ) -> PendingAction:
        action = PendingAction(id=uuid.uuid4().hex[:12], kind=kind, summary=summary, payload=payload)
        with self._lock:
            self._pending[action.id] = action
            self._executors[action.id] = executor
        return action

    def get(self, action_id: str) -> Optional[PendingAction]:
        return self._pending.get(action_id)

    def list_pending(self) -> list:
        return [a.__dict__ for a in self._pending.values() if a.status == "pending"]

    def resolve(self, action_id: str, approve: bool) -> PendingAction:
        with self._lock:
            action = self._pending.get(action_id)
            if action is None:
                raise KeyError(action_id)
            if action.status != "pending":
                return action
            if not approve:
                action.status = "rejected"
                action.result = "用户已拒绝该操作"
                return action
            action.status = "approved"
            executor = self._executors.get(action_id)
        # 在锁外执行，避免长任务阻塞
        if executor is not None:
            try:
                action.result = executor(action.payload)
                action.status = "done"
            except Exception as exc:
                action.result = f"执行失败：{exc}"
                action.status = "done"
        return action


confirm_registry = ConfirmRegistry()
