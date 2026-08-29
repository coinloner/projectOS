"""Skill 领域模块：目录、项目覆盖和 Agent 绑定。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING

from app.skill.store import SkillStore

if TYPE_CHECKING:
    from app.bootstrap.runtime import ProjectOSContainer


_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class AgentSkillAssignment:
    agent_id: str
    refs: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {"agent_id": self.agent_id, "refs": list(self.refs)}


class SkillModule:
    """面向 Pro 用户的 Skill 配置模块。

    Skill 内容由 SkillStore 解析；本模块只维护 Agent 到 Skill ref 的绑定，
    不修改 Policy，也不授予额外工具权限。
    """

    relative_path = ".projectos/skills/assignments.json"

    def __init__(self, project_path: str) -> None:
        self.project_path = str(Path(project_path).resolve())
        self.store = SkillStore(self.project_path)
        self._path = Path(self.project_path) / self.relative_path

    def list_skills(self) -> tuple[dict[str, object], ...]:
        result = []
        for ref in self.store.refs():
            document = self.store.load(ref)
            result.append({"ref": ref, "title": document.title, "source": document.source})
        return tuple(result)

    def assignments(self) -> tuple[AgentSkillAssignment, ...]:
        if not self._path.is_file():
            return ()
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Skill assignments 必须是对象")
        result = []
        for agent_id, refs in raw.items():
            if not isinstance(agent_id, str) or not isinstance(refs, list):
                raise ValueError("Skill assignments 格式无效")
            result.append(AgentSkillAssignment(agent_id, tuple(str(ref) for ref in refs)))
        return tuple(sorted(result, key=lambda item: item.agent_id))

    def refs_for(self, agent_id: str) -> tuple[str, ...]:
        for assignment in self.assignments():
            if assignment.agent_id == agent_id:
                return assignment.refs
        return ()

    def resolve(self, agent_id: str, explicit_refs: tuple[str, ...] = ()) -> tuple[str, ...]:
        # LLM-generated contracts sometimes put a natural-language label in
        # skill_refs (for example ``领域建模``).  That is guidance text, not a
        # Skill identifier; ignore it here so one malformed hint cannot abort
        # an otherwise valid implementation plan. Explicit user assignments
        # still fail later if they use an unknown ASCII ref.
        refs = [
            ref for ref in [*explicit_refs, *self.refs_for(agent_id)]
            if _SAFE_REF.fullmatch(str(ref).strip())
        ]
        return tuple(dict.fromkeys(refs))

    def assign(self, agent_id: str, refs: tuple[str, ...] | list[str]) -> AgentSkillAssignment:
        if not agent_id or not agent_id.strip():
            raise ValueError("agent_id 不能为空")
        normalized = tuple(dict.fromkeys(str(ref).strip() for ref in refs if str(ref).strip()))
        if len(normalized) > 12:
            raise ValueError("单个 Agent 最多绑定 12 个 Skill")
        # 绑定时立即校验，避免运行到中途才发现 Skill ref 不存在。
        self.store.load_many(normalized)
        assignments = {item.agent_id: item.refs for item in self.assignments()}
        assignments[agent_id] = normalized
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(assignments, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return AgentSkillAssignment(agent_id, normalized)

    def save_project_skill(self, ref: str, content: str) -> dict[str, object]:
        if not _SAFE_REF.fullmatch(ref):
            raise ValueError("Skill ref 只能包含字母、数字、点、下划线和短横线")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Skill 内容不能为空")
        if len(content) > 16_000:
            raise ValueError("Skill 内容不能超过 16000 字符")
        path = self._path.parent / f"{ref}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.strip() + "\n", encoding="utf-8")
        document = self.store.load(ref)
        return {"ref": ref, "title": document.title, "source": document.source}

    def render_for(self, agent_id: str, explicit_refs: tuple[str, ...] = ()) -> tuple[str, tuple[str, ...]]:
        refs = self.resolve(agent_id, explicit_refs)
        # WorkItem skill_refs may be suggested by an LLM contract.  Only
        # registered IDs are executable references; unknown suggestions are
        # ignored instead of aborting an otherwise valid run. User assignments
        # are validated eagerly by assign()/SkillStore.load_many().
        known = set(self.store.refs())
        refs = tuple(ref for ref in refs if ref in known)
        return self.store.render(refs), refs


def install(container: "ProjectOSContainer") -> None:
    container.skills = SkillModule(container.project_path)
