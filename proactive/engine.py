"""主动关心引擎（小念最重要的差异化能力）。

被动问答只是工具，主动关心才是"伴侣"。本引擎让小念在对的时间主动开口：
- 调度器（APScheduler cron）：晨间问候、午间提醒、夜间关心等固定节律。
- 预测触发：基于学到的规律 + 当前生活状态 + 日程，在恰当时机主动关心。
- 关心策略：贴合状态（忙碌/疲惫/低落）给恰当的话；敏感关心（如生理期）
  默认关闭、需授权、数据仅本地。
- 静默协议：静默时段不打扰；无事不硬找话；同类关心有节流，避免打扰感。

产出的"主动消息"进入 outbox，由 UI/IM Bridge 推送给主人。
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from apscheduler.schedulers.background import BackgroundScheduler

from config import settings
from observability import tracer
from security import audit


@dataclass
class ProactiveMessage:
    kind: str
    text: str
    emotion: str = "caring"
    created: float = field(default_factory=time.time)
    delivered: bool = False


class ProactiveEngine:
    def __init__(self) -> None:
        self._scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self._outbox: List[ProactiveMessage] = []
        self._lock = threading.Lock()
        self._last_sent: Dict[str, float] = {}
        self._started = False
        # 由外部注入：生成关心文案（可走 LLM）/ 读取生活状态
        self.compose: Optional[Callable[[str, Dict[str, Any]], str]] = None
        self.get_life_state: Optional[Callable[[], Dict[str, Any]]] = None
        self.on_message: Optional[Callable[[ProactiveMessage], None]] = None

    # ---------- 静默与节流 ----------
    def _in_quiet_hours(self) -> bool:
        h = datetime.now().hour
        s, e = settings.quiet_start, settings.quiet_end
        if s <= e:
            return s <= h < e
        return h >= s or h < e  # 跨夜

    def _throttled(self, kind: str, min_gap_sec: float) -> bool:
        last = self._last_sent.get(kind, 0)
        return (time.time() - last) < min_gap_sec

    # ---------- 发出关心 ----------
    def emit(self, kind: str, default_text: str, emotion: str = "caring",
             respect_quiet: bool = True, min_gap_sec: float = 3600) -> Optional[ProactiveMessage]:
        if not settings.proactive_enabled:
            return None
        if respect_quiet and self._in_quiet_hours():
            tracer.log("proactive_skip", kind=kind, reason="quiet_hours")
            return None
        if self._throttled(kind, min_gap_sec):
            tracer.log("proactive_skip", kind=kind, reason="throttled")
            return None

        life = {}
        if self.get_life_state:
            try:
                life = self.get_life_state() or {}
            except Exception:
                life = {}

        text = default_text
        if self.compose:
            try:
                composed = self.compose(kind, life)
                if composed:
                    text = composed
            except Exception:
                pass

        msg = ProactiveMessage(kind=kind, text=text, emotion=emotion)
        with self._lock:
            self._outbox.append(msg)
            self._last_sent[kind] = time.time()
        audit.record("proactive.emit", kind=kind)
        tracer.log("proactive_emit", kind=kind)
        if self.on_message:
            try:
                self.on_message(msg)
            except Exception:
                pass
        return msg

    # ---------- 固定节律 ----------
    def _morning(self) -> None:
        self.emit("morning", "早上好呀～新的一天，我帮你看了下今天的安排，要不要一起过一遍？", "happy", min_gap_sec=3600 * 6)

    def _noon(self) -> None:
        self.emit("noon", "中午啦，记得吃饭哦。想吃点什么？我可以帮你点。", "caring", min_gap_sec=3600 * 4)

    def _evening(self) -> None:
        self.emit("evening", "忙了一天辛苦啦，今天过得怎么样？需要我帮你把明天的事理一理吗？", "caring", min_gap_sec=3600 * 6)

    def _predictive_tick(self) -> None:
        """预测触发：根据当前生活状态决定是否主动关心。"""
        if not self.get_life_state:
            return
        try:
            life = self.get_life_state() or {}
        except Exception:
            return
        fatigue = float(life.get("fatigue", 0))
        workload = float(life.get("workload", 0))
        if fatigue > 0.75:
            self.emit("care_fatigue", "你这两天好像很累，记得给自己留点休息时间。要不要我帮你把不急的事往后排排？", "worried", min_gap_sec=3600 * 4)
        elif workload > 0.8:
            self.emit("care_busy", "今天任务有点满，我盯着进度，需要我帮你分担哪一块？", "caring", min_gap_sec=3600 * 3)

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._started:
            return
        self._scheduler.add_job(self._morning, "cron", hour=8, minute=30, id="morning")
        self._scheduler.add_job(self._noon, "cron", hour=12, minute=0, id="noon")
        self._scheduler.add_job(self._evening, "cron", hour=20, minute=0, id="evening")
        self._scheduler.add_job(self._predictive_tick, "interval", minutes=30, id="predict")
        try:
            self._scheduler.start()
            self._started = True
            audit.record("proactive.start")
        except Exception as exc:
            tracer.log("proactive_error", error=str(exc)[:160])

    def stop(self) -> None:
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False

    # ---------- outbox ----------
    def pending(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [asdict(m) for m in self._outbox if not m.delivered]

    def drain(self) -> List[Dict[str, Any]]:
        with self._lock:
            out = [asdict(m) for m in self._outbox if not m.delivered]
            for m in self._outbox:
                m.delivered = True
            return out

    def trigger_demo(self, kind: str = "evening") -> Optional[Dict[str, Any]]:
        """演示用：忽略静默/节流，立刻产生一条主动关心。"""
        mapping = {
            "morning": ("早上好呀～今天的安排我帮你理好了，先看最重要的三件？", "happy"),
            "noon": ("中午啦，记得吃饭。想吃点什么我帮你点～", "caring"),
            "evening": ("忙了一天辛苦啦，早点休息。明天的事我都记着呢。", "caring"),
            "care_fatigue": ("你最近好像很累，今晚早点睡吧，我守着这边。", "worried"),
        }
        text, emotion = mapping.get(kind, mapping["evening"])
        msg = ProactiveMessage(kind=kind, text=text, emotion=emotion)
        with self._lock:
            self._outbox.append(msg)
        audit.record("proactive.demo", kind=kind)
        if self.on_message:
            try:
                self.on_message(msg)
            except Exception:
                pass
        return asdict(msg)


_engine: Optional[ProactiveEngine] = None


def get_proactive() -> ProactiveEngine:
    global _engine
    if _engine is None:
        _engine = ProactiveEngine()
    return _engine
