"""Planner 场景评测：校验计划结构和 Agent 路径，不调用真实模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from uuid import uuid4

from app.planner.service import PlannerFailure, PlannerService


@dataclass(frozen=True)
class PlannerScenario:
    name: str
    goal: str
    expected_agents: tuple[str, ...] = ()
    required_agents: tuple[str, ...] = ()
    forbidden_agents: tuple[str, ...] = ()
    expected_order: tuple[str, ...] = ()
    max_steps: int = 10


@dataclass(frozen=True)
class PlannerScenarioResult:
    name: str
    success: bool
    valid_plan: bool
    selected_agents: tuple[str, ...]
    missing_agents: tuple[str, ...] = ()
    forbidden_agents: tuple[str, ...] = ()
    extra_steps: int = 0
    order_correct: bool = True
    stable: bool | None = None
    error: str | None = None


@dataclass(frozen=True)
class PlannerEvaluationReport:
    results: tuple[PlannerScenarioResult, ...]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def success_count(self) -> int:
        return sum(result.success for result in self.results)

    @property
    def success_rate(self) -> float:
        return self.success_count / self.total if self.total else 0.0

    @property
    def valid_plan_rate(self) -> float:
        return sum(result.valid_plan for result in self.results) / self.total if self.total else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "success_count": self.success_count,
            "success_rate": self.success_rate,
            "valid_plan_rate": self.valid_plan_rate,
            "results": [
                {
                    "name": result.name,
                    "success": result.success,
                    "valid_plan": result.valid_plan,
                    "selected_agents": list(result.selected_agents),
                    "missing_agents": list(result.missing_agents),
                    "forbidden_agents": list(result.forbidden_agents),
                    "extra_steps": result.extra_steps,
                    "order_correct": result.order_correct,
                    "stable": result.stable,
                    "error": result.error,
                }
                for result in self.results
            ],
        }


class PlannerEvaluator:
    """在给定 PlannerService 上运行场景集；真实 LLM 由调用方显式传入。"""

    def evaluate(
        self,
        planner: PlannerService,
        scenarios: Iterable[PlannerScenario],
        *,
        repetitions: int = 1,
    ) -> PlannerEvaluationReport:
        if repetitions < 1 or repetitions > 5:
            raise ValueError("repetitions 必须在 1 到 5 之间")
        results = [self._evaluate_one(planner, scenario, repetitions) for scenario in scenarios]
        return PlannerEvaluationReport(tuple(results))

    def _evaluate_one(self, planner: PlannerService, scenario: PlannerScenario, repetitions: int) -> PlannerScenarioResult:
        runs: list[tuple[str, ...]] = []
        errors: list[str] = []
        valid = True
        for _ in range(repetitions):
            try:
                result = planner.plan(goal=scenario.goal, plan_id=f"eval-{uuid4().hex[:10]}")
                selected = tuple(item.agent_id for item in result.plan.work_items)
                if len(selected) > scenario.max_steps:
                    valid = False
                runs.append(selected)
            except (PlannerFailure, ValueError) as error:
                valid = False
                errors.append(str(error))
        selected = runs[0] if runs else ()
        missing = tuple(agent for agent in scenario.required_agents if agent not in selected)
        forbidden = tuple(agent for agent in selected if agent in scenario.forbidden_agents)
        expected = set(scenario.expected_agents)
        extra = sum(agent not in expected for agent in selected) if expected else 0
        order_correct = not scenario.expected_order or _is_subsequence(scenario.expected_order, selected)
        stable = len(set(runs)) == 1 if repetitions > 1 and runs else None
        success = bool(runs) and valid and not missing and not forbidden and order_correct
        return PlannerScenarioResult(
            name=scenario.name,
            success=success,
            valid_plan=valid and bool(runs),
            selected_agents=selected,
            missing_agents=missing,
            forbidden_agents=forbidden,
            extra_steps=extra,
            order_correct=order_correct,
            stable=stable,
            error=errors[0] if errors else None,
        )


def _is_subsequence(expected: tuple[str, ...], actual: tuple[str, ...]) -> bool:
    position = 0
    for agent in actual:
        if position < len(expected) and agent == expected[position]:
            position += 1
    return position == len(expected)


def default_planner_scenarios() -> tuple[PlannerScenario, ...]:
    return (
        PlannerScenario(
            name="requirement_only",
            goal="整理一个产品想法并明确需求",
            expected_agents=("requirement_agent",),
            required_agents=("requirement_agent",),
            forbidden_agents=("code_agent", "test_agent"),
            max_steps=3,
        ),
        PlannerScenario(
            name="architecture_from_requirement",
            goal="已有需求，请设计技术架构",
            expected_agents=("architecture_agent",),
            required_agents=("architecture_agent",),
            forbidden_agents=("code_agent",),
            max_steps=3,
        ),
        PlannerScenario(
            name="delivery_requires_verification",
            goal="交付一个可运行的软件并验证测试结果",
            required_agents=("code_agent", "test_agent", "review_agent"),
            expected_order=("code_agent", "test_agent", "review_agent"),
            max_steps=8,
        ),
    )
