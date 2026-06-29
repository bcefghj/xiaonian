"""本地隐私优先嵌入器（供 Mem0 使用）。

为什么自带一个本地嵌入器：
- 隐私：嵌入计算完全在本机完成，记忆内容不需要发往任何云端 embeddings 服务，
  契合小念"数据全本地"的核心卖点。
- 轻量：不依赖 torch / sentence-transformers，纯 Python + 哈希特征，零额外下载，
  在任意机器（含离线演示）都能稳定运行。
- 可替换：架构上仍是 Mem0 的标准 EmbeddingBase，未来可一键换成 fastembed /
  OpenAI 兼容嵌入端点以提升召回质量。

实现：字符级 n-gram 哈希到固定维度向量 + L2 归一化（hashing trick）。
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import List, Optional

try:
    from mem0.embeddings.base import EmbeddingBase
    from mem0.configs.embeddings.base import BaseEmbedderConfig
except Exception:  # pragma: no cover
    EmbeddingBase = object  # type: ignore
    BaseEmbedderConfig = object  # type: ignore

DIMS = 384


def _tokens(text: str) -> List[str]:
    text = (text or "").lower()
    words = re.findall(r"[a-z0-9]+", text)
    # 中文按 2-gram 切，英文按词 + 3-gram
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    grams: List[str] = list(words)
    for i in range(len(cjk) - 1):
        grams.append(cjk[i] + cjk[i + 1])
    for w in words:
        for i in range(len(w) - 2):
            grams.append(w[i : i + 3])
    return grams or list(text)


def embed_text(text: str, dims: int = DIMS) -> List[float]:
    vec = [0.0] * dims
    for tok in _tokens(text):
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        idx = h % dims
        sign = 1.0 if (h >> 8) % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


class LocalHashEmbedding(EmbeddingBase):  # type: ignore[misc]
    """Mem0 标准嵌入器接口的本地实现。"""

    def __init__(self, config: Optional["BaseEmbedderConfig"] = None):  # noqa: D401
        try:
            super().__init__(config)
        except Exception:
            self.config = config
        self.dims = DIMS
        if config is not None and getattr(config, "embedding_dims", None):
            self.dims = int(config.embedding_dims)

    def embed(self, text, memory_action=None):  # noqa: ANN001
        if isinstance(text, list):
            text = " ".join(map(str, text))
        return embed_text(str(text), self.dims)


# Mem0 的 EmbedderConfig 会按固定白名单校验 provider 名，因此我们把本地嵌入器
# 挂载到一个白名单内、且走通用 BaseEmbedderConfig 路径的 provider 名上（huggingface），
# 从而在不改 Mem0 源码的前提下接入自带的隐私嵌入器。
CARRIER_PROVIDER = "huggingface"


def register_with_mem0() -> bool:
    """把本地嵌入器注册进 Mem0 的工厂，使其可通过 provider 名引用。"""
    try:
        from mem0.utils.factory import EmbedderFactory

        EmbedderFactory.provider_to_class[CARRIER_PROVIDER] = (
            "memory.embedder.LocalHashEmbedding"
        )
        return True
    except Exception:
        return False
