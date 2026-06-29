"""本地可观测层：trace / token / 成本 追踪。

企业级要求：每一步可追溯、可统计。所有 trace 仅写本机 data/traces.jsonl，
不上云。借鉴美团技术报告中对 Agent 全链路可观测的要求。
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from config import settings


class Tracer:
    def __init__(self) -> None:
        self._path: Path = settings.data_dir / "traces.jsonl"
        self._lock = threading.Lock()
        self._totals: Dict[str, float] = defaultdict(float)
        self._spans: List[Dict[str, Any]] = []

    def log(self, kind: str, **fields: Any) -> None:
        rec = {"ts": time.time(), "kind": kind, **fields}
        with self._lock:
            self._spans.append(rec)
            if len(self._spans) > 500:
                self._spans = self._spans[-500:]
            usage = fields.get("usage") or {}
            self._totals["prompt_tokens"] += usage.get("prompt_tokens", 0)
            self._totals["completion_tokens"] += usage.get("completion_tokens", 0)
            self._totals["total_tokens"] += usage.get("total_tokens", 0)
            self._totals["cost_cny"] += usage.get("cost_cny", 0.0)
            self._totals["calls"] += 1 if kind == "llm" else 0
            try:
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception:
                pass

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "calls": int(self._totals["calls"]),
                "prompt_tokens": int(self._totals["prompt_tokens"]),
                "completion_tokens": int(self._totals["completion_tokens"]),
                "total_tokens": int(self._totals["total_tokens"]),
                "cost_cny": round(self._totals["cost_cny"], 6),
            }

    def recent(self, n: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._spans[-n:])


tracer = Tracer()
