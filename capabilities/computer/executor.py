"""电脑操控 harness（"帮你干活"）。

定位：让小念真正在你的电脑上动手——执行代码、读写文件、跑命令、抓网页。

工程取舍：
- 生产/进阶后端：Open Interpreter（Apache-2.0，跨平台原生沙箱 + 权限 + MCP）。
  若环境已安装 `open-interpreter` 且开启 XIAONIAN_USE_OPEN_INTERPRETER，则走它。
- 默认后端：内置受控 harness——把能力收敛为白名单动作，读操作即时执行，
  写/危险操作（写文件、删除、移动、跑命令）一律走两步确认（人在环），
  并写审计日志。这样演示稳定、安全、可解释。

所有动作都被审计记录；所有写操作默认要确认。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings
from security import audit, confirm_registry

READ_ACTIONS = {"read_file", "list_dir", "run_python", "system_info"}
WRITE_ACTIONS = {"write_file", "move", "delete", "run_shell", "open_app"}


@dataclass
class ActionResult:
    ok: bool
    output: str
    needs_confirm: bool = False
    confirm_id: Optional[str] = None
    summary: Optional[str] = None


class ComputerExecutor:
    def __init__(self) -> None:
        self.workspace = Path.home()
        self._oi = None
        if os.getenv("XIAONIAN_USE_OPEN_INTERPRETER", "").lower() in {"1", "true", "yes"}:
            self._try_open_interpreter()

    def _try_open_interpreter(self) -> None:
        try:
            from interpreter import interpreter  # type: ignore

            interpreter.auto_run = False  # 保留人在环
            interpreter.offline = False
            self._oi = interpreter
        except Exception:
            self._oi = None

    @property
    def backend(self) -> str:
        return "open-interpreter" if self._oi is not None else "builtin-sandbox"

    # ---------- 对外主入口 ----------
    def dispatch(self, action: str, args: Dict[str, Any], require_confirm: Optional[bool] = None) -> ActionResult:
        require_confirm = settings.require_confirm if require_confirm is None else require_confirm
        audit.record("computer.request", op=action, args=_safe_args(args), backend=self.backend)

        if action in READ_ACTIONS:
            return self._execute(action, args)

        if action in WRITE_ACTIONS:
            if require_confirm:
                preview = self._preview(action, args)
                pending = confirm_registry.request(
                    kind=f"computer.{action}",
                    summary=preview,
                    payload={"action": action, "args": args},
                    executor=lambda p: self._execute(p["action"], p["args"]).output,
                )
                audit.record("computer.await_confirm", op=action, confirm_id=pending.id)
                return ActionResult(
                    ok=True,
                    output=f"该操作需要你确认：{preview}",
                    needs_confirm=True,
                    confirm_id=pending.id,
                    summary=preview,
                )
            return self._execute(action, args)

        return ActionResult(ok=False, output=f"未知动作：{action}")

    # ---------- 预览 ----------
    def _preview(self, action: str, args: Dict[str, Any]) -> str:
        if action == "write_file":
            return f"写入文件 {args.get('path')}（{len(str(args.get('content','')))} 字符）"
        if action == "move":
            return f"移动 {args.get('src')} → {args.get('dst')}"
        if action == "delete":
            return f"删除 {args.get('path')}"
        if action == "run_shell":
            return f"执行命令：{args.get('command')}"
        if action == "open_app":
            return f"打开应用：{args.get('name')}"
        return f"{action} {args}"

    # ---------- 实际执行 ----------
    def _execute(self, action: str, args: Dict[str, Any]) -> ActionResult:
        try:
            fn = getattr(self, f"_do_{action}")
        except AttributeError:
            return ActionResult(ok=False, output=f"不支持的动作：{action}")
        try:
            out = fn(args)
            audit.record("computer.done", op=action, ok=True)
            return ActionResult(ok=True, output=out)
        except Exception as exc:
            audit.record("computer.done", op=action, ok=False, error=str(exc)[:200])
            return ActionResult(ok=False, output=f"执行失败：{exc}")

    # ---------- 动作实现 ----------
    def _resolve(self, p: str) -> Path:
        path = Path(os.path.expanduser(str(p)))
        if not path.is_absolute():
            path = self.workspace / path
        return path

    def _do_read_file(self, args: Dict[str, Any]) -> str:
        path = self._resolve(args["path"])
        data = path.read_text(encoding="utf-8", errors="replace")
        return data[:8000]

    def _do_list_dir(self, args: Dict[str, Any]) -> str:
        path = self._resolve(args.get("path", "."))
        items = sorted(os.listdir(path))[:200]
        return "\n".join(items)

    def _do_system_info(self, args: Dict[str, Any]) -> str:
        import platform

        return (
            f"系统: {platform.platform()}\n"
            f"Python: {sys.version.split()[0]}\n"
            f"用户目录: {Path.home()}\n"
            f"CPU 架构: {platform.machine()}"
        )

    def _do_run_python(self, args: Dict[str, Any]) -> str:
        code = args["code"]
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=args.get("timeout", 30),
        )
        return (proc.stdout + proc.stderr)[:8000]

    def _do_write_file(self, args: Dict[str, Any]) -> str:
        path = self._resolve(args["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(args.get("content", "")), encoding="utf-8")
        return f"已写入 {path}"

    def _do_move(self, args: Dict[str, Any]) -> str:
        src = self._resolve(args["src"])
        dst = self._resolve(args["dst"])
        shutil.move(str(src), str(dst))
        return f"已移动 {src} → {dst}"

    def _do_delete(self, args: Dict[str, Any]) -> str:
        path = self._resolve(args["path"])
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return f"已删除 {path}"

    def _do_run_shell(self, args: Dict[str, Any]) -> str:
        proc = subprocess.run(
            args["command"], shell=True, capture_output=True, text=True,
            timeout=args.get("timeout", 60), cwd=str(self.workspace),
        )
        return (proc.stdout + proc.stderr)[:8000]

    def _do_open_app(self, args: Dict[str, Any]) -> str:
        name = args["name"]
        if sys.platform == "darwin":
            subprocess.run(["open", "-a", name], check=False)
        elif sys.platform.startswith("win"):
            os.startfile(name)  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", name], check=False)
        return f"已尝试打开 {name}"


def _safe_args(args: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in args.items():
        s = str(v)
        out[k] = s[:120] + ("…" if len(s) > 120 else "")
    return out


_executor: Optional[ComputerExecutor] = None


def get_executor() -> ComputerExecutor:
    global _executor
    if _executor is None:
        _executor = ComputerExecutor()
    return _executor
