"""小念可用工具定义（OpenAI function-calling schema）+ 执行分发。

工具是编排层与能力层的接口：
- computer_action：在电脑上干活（读/写/跑命令，写操作走两步确认）
- use_skill：载入某个技能的详细步骤（Agent Skills 三级披露的"触发级"）
- remember：把结构化事实写入记忆（越用越懂你）
- recall：检索记忆
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from capabilities.computer import get_executor
from capabilities.skills import get_skills
from config import settings
from memory import get_memory
from security import audit

TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "computer_action",
            "description": "在主人的电脑上执行一个动作来真正把活干完。读操作即时执行；写/危险操作会自动转为两步确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "read_file", "list_dir", "system_info", "run_python",
                            "write_file", "move", "delete", "run_shell", "open_app",
                        ],
                    },
                    "args": {"type": "object", "description": "动作参数，如 {path, content, command, code, src, dst, name}"},
                },
                "required": ["action", "args"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "use_skill",
            "description": "当任务匹配某个已注册技能时，载入该技能的详细步骤再据此执行（如点外卖/打车/写周报）。",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "把关于主人的结构化事实写入长期记忆，让自己越来越懂他。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "如 diet/habit/relation/address/preference/health"},
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["category", "key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "从长期记忆中检索与当前问题相关的信息。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]


def execute_tool(name: str, arguments: str, user_id: str) -> Dict[str, Any]:
    """执行一个工具调用，返回结构化结果（含是否需要确认）。"""
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
    except Exception:
        args = {}

    audit.record("tool.call", name=name, args=args, user_id=user_id)

    if name == "computer_action":
        execu = get_executor()
        res = execu.dispatch(args.get("action", ""), args.get("args", {}) or {})
        return {
            "output": res.output,
            "needs_confirm": res.needs_confirm,
            "confirm_id": res.confirm_id,
            "summary": res.summary,
        }

    if name == "use_skill":
        skills = get_skills()
        body = skills.load(args.get("name", ""))
        if body is None:
            return {"output": f"未找到技能：{args.get('name')}。可用：" + ", ".join(s['name'] for s in skills.catalog())}
        return {"output": f"已载入技能《{args.get('name')}》步骤：\n{body}"}

    if name == "remember":
        get_memory().remember_fact(
            user_id, args.get("category", "misc"), args.get("key", ""), args.get("value", "")
        )
        return {"output": f"好，我记住了：{args.get('category')}.{args.get('key')} = {args.get('value')}"}

    if name == "recall":
        hits = get_memory().search(user_id, args.get("query", ""), limit=5)
        if not hits:
            return {"output": "我还没有相关记忆。"}
        return {"output": "\n".join(f"- {h.get('text','')}" for h in hits)}

    return {"output": f"未知工具：{name}"}
