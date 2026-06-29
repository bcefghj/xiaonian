"""生活状态推断（全部本地）。

把可观测信号汇聚成"主人当前生活状态"：忙碌度 workload、疲惫度 fatigue、情绪 mood。
供宠物情绪映射与主动关心引擎使用。演示阶段用对话频率 + 时间等轻量信号推断，
未来可接入（经授权的）健康/日程数据提升精度。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Dict, List


class LifeState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._activity: List[float] = []  # 最近交互时间戳
        self._mood = "neutral"
        self._manual: Dict[str, Any] = {}

    def record_activity(self) -> None:
        with self._lock:
            now = time.time()
            self._activity.append(now)
            self._activity = [t for t in self._activity if now - t < 3600 * 3]

    def set_mood(self, mood: str) -> None:
        with self._lock:
            self._mood = mood

    def set_manual(self, **kw: Any) -> None:
        with self._lock:
            self._manual.update(kw)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            recent = [t for t in self._activity if now - t < 3600]
            workload = min(1.0, len(recent) / 20.0)
            hour = datetime.now().hour
            fatigue = 0.3
            if hour >= 23 or hour < 6:
                fatigue = 0.8
            elif hour >= 21:
                fatigue = 0.55
            fatigue = max(fatigue, min(1.0, len(recent) / 25.0))
            data = {
                "workload": round(self._manual.get("workload", workload), 2),
                "fatigue": round(self._manual.get("fatigue", fatigue), 2),
                "mood": self._manual.get("mood", self._mood),
                "sleep_hours": self._manual.get("sleep_hours"),
                "interactions_last_hour": len(recent),
            }
            return data


life_state = LifeState()
