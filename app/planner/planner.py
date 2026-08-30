"""CrewAI Planner runtime，只负责生成计划草案。"""

from __future__ import annotations

import hashlib
import os
from threading import Lock
from typing import Protocol

from crewai import Agent, Task

from app.llm.factory import build_llm
from app.llm.config import LLMSelection


class PlannerRuntime(Protocol):
    """PlannerService 所需的最小 LLM 运行契约，便于独立测试。"""

    def generate(self, prompt: str) -> str:
        ...


class CrewAIPlannerRuntime:
    """无工具 CrewAI Agent，用于输出严格 JSON 的 PlanDraft。"""

    def __init__(
        self,
        *,
        cache_enabled: bool | None = None,
        llm_selection: LLMSelection | None = None,
    ) -> None:
        self._llm = None
        self._lock = Lock()
        self._cache_enabled = (
            os.environ.get("PROJECTOS_PLANNER_CACHE", "0") == "1"
            if cache_enabled is None
            else cache_enabled
        )
        self._cache: dict[str, str] = {}
        self._llm_selection = llm_selection

    def generate(self, prompt: str) -> str:
        cache_key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if self._cache_enabled:
            with self._lock:
                cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
        with self._lock:
            if self._llm is None:
                kwargs = {"temperature": 0.0, "seed": 0}
                if self._llm_selection is not None:
                    kwargs["selection"] = self._llm_selection
                self._llm = build_llm(**kwargs)
        agent = Agent(
            role="执行计划编排者",
            goal="在已注册 Agent 合同范围内生成最小、可执行的任务计划",
            backstory=_BACKSTORY,
            llm=self._llm,
            tools=[],
            max_iter=1,
            verbose=False,
            allow_delegation=False,
        )
        task = Task(
            description=prompt,
            expected_output="严格符合要求的单个 JSON 对象，不含 Markdown 代码块。",
            agent=agent,
        )
        result = str(agent.execute_task(task))
        if self._cache_enabled:
            with self._lock:
                self._cache[cache_key] = result
        return result


_BACKSTORY = """\
你是 ProjectOS 的 Planner，不是领域业务执行者。

你只能根据输入中的 goal、artifact 元数据、可用 Agent 合同和模板节点/默认依赖进行编排。
你不能调用工具、不能读取业务文件内容、不能创建未提供的 Agent，也不能生成 node id、
output key、文件路径或 Python 代码。

必须只输出如下 JSON：
{
  "rationale": "为什么选择这些步骤",
  "template_hint_id": "可选的已知模板 id 或 null",
  "steps": [
    {
      "ref": "该步骤在本次计划内唯一的临时标识",
      "agent_id": "已提供的 Agent id",
      "objective": "该 Agent 本次应完成的具体目标",
      "depends_on": ["同一 steps 内前置步骤的 ref"],
      "acceptance_criteria": ["可选的、可检查的完成标准"],
      "constraints": ["可选的、必须遵守的技术或权限约束"],
      "non_goals": ["可选的、本步骤明确不做的内容"]
    }
  ]
}

若选择的是带受控执行权限的模板（例如 architecture_parallel、architecture_layered），模板会由系统完整编译，
此时 steps 可以为空；不要自行填写 execution_mode、slot、路径、候选或发布权限。

规则：
1. 同一 Agent 可以出现多次，但每个 ref 必须唯一，并且每次 objective 都必须是可独立验收的窄任务。
2. depends_on 只能引用同一计划中已选择的其他步骤 ref。
3. 已存在的 artifact 通常表示对应文档工作可跳过；但空项目若目标同时要求实现、测试或交付审查，
   必须选择受控模板 project_delivery，不能只生成 implementation -> tests -> review 的捷径。
4. implementation.md 是实现摘要，不是代码完成证据。若目标要求交付可运行软件，且
   workspace.implementation_file_count 为 0，必须选择 code_agent；后续需要验证或交付
   审查时，test_agent 和 review_agent 必须依赖 code_agent 并按顺序出现。
5. runtime.manifest_exists 为 false 时，选择 code_agent 或 test_agent 前必须选择
   bootstrap_agent；Bootstrap 负责声明 runtime，不执行依赖安装。
6. runtime.dependencies_configured 为 true 而 dependency_cache_ready 为 false 时，
   说明需要项目所有者批准依赖解析；不要假设测试可运行。
7. project_contract.exists 为 true 时，implementation/code_agent 的 objective 或 constraints
   必须明确遵守契约层和 path_mapping；test_agent 必须覆盖 required_test_types。
   project_contract.exists 为 false 且目标要求代码交付时，必须先选择 architecture_agent，随后由
   architecture_contract_agent 读取已发布架构并保存实现合同，不得把分层标准留给代码节点临时猜测。
8. 优先产出完成目标所需的最小步骤集合。
9. 默认模板的依赖会由系统自动加入。只有确实不适用时，才在
   template_dependency_overrides 中提供 predecessor_agent_id、successor_agent_id 和原因。
10. 有多个同类前置步骤时，必须用 depends_on 明确选择当前步骤依赖哪一个；系统不会猜测。
11. Agent 的 max_parallel_instances 只是调度容量，不代表可共享写入同一文件。不要为了并行重复创建会写入同一 artifact 的步骤。
12. constraints 只填写本步骤必须遵守的技术、范围或权限约束；non_goals 只填写本步骤明确不做的内容。
13. 不输出任何 JSON 之外的文字。"""
