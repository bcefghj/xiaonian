"""小念 XiaoNian — 全局配置。

集中读取环境变量（.env），供各层使用。所有路径默认指向本机 data/ 目录，
体现"数据全本地"的隐私原则。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv 可选
    pass


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


def _b(key: str, default: bool) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class ProviderConfig:
    """单个模型 provider 的连接配置。

    protocol：openai（OpenAI 兼容 /chat/completions）或 anthropic（Anthropic 兼容
    /v1/messages）。MiniMax 的 coding 套餐 key 走 anthropic 协议（与 OpenAI 文本
    接口是不同的额度池）。
    """

    name: str
    api_key: str
    base_url: str
    model: str
    protocol: str = "openai"

    @property
    def available(self) -> bool:
        return bool(self.api_key) and not self.api_key.startswith("sk-xxxx")


@dataclass
class Settings:
    user_id: str = os.getenv("XIAONIAN_USER_ID", "master")
    user_name: str = os.getenv("XIAONIAN_USER_NAME", "主人")
    host: str = os.getenv("XIAONIAN_HOST", "127.0.0.1")
    port: int = int(os.getenv("XIAONIAN_PORT", "8765"))

    default_provider: str = os.getenv("XIAONIAN_PROVIDER", "minimax")

    require_confirm: bool = _b("XIAONIAN_REQUIRE_CONFIRM", True)
    redact_pii: bool = _b("XIAONIAN_REDACT_PII", True)

    proactive_enabled: bool = _b("XIAONIAN_PROACTIVE_ENABLED", True)
    quiet_start: int = int(os.getenv("XIAONIAN_QUIET_START", "23"))
    quiet_end: int = int(os.getenv("XIAONIAN_QUIET_END", "8"))

    providers: Dict[str, ProviderConfig] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.providers = {
            "minimax": ProviderConfig(
                name="minimax",
                api_key=os.getenv("MINIMAX_API_KEY", ""),
                base_url=os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/anthropic"),
                model=os.getenv("MINIMAX_MODEL", "MiniMax-M2"),
                protocol=os.getenv("MINIMAX_PROTOCOL", "anthropic"),
            ),
            "deepseek": ProviderConfig(
                name="deepseek",
                api_key=os.getenv("DEEPSEEK_API_KEY", ""),
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
                model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            ),
            "mimo": ProviderConfig(
                name="mimo",
                api_key=os.getenv("MIMO_API_KEY", ""),
                base_url=os.getenv("MIMO_BASE_URL", ""),
                model=os.getenv("MIMO_MODEL", "mimo"),
            ),
        }

    def provider(self, name: str | None = None) -> ProviderConfig:
        return self.providers.get(name or self.default_provider, self.providers["minimax"])

    @property
    def data_dir(self) -> Path:
        return DATA_DIR


settings = Settings()
