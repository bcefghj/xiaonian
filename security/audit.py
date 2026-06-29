"""本地 append-only 审计日志。

所有写操作、确认、技能调用、主动关心都留痕，写到本机 data/audit.jsonl，
保证可追溯、可解释，符合企业级合规要求。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

from config import settings


class Audit:
    def __init__(self) -> None:
        self._path: Path = settings.data_dir / "audit.jsonl"
        self._lock = threading.Lock()

    def record(self, action: str, **fields: Any) -> None:
        rec = {"ts": time.time(), "action": action, **fields}
        with self._lock:
            try:
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception:
                pass

    def tail(self, n: int = 50) -> List[Dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()[-n:]
            return [json.loads(x) for x in lines if x.strip()]
        except Exception:
            return []


audit = Audit()
