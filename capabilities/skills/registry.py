"""Skill 系统 —— 采用 Anthropic Agent Skills 开放标准。

核心思想（三级渐进式披露 progressive disclosure）：
1. 启动级：只把每个技能的「名称 + 描述」（YAML frontmatter）载入上下文，
   10 个技能开销 < 500 token，核心永不臃肿。
2. 触发级：当某技能被判定相关时，才载入它的 SKILL.md 正文（操作步骤）。
3. 资源级：正文里引用的脚本/模板/数据文件，等真正用到时才读。

每个技能是一个目录，含 SKILL.md（--- YAML 元数据 --- + Markdown 指令）。
这样小念"会成长"：加目录即加能力，核心逻辑零改动，可跨平台移植。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

BUILTIN_DIR = Path(__file__).resolve().parent / "builtin"
USER_DIR = Path(__file__).resolve().parents[2] / "data" / "skills"


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    meta: Dict[str, Any] = field(default_factory=dict)
    _body: Optional[str] = None

    def body(self) -> str:
        """触发级：按需载入 SKILL.md 正文。"""
        if self._body is None:
            text = (self.path / "SKILL.md").read_text(encoding="utf-8")
            self._body = _strip_frontmatter(text)
        return self._body

    def resource(self, rel: str) -> str:
        """资源级：按需读取技能目录下的引用文件。"""
        p = (self.path / rel).resolve()
        if not str(p).startswith(str(self.path.resolve())):
            raise ValueError("越权访问技能目录外的文件")
        return p.read_text(encoding="utf-8")


def _parse_frontmatter(text: str) -> Dict[str, Any]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}
    try:
        import yaml

        return yaml.safe_load(m.group(1)) or {}
    except Exception:
        return {}


def _strip_frontmatter(text: str) -> str:
    return re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.DOTALL).strip()


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: Dict[str, Skill] = {}
        self.reload()

    def reload(self) -> None:
        self._skills.clear()
        for base in (BUILTIN_DIR, USER_DIR):
            if not base.exists():
                continue
            for d in sorted(base.iterdir()):
                skill_md = d / "SKILL.md"
                if d.is_dir() and skill_md.exists():
                    meta = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
                    name = meta.get("name", d.name)
                    self._skills[name] = Skill(
                        name=name,
                        description=meta.get("description", ""),
                        path=d,
                        meta=meta,
                    )

    # ---- 启动级：只暴露名称+描述 ----
    def catalog(self) -> List[Dict[str, str]]:
        return [{"name": s.name, "description": s.description} for s in self._skills.values()]

    def catalog_prompt(self) -> str:
        if not self._skills:
            return ""
        lines = ["【可用技能（需要时调用 use_skill 载入详细步骤）】"]
        for s in self._skills.values():
            lines.append(f"- {s.name}: {s.description}")
        return "\n".join(lines)

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    # ---- 触发级：载入正文 ----
    def load(self, name: str) -> Optional[str]:
        s = self._skills.get(name)
        if s is None:
            # 容错：按描述模糊匹配
            for k, v in self._skills.items():
                if name in k or name in v.description:
                    s = v
                    break
        return s.body() if s else None

    # ---- 安装新技能（会成长）----
    def install_from_text(self, name: str, skill_md: str) -> Skill:
        USER_DIR.mkdir(parents=True, exist_ok=True)
        d = USER_DIR / re.sub(r"[^a-zA-Z0-9_-]", "-", name)
        d.mkdir(exist_ok=True)
        (d / "SKILL.md").write_text(skill_md, encoding="utf-8")
        self.reload()
        return self._skills.get(name) or self._skills[d.name]


_skills: Optional[SkillRegistry] = None


def get_skills() -> SkillRegistry:
    global _skills
    if _skills is None:
        _skills = SkillRegistry()
    return _skills
