"""PII 脱敏：上云前对敏感信息做最小化处理。

隐私是小念的核心卖点。任何要发往云端模型的文本，先经过这里把手机号、邮箱、
身份证、银行卡等替换为占位符，降低敏感信息出域风险。
"""
from __future__ import annotations

import re
from typing import Tuple

_PATTERNS = [
    ("PHONE", re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")),
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("IDCARD", re.compile(r"(?<!\d)(\d{17}[\dXx])(?!\d)")),
    ("BANKCARD", re.compile(r"(?<!\d)(\d{16,19})(?!\d)")),
]


def redact_pii(text: str) -> Tuple[str, int]:
    """返回 (脱敏后文本, 命中条数)。"""
    if not text:
        return text, 0
    count = 0
    out = text
    for tag, pat in _PATTERNS:
        out, n = pat.subn(f"[{tag}]", out)
        count += n
    return out, count
