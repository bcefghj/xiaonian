"""多 provider 模型抽象层。

设计目标：
- 所有 provider（MiniMax M3 / DeepSeek / 小米 MiMo）统一为 OpenAI 兼容协议，
  通过同一套接口调用，UI 可一键切换。
- 统一处理各家差异（例如 MiniMax 多轮工具调用需回填带 reasoning 的完整
  assistant 消息）。
- 离线/无密钥时优雅降级为内置 mock，保证整套系统在演示环境也能端到端跑通。
- 内置 token / 成本 / 延迟 观测，喂给可观测层。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from config import ProviderConfig, settings

try:
    from openai import OpenAI

    _HAS_OPENAI = True
except Exception:  # pragma: no cover
    _HAS_OPENAI = False

try:
    from anthropic import Anthropic

    _HAS_ANTHROPIC = True
except Exception:  # pragma: no cover
    _HAS_ANTHROPIC = False


@dataclass
class ChatResult:
    text: str
    provider: str
    model: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    raw_message: Optional[Dict[str, Any]] = None
    usage: Dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    offline: bool = False


# 粗略的每百万 token 价格（人民币元），仅用于本地成本观测的量级估计。
_PRICE_PER_MTOK = {
    "minimax": {"in": 2.0, "out": 8.0},
    "deepseek": {"in": 1.0, "out": 2.0},
    "mimo": {"in": 1.0, "out": 2.0},
    "mock": {"in": 0.0, "out": 0.0},
}


class ModelRouter:
    """根据 provider 名路由到对应的 OpenAI 兼容客户端。"""

    def __init__(self) -> None:
        self._clients: Dict[str, Any] = {}
        self._anthropic_clients: Dict[str, Any] = {}

    def _client(self, cfg: ProviderConfig):
        if not _HAS_OPENAI or not cfg.available or not cfg.base_url:
            return None
        if cfg.name not in self._clients:
            self._clients[cfg.name] = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
        return self._clients[cfg.name]

    def _anthropic_client(self, cfg: ProviderConfig):
        if not _HAS_ANTHROPIC or not cfg.available or not cfg.base_url:
            return None
        if cfg.name not in self._anthropic_clients:
            self._anthropic_clients[cfg.name] = Anthropic(
                api_key=cfg.api_key, base_url=cfg.base_url
            )
        return self._anthropic_clients[cfg.name]

    def available_providers(self) -> List[Dict[str, Any]]:
        out = []
        for name, cfg in settings.providers.items():
            out.append(
                {
                    "name": name,
                    "model": cfg.model,
                    "available": cfg.available and bool(cfg.base_url),
                    "is_default": name == settings.default_provider,
                }
            )
        return out

    @staticmethod
    def _estimate_cost(provider: str, usage: Dict[str, int]) -> float:
        p = _PRICE_PER_MTOK.get(provider, _PRICE_PER_MTOK["mock"])
        pin = usage.get("prompt_tokens", 0) / 1_000_000 * p["in"]
        pout = usage.get("completion_tokens", 0) / 1_000_000 * p["out"]
        return round(pin + pout, 6)

    def chat(
        self,
        messages: List[Dict[str, Any]],
        provider: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> ChatResult:
        cfg = settings.provider(provider)
        t0 = time.time()

        if cfg.protocol == "anthropic":
            if not cfg.available or self._anthropic_client(cfg) is None:
                res = _mock_chat(messages, tools)
                res.latency_ms = int((time.time() - t0) * 1000)
                return res
            return self._anthropic_chat(cfg, messages, tools, temperature, max_tokens, t0)

        client = self._client(cfg)
        if client is None:
            res = _mock_chat(messages, tools)
            res.latency_ms = int((time.time() - t0) * 1000)
            return res

        kwargs: Dict[str, Any] = {
            "model": cfg.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as exc:  # 网络/鉴权失败 → 降级，保证不崩
            res = _mock_chat(messages, tools, error=str(exc))
            res.latency_ms = int((time.time() - t0) * 1000)
            res.provider = cfg.name
            res.model = cfg.model
            return res

        choice = resp.choices[0]
        msg = choice.message
        tool_calls: List[Dict[str, Any]] = []
        if getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                tool_calls.append(
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                )

        usage = {}
        if getattr(resp, "usage", None):
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }
        usage["cost_cny"] = self._estimate_cost(cfg.name, usage)

        return ChatResult(
            text=msg.content or "",
            provider=cfg.name,
            model=cfg.model,
            tool_calls=tool_calls,
            raw_message=msg.model_dump() if hasattr(msg, "model_dump") else None,
            usage=usage,
            latency_ms=int((time.time() - t0) * 1000),
        )

    # ---------- Anthropic 协议（MiniMax coding 套餐）----------
    def _anthropic_chat(
        self,
        cfg: ProviderConfig,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        temperature: float,
        max_tokens: int,
        t0: float,
    ) -> ChatResult:
        client = self._anthropic_client(cfg)
        system, conv = _openai_to_anthropic_messages(messages)
        kwargs: Dict[str, Any] = {
            "model": cfg.model,
            "max_tokens": max(max_tokens, 1024),
            "messages": conv,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = _openai_to_anthropic_tools(tools)

        try:
            resp = client.messages.create(**kwargs)
        except Exception as exc:
            res = _mock_chat(messages, tools, error=str(exc))
            res.latency_ms = int((time.time() - t0) * 1000)
            res.provider = cfg.name
            res.model = cfg.model
            return res

        text_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "arguments": _json.dumps(block.input, ensure_ascii=False),
                    }
                )

        usage = {}
        if getattr(resp, "usage", None):
            pin = getattr(resp.usage, "input_tokens", 0)
            pout = getattr(resp.usage, "output_tokens", 0)
            usage = {
                "prompt_tokens": pin,
                "completion_tokens": pout,
                "total_tokens": pin + pout,
            }
        usage["cost_cny"] = self._estimate_cost(cfg.name, usage)

        return ChatResult(
            text="".join(text_parts),
            provider=cfg.name,
            model=cfg.model,
            tool_calls=tool_calls,
            usage=usage,
            latency_ms=int((time.time() - t0) * 1000),
        )


import json as _json
import uuid as _uuid


def _openai_to_anthropic_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for t in tools:
        fn = t.get("function", t)
        out.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return out


def _openai_to_anthropic_messages(messages: List[Dict[str, Any]]):
    """把 OpenAI 风格消息（含 tool_calls / tool 角色）转换为 Anthropic 格式。

    返回 (system_str, anthropic_messages)。
    """
    system_parts: List[str] = []
    conv: List[Dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            if m.get("content"):
                system_parts.append(str(m["content"]))
            continue
        if role == "user":
            conv.append({"role": "user", "content": str(m.get("content", ""))})
            continue
        if role == "assistant":
            blocks: List[Dict[str, Any]] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": str(m["content"])})
            for tc in m.get("tool_calls", []) or []:
                fn = tc.get("function", {})
                try:
                    args = _json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                blocks.append(
                    {"type": "tool_use", "id": tc.get("id"), "name": fn.get("name"), "input": args}
                )
            if not blocks:
                blocks.append({"type": "text", "text": ""})
            conv.append({"role": "assistant", "content": blocks})
            continue
        if role == "tool":
            conv.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.get("tool_call_id"),
                            "content": str(m.get("content", "")),
                        }
                    ],
                }
            )
            continue
    # Anthropic 要求首条为 user；若不是，插入占位
    if conv and conv[0]["role"] != "user":
        conv.insert(0, {"role": "user", "content": "（开始）"})
    return "\n\n".join(system_parts), conv


def _intent_tool_call(text: str) -> Optional[Dict[str, Any]]:
    """离线演示用的轻量意图识别：把常见诉求映射到一个工具调用，
    让"帮你干活 / 技能 / 两步确认"链路在没有在线模型时也能完整演示。"""
    import os

    t = text.lower()
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    # 写操作意图优先（会触发两步确认）
    if any(k in text for k in ["写入", "新建文件", "创建文件", "保存到", "建个文件", "写个文件"]):
        return {"name": "computer_action", "arguments": _json.dumps(
            {"action": "write_file", "args": {"path": os.path.join(desktop, "小念测试.txt"), "content": "你好，这是小念帮你创建的文件。"}}, ensure_ascii=False)}
    if any(k in text for k in ["系统", "电脑配置", "什么系统"]):
        return {"name": "computer_action", "arguments": _json.dumps(
            {"action": "system_info", "args": {}}, ensure_ascii=False)}
    if any(k in text for k in ["桌面", "文件", "目录", "列出", "看看有哪些", "整理"]):
        return {"name": "computer_action", "arguments": _json.dumps(
            {"action": "list_dir", "args": {"path": desktop}}, ensure_ascii=False)}
    if any(k in text for k in ["记住", "我喜欢", "我的"]):
        return {"name": "remember", "arguments": _json.dumps(
            {"category": "preference", "key": "note", "value": text[:60]}, ensure_ascii=False)}
    if any(k in text for k in ["外卖", "吃", "饿", "午饭", "晚饭"]):
        return {"name": "use_skill", "arguments": _json.dumps({"name": "order-food"}, ensure_ascii=False)}
    if any(k in text for k in ["打车", "叫车", "去机场", "去公司", "回家"]):
        return {"name": "use_skill", "arguments": _json.dumps({"name": "hail-ride"}, ensure_ascii=False)}
    if any(k in text for k in ["周报", "日报", "总结", "汇报"]):
        return {"name": "use_skill", "arguments": _json.dumps({"name": "weekly-report"}, ensure_ascii=False)}
    return None


def _mock_chat(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    error: Optional[str] = None,
) -> ChatResult:
    """离线/降级回复。

    - 第一轮（还没有工具结果）：根据意图尝试发起一个工具调用，演示"干活"链路。
    - 已有工具结果：基于结果给出收尾回复。
    这样即便没有在线模型额度，整套"懂你→干活→确认→进化"链路也能完整跑通演示。
    """
    last_user = ""
    has_tool_result = any(m.get("role") == "tool" for m in messages)
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = str(m.get("content", ""))
            break

    prefix = "[小念·本地降级] " if error else "[小念·离线演示] "

    # 第一轮且支持工具：尝试发起意图工具调用
    if tools and not has_tool_result:
        tc = _intent_tool_call(last_user)
        if tc is not None:
            return ChatResult(
                text="",
                provider="mock",
                model="offline",
                tool_calls=[{"id": "call_" + _uuid.uuid4().hex[:8], "name": tc["name"], "arguments": tc["arguments"]}],
                usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_cny": 0.0},
                offline=True,
            )

    if has_tool_result:
        tool_out = ""
        for m in reversed(messages):
            if m.get("role") == "tool":
                tool_out = str(m.get("content", ""))[:400]
                break
        text = f"{prefix}我按你说的办好了，结果是：\n{tool_out}\n还需要我做点别的吗？"
    else:
        note = f"（在线模型暂不可用：{error[:60]}）" if error else "（当前为离线演示，配置可用的模型额度后会用真实大模型回答）"
        text = (
            f"{prefix}我听到你说：「{last_user[:120]}」。{note}\n"
            "我能帮你：整理桌面文件、记住你的偏好、点外卖/打车、写周报等。试试对我说『帮我看看桌面有哪些文件』。"
        )
    return ChatResult(
        text=text,
        provider="mock",
        model="offline",
        usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_cny": 0.0},
        offline=True,
    )


_router: Optional[ModelRouter] = None


def get_router() -> ModelRouter:
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router
