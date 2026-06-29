"""场景评测 runner —— 计算小念在真实生活场景上的端到端成功率。

企业级要求：能力要可度量。本 runner 直接驱动 LangGraph Agent，对每个场景跑一遍，
用断言检查关键行为（调对工具 / 触发确认 / 命中技能 / 画像落地），输出成功率报告。

用法：
    python -m eval.runner
（无需启动 daemon；直接调用内核。离线降级模式下也能跑通，验证链路完整性。）
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

from config import settings
from graph import get_agent
from memory import get_memory

ROOT = Path(__file__).resolve().parent


def _check(result: Dict[str, Any], checks: Dict[str, Any], user_id: str) -> List[str]:
    fails: List[str] = []
    tools = [t["name"] for t in result.get("tool_events", [])]
    reply = result.get("reply", "") or ""

    if "tool_any" in checks:
        if not any(t in tools for t in checks["tool_any"]):
            fails.append(f"期望调用工具之一 {checks['tool_any']}，实际 {tools}")
    if checks.get("needs_confirm"):
        if not result.get("pending_confirm"):
            fails.append("期望触发两步确认，但没有")
    if checks.get("reply_not_empty") and not reply.strip():
        fails.append("回复为空")
    if "reply_contains" in checks and checks["reply_contains"] not in reply:
        fails.append(f"回复未包含「{checks['reply_contains']}」")
    if "profile_contains" in checks:
        prof = get_memory().get_profile(user_id)["profile"]
        flat = str(prof)
        if checks["profile_contains"] not in flat:
            fails.append(f"画像未包含「{checks['profile_contains']}」")
    return fails


def run() -> int:
    data = yaml.safe_load((ROOT / "scenarios.yaml").read_text(encoding="utf-8"))
    scenarios = data["scenarios"]
    agent = get_agent()
    mem = get_memory()
    user_id = "eval_user"

    passed = 0
    print("=" * 64)
    print(f"小念 场景评测 · 共 {len(scenarios)} 个场景 · 引擎 memory={mem.engine}")
    print("=" * 64)

    for sc in scenarios:
        if "setup_remember" in sc:
            s = sc["setup_remember"]
            mem.remember_fact(user_id, s["category"], s["key"], s["value"])
        result = agent.chat(sc["message"], user_id=user_id, thread_id=sc["id"])
        fails = _check(result, sc.get("checks", {}), user_id)
        ok = not fails
        passed += int(ok)
        mark = "✅ PASS" if ok else "❌ FAIL"
        print(f"{mark}  [{sc['id']}] {sc['desc']}")
        for f in fails:
            print(f"        - {f}")

    rate = passed / len(scenarios) * 100
    print("=" * 64)
    print(f"场景成功率: {passed}/{len(scenarios)} = {rate:.1f}%")
    print("=" * 64)
    return 0 if passed == len(scenarios) else 1


if __name__ == "__main__":
    sys.exit(run())
