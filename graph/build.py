"""LangGraph 编排：小念的大脑主循环。

为什么用 LangGraph：需要"精确控制 + 可调试 + 生产级持久化 + 人在环"。
（Klarna / Uber / 摩根大通在生产使用。）

状态图：
  recall（查记忆，注入上下文）
    → agent（LLM 思考，可发起工具调用）
    → [有工具调用?] → tools（执行；写操作转两步确认）→ agent（回填结果继续）
    → [无工具调用?] → persist（写回记忆）→ END

用 MemorySaver 做 checkpointer，支持多轮对话状态持久化与人在环中断恢复。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from config import settings
from memory import get_memory
from model import get_router
from observability import tracer
from security import redact_pii
from capabilities.skills import get_skills

from .persona import system_prompt
from .tools import TOOLS, execute_tool

MAX_TOOL_ROUNDS = 4


class AgentState(TypedDict, total=False):
    user_id: str
    provider: Optional[str]
    messages: List[Dict[str, Any]]   # 完整对话（含 system/user/assistant/tool）
    rounds: int
    pending_confirm: Optional[Dict[str, Any]]
    reply: str
    tool_events: List[Dict[str, Any]]


def _recall_node(state: AgentState) -> AgentState:
    user_id = state.get("user_id", settings.user_id)
    msgs = state["messages"]
    last_user = ""
    for m in reversed(msgs):
        if m.get("role") == "user":
            last_user = str(m.get("content", ""))
            break

    mem = get_memory()
    context = mem.recall_context(user_id, last_user, limit=5)
    catalog = get_skills().catalog_prompt()
    sys = system_prompt(memory_context=context, skill_catalog=catalog)

    # 注入/更新 system 消息
    new_msgs = [m for m in msgs if m.get("role") != "system"]
    new_msgs.insert(0, {"role": "system", "content": sys})
    tracer.log("recall", user_id=user_id, mem_hit=bool(context))
    return {"messages": new_msgs, "tool_events": state.get("tool_events", [])}


def _agent_node(state: AgentState) -> AgentState:
    router = get_router()
    provider = state.get("provider")
    msgs = _maybe_redact(state["messages"])

    result = router.chat(msgs, provider=provider, tools=TOOLS)
    tracer.log(
        "llm",
        provider=result.provider,
        model=result.model,
        latency_ms=result.latency_ms,
        usage=result.usage,
        offline=result.offline,
    )

    assistant_msg: Dict[str, Any] = {"role": "assistant", "content": result.text}
    if result.tool_calls:
        # 回填完整 assistant 消息（含 tool_calls），满足多 provider 多轮工具调用要求
        assistant_msg["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]},
            }
            for tc in result.tool_calls
        ]

    new_messages = state["messages"] + [assistant_msg]
    return {
        "messages": new_messages,
        "reply": result.text,
        "rounds": state.get("rounds", 0),
    }


def _tools_node(state: AgentState) -> AgentState:
    user_id = state.get("user_id", settings.user_id)
    msgs = state["messages"]
    last = msgs[-1]
    tool_calls = last.get("tool_calls", [])
    events = list(state.get("tool_events", []))
    pending = None
    tool_messages: List[Dict[str, Any]] = []

    for tc in tool_calls:
        fn = tc["function"]
        name, arguments = fn["name"], fn["arguments"]
        res = execute_tool(name, arguments, user_id)
        events.append({"name": name, "arguments": arguments, "result": res.get("output", "")[:500]})
        if res.get("needs_confirm"):
            pending = {"confirm_id": res.get("confirm_id"), "summary": res.get("summary")}
        tool_messages.append(
            {
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": name,
                "content": res.get("output", ""),
            }
        )

    return {
        "messages": msgs + tool_messages,
        "rounds": state.get("rounds", 0) + 1,
        "pending_confirm": pending,
        "tool_events": events,
    }


def _persist_node(state: AgentState) -> AgentState:
    user_id = state.get("user_id", settings.user_id)
    msgs = state["messages"]
    last_user = ""
    for m in reversed(msgs):
        if m.get("role") == "user":
            last_user = str(m.get("content", ""))
            break
    reply = state.get("reply", "")
    if last_user:
        get_memory().add(
            user_id,
            [{"role": "user", "content": last_user}, {"role": "assistant", "content": reply}],
        )
    return {}


def _route_after_agent(state: AgentState) -> str:
    last = state["messages"][-1]
    if last.get("tool_calls") and state.get("rounds", 0) < MAX_TOOL_ROUNDS:
        return "tools"
    return "persist"


def _route_after_tools(state: AgentState) -> str:
    if state.get("pending_confirm"):
        return "persist"  # 等待人确认，先收尾本轮
    return "agent"


def _maybe_redact(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not settings.redact_pii:
        return messages
    out = []
    for m in messages:
        mm = dict(m)
        if isinstance(mm.get("content"), str):
            red, n = redact_pii(mm["content"])
            if n:
                tracer.log("redact", count=n)
            mm["content"] = red
        out.append(mm)
    return out


class XiaoNianAgent:
    def __init__(self) -> None:
        g = StateGraph(AgentState)
        g.add_node("recall", _recall_node)
        g.add_node("agent", _agent_node)
        g.add_node("tools", _tools_node)
        g.add_node("persist", _persist_node)
        g.set_entry_point("recall")
        g.add_edge("recall", "agent")
        g.add_conditional_edges("agent", _route_after_agent, {"tools": "tools", "persist": "persist"})
        g.add_conditional_edges("tools", _route_after_tools, {"agent": "agent", "persist": "persist"})
        g.add_edge("persist", END)
        self.graph = g.compile(checkpointer=MemorySaver())

    def chat(
        self,
        user_text: str,
        user_id: Optional[str] = None,
        provider: Optional[str] = None,
        thread_id: str = "default",
    ) -> Dict[str, Any]:
        user_id = user_id or settings.user_id
        state: AgentState = {
            "user_id": user_id,
            "provider": provider,
            "messages": [{"role": "user", "content": user_text}],
            "rounds": 0,
            "tool_events": [],
        }
        config = {"configurable": {"thread_id": f"{user_id}:{thread_id}"}}
        final = self.graph.invoke(state, config=config)
        return {
            "reply": final.get("reply", ""),
            "tool_events": final.get("tool_events", []),
            "pending_confirm": final.get("pending_confirm"),
        }


_agent: Optional[XiaoNianAgent] = None


def get_agent() -> XiaoNianAgent:
    global _agent
    if _agent is None:
        _agent = XiaoNianAgent()
    return _agent
