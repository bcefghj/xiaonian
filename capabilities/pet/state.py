"""宠物形象 —— 情感化的"脸"（轻量）。

定位：小念的脸。把主人的状态可视化为一只小宠物的情绪，强化"主动关心"的陪伴感，
而不是做成独立养成游戏。

状态来源（全部本地处理）：
- 任务/日程负荷（忙不忙）
- 对话情绪（开心/低落/疲惫）
- 可选健康数据（睡眠/活动；需授权）

映射成宠物情绪 → 前端用对应表情/形象展示。表情图可用 MiniMax 图像生成离线产出，
此处只负责状态机与映射。
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Dict, Optional

# 情绪 → emoji（前端可替换为 MiniMax 生成的形象图）
EMOTION_FACE = {
    "happy": "(◕ᴗ◕)",
    "calm": "(｡•‿•｡)",
    "caring": "(っ◕‿◕)っ",
    "tired": "(￣ヘ￣)",
    "worried": "(•́ω•̀)",
    "sleepy": "(￫ᴗ￩)",
    "excited": "(★ᴗ★)",
}

EMOTION_COLOR = {
    "happy": "#FFD166",
    "calm": "#9AD0EC",
    "caring": "#FF9AA2",
    "tired": "#B0A8B9",
    "worried": "#F4A259",
    "sleepy": "#8E9AAF",
    "excited": "#FF7AA2",
}


@dataclass
class PetSnapshot:
    emotion: str
    face: str
    color: str
    message: str
    energy: int  # 0-100
    updated: float


class PetState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._emotion = "calm"
        self._energy = 80
        self._message = "我在呢～"
        self._updated = time.time()

    def update_from_signals(
        self,
        workload: float = 0.3,
        mood: str = "neutral",
        fatigue: float = 0.3,
        sleep_hours: Optional[float] = None,
    ) -> PetSnapshot:
        """根据信号更新宠物情绪。workload/fatigue ∈ [0,1]。"""
        energy = int(max(0, min(100, 100 - fatigue * 60 - workload * 20)))
        if sleep_hours is not None and sleep_hours < 6:
            energy = min(energy, 55)

        if mood in {"sad", "down", "low"}:
            emotion, msg = "worried", "感觉你今天有点累，要不要歇会儿？我陪你。"
        elif fatigue > 0.7 or (sleep_hours is not None and sleep_hours < 6):
            emotion, msg = "tired", "你看起来需要休息了，早点睡呀。"
        elif workload > 0.7:
            emotion, msg = "caring", "今天事情有点多，我帮你盯着，别太拼。"
        elif mood in {"happy", "good", "up"}:
            emotion, msg = "happy", "看你心情不错，我也开心！"
        elif energy > 80:
            emotion, msg = "excited", "状态满格，今天能搞定很多事～"
        else:
            emotion, msg = "calm", "一切都好，有事叫我。"

        with self._lock:
            self._emotion = emotion
            self._energy = energy
            self._message = msg
            self._updated = time.time()
        return self.snapshot()

    def set_emotion(self, emotion: str, message: str = "") -> PetSnapshot:
        with self._lock:
            self._emotion = emotion if emotion in EMOTION_FACE else "calm"
            if message:
                self._message = message
            self._updated = time.time()
        return self.snapshot()

    def snapshot(self) -> PetSnapshot:
        with self._lock:
            e = self._emotion
            return PetSnapshot(
                emotion=e,
                face=EMOTION_FACE.get(e, EMOTION_FACE["calm"]),
                color=EMOTION_COLOR.get(e, "#9AD0EC"),
                message=self._message,
                energy=self._energy,
                updated=self._updated,
            )

    def as_dict(self) -> Dict:
        return asdict(self.snapshot())


_pet: Optional[PetState] = None


def get_pet() -> PetState:
    global _pet
    if _pet is None:
        _pet = PetState()
    return _pet
