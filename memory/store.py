"""记忆层：Mem0（语义记忆，越用越懂你）+ 结构化画像（SQLite）。

设计：
- 语义记忆走 Mem0（业界领先的 Agent 记忆层）：LLM 抽取事实 → 本地向量库（Qdrant
  本地路径模式）→ 亚秒级检索。嵌入用本机隐私嵌入器，记忆内容不出域。
- 结构化画像（用户画像 / 生活状态 / 习惯作息 / 重要关系 / 偏好禁忌）走 SQLite，
  供"记忆可视化 UI"和"主动关心引擎"读取。
- 所有失败都优雅降级：即使 Mem0/LLM 不可用，结构化画像与本地关键词回忆仍然工作，
  保证演示链路不断。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings
from observability import tracer

from .embedder import CARRIER_PROVIDER, embed_text, register_with_mem0

_DIMS = 384


class _ProfileDB:
    """结构化画像 + 本地关键词记忆兜底。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS profile(
                    user_id TEXT, category TEXT, key TEXT, value TEXT, updated REAL,
                    PRIMARY KEY(user_id, category, key))"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS notes(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT, text TEXT, created REAL)"""
            )

    def set_fact(self, user_id: str, category: str, key: str, value: str) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "REPLACE INTO profile(user_id,category,key,value,updated) VALUES(?,?,?,?,?)",
                (user_id, category, key, value, time.time()),
            )

    def get_profile(self, user_id: str) -> Dict[str, Dict[str, str]]:
        out: Dict[str, Dict[str, str]] = {}
        with self._lock, self._conn() as c:
            for row in c.execute(
                "SELECT category,key,value FROM profile WHERE user_id=? ORDER BY category,key",
                (user_id,),
            ):
                out.setdefault(row["category"], {})[row["key"]] = row["value"]
        return out

    def add_note(self, user_id: str, text: str) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO notes(user_id,text,created) VALUES(?,?,?)",
                (user_id, text, time.time()),
            )

    def search_notes(self, user_id: str, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        rows = []
        with self._lock, self._conn() as c:
            for row in c.execute(
                "SELECT text,created FROM notes WHERE user_id=? ORDER BY id DESC LIMIT 200",
                (user_id,),
            ):
                rows.append({"text": row["text"], "created": row["created"]})
        # 本地向量相似度兜底打分
        qv = embed_text(query)
        scored = []
        for r in rows:
            sv = embed_text(r["text"])
            score = sum(a * b for a, b in zip(qv, sv))
            scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for s, r in scored[:limit] if s > 0.05]


class MemoryStore:
    def __init__(self) -> None:
        self.profile_db = _ProfileDB(settings.data_dir / "profile.db")
        self._mem = None
        self._mem_error: Optional[str] = None
        self._init_mem0()

    # ---- Mem0 初始化 ----
    def _init_mem0(self) -> None:
        try:
            register_with_mem0()
            from mem0 import Memory

            cfg = settings.provider()  # 当前默认 provider
            llm_provider = cfg.name if cfg.name in {"minimax", "deepseek"} else "openai"
            llm_conf: Dict[str, Any] = {
                "provider": llm_provider,
                "config": {"model": cfg.model, "api_key": cfg.api_key},
            }
            qdrant_path = str(settings.data_dir / "qdrant")
            mem_config = {
                "llm": llm_conf,
                "embedder": {
                    "provider": CARRIER_PROVIDER,  # 实际挂载的是本地隐私嵌入器
                    "config": {"embedding_dims": _DIMS},
                },
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "collection_name": "xiaonian",
                        "path": qdrant_path,
                        "on_disk": True,
                        "embedding_model_dims": _DIMS,
                    },
                },
            }
            if not cfg.available:
                # 无可用在线 LLM：Mem0 的事实抽取会失败，跳过初始化，使用本地兜底
                self._mem_error = "no_online_llm"
                return
            self._mem = Memory.from_config(mem_config)
        except Exception as exc:  # pragma: no cover
            self._mem_error = str(exc)
            self._mem = None

    @property
    def engine(self) -> str:
        return "mem0" if self._mem is not None else "local-fallback"

    # ---- 写入 ----
    def add(self, user_id: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        text = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in messages)
        self.profile_db.add_note(user_id, text)
        result: Dict[str, Any] = {"engine": self.engine, "facts": []}
        if self._mem is not None:
            try:
                r = self._mem.add(messages, user_id=user_id)
                result["facts"] = r.get("results", []) if isinstance(r, dict) else r
            except Exception as exc:
                tracer.log("memory_error", op="add", error=str(exc)[:160])
        return result

    def remember_fact(self, user_id: str, category: str, key: str, value: str) -> None:
        """显式结构化记忆（如：偏好辣、作息晚睡），供画像与主动引擎使用。"""
        self.profile_db.set_fact(user_id, category, key, value)
        if self._mem is not None:
            try:
                self._mem.add(
                    [{"role": "user", "content": f"{category}.{key}: {value}"}],
                    user_id=user_id,
                )
            except Exception:
                pass

    # ---- 检索 ----
    def search(self, user_id: str, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        if self._mem is not None:
            try:
                r = self._mem.search(query, user_id=user_id, limit=limit)
                items = r.get("results", []) if isinstance(r, dict) else r
                return [{"text": x.get("memory", ""), "score": x.get("score")} for x in items]
            except Exception as exc:
                tracer.log("memory_error", op="search", error=str(exc)[:160])
        # 兜底
        return self.profile_db.search_notes(user_id, query, limit)

    def recall_context(self, user_id: str, query: str, limit: int = 5) -> str:
        """把检索到的记忆 + 结构化画像拼成可注入上下文的字符串。"""
        parts: List[str] = []
        prof = self.profile_db.get_profile(user_id)
        if prof:
            flat = []
            for cat, kv in prof.items():
                for k, v in kv.items():
                    flat.append(f"- [{cat}] {k}: {v}")
            if flat:
                parts.append("【你已知的关于主人的画像】\n" + "\n".join(flat[:30]))
        hits = self.search(user_id, query, limit)
        if hits:
            parts.append(
                "【相关记忆】\n" + "\n".join(f"- {h['text']}" for h in hits if h.get("text"))
            )
        return "\n\n".join(parts)

    def get_profile(self, user_id: str) -> Dict[str, Any]:
        return {
            "engine": self.engine,
            "profile": self.profile_db.get_profile(user_id),
            "mem_error": self._mem_error,
        }


_memory: Optional[MemoryStore] = None


def get_memory() -> MemoryStore:
    global _memory
    if _memory is None:
        _memory = MemoryStore()
    return _memory
