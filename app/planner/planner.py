"""CrewAI Planner runtime，只负责生成计划草案。"""

from __future__ import annotations

from typing import Protocol

from crewai import Agent, Task

from app.llm.factory import build_llm


class PlannerRuntime(Protocol):
    """PlannerService 所需的最小 LLM 运行契约，便于独立测试。"""

    def generate(self, prompt: str) -> str:
        ...


class CrewAIPlannerRuntime:
    """无工具 CrewAI Agent，用于输出严格 JSON 的 PlanDraft。"""

    def generate(self, prompt: str) -> str:
        agent = Agent(
            role="执行计划编排者",
            goal="在已注册 Agent 合同范围内生成最小、可执行的任务计划",
            backstory=_BACKSTORY,
            llm=build_llm(),
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
        return str(agent.execute_task(task))


_BACKSTORY = """\
你是 ProjectOS 的 Planner，不是领域业务执行者。

你只能根据输入中的 goal、artifact 元数据、可用 Agent 合同和模板摘要进行编排。
你不能调用工具、不能读取业务文件内容、不能创建未提供的 Agent，也不能生成 node id、
output key、文件路径或 Python 代码。

必须只输出如下 JSON：
{
  "rationale": "为什么选择这些步骤",
  "template_hint_id": "可选的已知模板 id 或 null",
  "steps": [
    {
      "agent_id": "已提供的 Agent id",
      "objective": "该 Agent 本次应完成的具体目标",
      "depends_on": ["同一 steps 内的前置 Agent id"]
    }
  ]
}

规则：
1. 每个 Agent 最多出现一次。
2. depends_on 只能引用同一计划中已选择的其他 Agent。
3. 已存在的 artifact 通常表示对应文档工作可跳过；缺失 artifact 不代表必须运行所有 Agent。
4. implementation.md 是实现摘要，不是代码完成证据。若目标要求交付可运行软件，且
   workspace.implementation_file_count 为 0，必须选择 code_agent；后续需要验证或交付
   审查时，test_agent 和 review_agent 必须依赖 code_agent 并按顺序出现。
5. runtime.manifest_exists 为 false 时，选择 code_agent 或 test_agent 前必须选择
   bootstrap_agent；Bootstrap 负责声明 runtime，不执行依赖安装。
6. runtime.dependencies_configured 为 true 而 dependency_cache_ready 为 false 时，
   说明需要项目所有者批准依赖解析；不要假设测试可运行。
7. 优先产出完成目标所需的最小步骤集合。
8. 不输出任何 JSON 之外的文字。"""
