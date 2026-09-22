"""Policy 运行时模块：执行前指导与确定性质量策略的组合入口。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.policy.quality import PolicyGuidance, ProjectQualityPolicy

if TYPE_CHECKING:
    from app.bootstrap.runtime import ProjectOSContainer
    from app.orchestration.work_item import WorkItem


class PolicyModule:
    """与 SkillModule 并列的 Policy 组合层。

    PolicyModule 不保存 Agent 自由文本，也不授予工具权限；它只把已有 Policy
    实现组合成执行前指导和执行后判定两个入口。
    """

    def __init__(self, project_path: str) -> None:
        self.project_path = project_path
        self.project_quality = ProjectQualityPolicy()

    def guidance_for(self, item: "WorkItem") -> PolicyGuidance | None:
        if not item.policy_refs and item.agent_id != "code_agent":
            return None
        references = item.policy_refs
        return self.project_quality.preflight(
            allowed_paths=item.allowed_paths,
            required_paths=item.required_paths,
        ) if references else None

    def evaluate_project(self):
        return self.project_quality.evaluate(self.project_path)


def install(container: "ProjectOSContainer") -> None:
    container.policies = PolicyModule(container.project_path)
