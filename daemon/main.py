"""小念 XiaoNian — 本地 daemon（FastAPI）。

这是住在你电脑里的服务进程：对外提供对话、记忆、技能、电脑操控、主动关心、
宠物状态、可观测等接口，并托管本地 Web UI。所有数据留在本机。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import settings
from capabilities.pet import get_pet
from capabilities.skills import get_skills
from graph import get_agent
from memory import get_memory
from model import get_router
from observability import tracer
from security import audit, confirm_registry

from .life_state import life_state

app = FastAPI(title="小念 XiaoNian", version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# ---------- 数据模型 ----------
class ChatRequest(BaseModel):
    message: str
    provider: Optional[str] = None
    thread_id: str = "default"
    user_id: Optional[str] = None


class ConfirmRequest(BaseModel):
    confirm_id: str
    approve: bool


class RememberRequest(BaseModel):
    category: str
    key: str
    value: str
    user_id: Optional[str] = None


class MoodRequest(BaseModel):
    workload: Optional[float] = None
    fatigue: Optional[float] = None
    mood: Optional[str] = None
    sleep_hours: Optional[float] = None


# ---------- 启动 ----------
@app.on_event("startup")
def _startup() -> None:
    get_memory()
    get_agent()
    _wire_proactive()
    if settings.proactive_enabled:
        from proactive import get_proactive

        get_proactive().start()
    audit.record("daemon.start", provider=settings.default_provider)


def _wire_proactive() -> None:
    from proactive import get_proactive

    engine = get_proactive()
    engine.get_life_state = life_state.snapshot

    def compose(kind: str, life: Dict[str, Any]) -> str:
        # 用 LLM 生成更贴合状态的关心文案（失败则用默认）
        router = get_router()
        prompt = (
            f"你是小念，现在要主动关心主人。场景：{kind}。"
            f"主人当前状态：{json.dumps(life, ensure_ascii=False)}。"
            "用一句温暖、简短、不打扰的话主动关心他，并可顺带提议帮他做点什么。只输出这句话。"
        )
        res = router.chat([{"role": "user", "content": prompt}], max_tokens=120)
        return (res.text or "").strip()

    engine.compose = compose

    def on_message(msg) -> None:
        get_pet().set_emotion(msg.emotion, msg.text)

    engine.on_message = on_message


def _refresh_pet() -> Dict[str, Any]:
    ls = life_state.snapshot()
    snap = get_pet().update_from_signals(
        workload=ls["workload"], mood=ls["mood"], fatigue=ls["fatigue"],
        sleep_hours=ls.get("sleep_hours"),
    )
    return snap.__dict__


# ---------- 健康/状态 ----------
@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "name": "小念 XiaoNian",
        "provider": settings.default_provider,
        "memory_engine": get_memory().engine,
        "computer_backend": __import__("capabilities.computer", fromlist=["get_executor"]).get_executor().backend,
    }


@app.get("/api/providers")
def providers() -> Dict[str, Any]:
    return {"providers": get_router().available_providers(), "default": settings.default_provider}


@app.post("/api/provider")
def set_provider(body: Dict[str, str]) -> Dict[str, Any]:
    name = body.get("provider", "")
    if name in settings.providers:
        settings.default_provider = name
        audit.record("provider.switch", provider=name)
    return {"default": settings.default_provider}


# ---------- 对话 ----------
@app.post("/api/chat")
def chat(req: ChatRequest) -> Dict[str, Any]:
    life_state.record_activity()
    result = get_agent().chat(
        req.message, user_id=req.user_id, provider=req.provider, thread_id=req.thread_id
    )
    result["pet"] = _refresh_pet()
    return result


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    """SSE：先推工具事件，再分块推回复，最后推宠物状态。"""
    life_state.record_activity()

    def gen():
        result = get_agent().chat(
            req.message, user_id=req.user_id, provider=req.provider, thread_id=req.thread_id
        )
        for ev in result.get("tool_events", []):
            yield _sse("tool", ev)
        if result.get("pending_confirm"):
            yield _sse("confirm", result["pending_confirm"])
        reply = result.get("reply", "")
        for i in range(0, len(reply), 24):
            yield _sse("delta", {"text": reply[i : i + 24]})
        yield _sse("pet", _refresh_pet())
        yield _sse("done", {"reply": reply})

    return StreamingResponse(gen(), media_type="text/event-stream")


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------- 两步确认 ----------
@app.get("/api/confirm/pending")
def confirm_pending() -> Dict[str, Any]:
    return {"pending": confirm_registry.list_pending()}


@app.post("/api/confirm")
def confirm(req: ConfirmRequest) -> Dict[str, Any]:
    try:
        action = confirm_registry.resolve(req.confirm_id, req.approve)
    except KeyError:
        return {"error": "确认项不存在或已处理"}
    audit.record("confirm.resolve", confirm_id=req.confirm_id, approve=req.approve)
    return {"status": action.status, "result": action.result}


# ---------- 记忆 ----------
@app.get("/api/memory/profile")
def memory_profile(user_id: Optional[str] = None) -> Dict[str, Any]:
    return get_memory().get_profile(user_id or settings.user_id)


@app.post("/api/memory/remember")
def memory_remember(req: RememberRequest) -> Dict[str, Any]:
    get_memory().remember_fact(req.user_id or settings.user_id, req.category, req.key, req.value)
    return {"ok": True}


@app.get("/api/memory/search")
def memory_search(q: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    return {"results": get_memory().search(user_id or settings.user_id, q, limit=8)}


# ---------- 技能 ----------
@app.get("/api/skills")
def skills() -> Dict[str, Any]:
    return {"skills": get_skills().catalog()}


@app.get("/api/skills/{name}")
def skill_detail(name: str) -> Dict[str, Any]:
    body = get_skills().load(name)
    return {"name": name, "body": body}


# ---------- 宠物 ----------
@app.get("/api/pet")
def pet() -> Dict[str, Any]:
    return _refresh_pet()


# ---------- 主动关心 ----------
@app.get("/api/proactive/pending")
def proactive_pending() -> Dict[str, Any]:
    from proactive import get_proactive

    return {"messages": get_proactive().drain()}


@app.post("/api/proactive/demo")
def proactive_demo(body: Dict[str, str]) -> Dict[str, Any]:
    from proactive import get_proactive

    msg = get_proactive().trigger_demo(body.get("kind", "evening"))
    if msg:
        get_pet().set_emotion(msg.get("emotion", "caring"), msg.get("text", ""))
    return {"message": msg, "pet": get_pet().as_dict()}


# ---------- 生活状态 ----------
@app.get("/api/life")
def life() -> Dict[str, Any]:
    return life_state.snapshot()


@app.post("/api/life")
def set_life(req: MoodRequest) -> Dict[str, Any]:
    kw = {k: v for k, v in req.dict().items() if v is not None}
    life_state.set_manual(**kw)
    return {"life": life_state.snapshot(), "pet": _refresh_pet()}


# ---------- 可观测 ----------
@app.get("/api/observability")
def observability() -> Dict[str, Any]:
    return {"summary": tracer.summary(), "recent": tracer.recent(40)}


@app.get("/api/audit")
def audit_log() -> Dict[str, Any]:
    return {"audit": audit.tail(60)}


# ---------- 静态前端 ----------
import os

_static = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "static")
if os.path.isdir(_static):
    app.mount("/", StaticFiles(directory=_static, html=True), name="static")
