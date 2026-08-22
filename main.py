import argparse
from pathlib import Path

from app.project.project import Project
from app.sandbox.controller import SandboxController
from app.bootstrap.runtime import build_container
from app.orchestration.runner import GraphRunStatus
from app.planner.service import PlannerFailure


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(prog="projectos", description="半托管多 Agent 项目交付 CLI")
    parser.add_argument("--project", required=True, help="项目目录，例如 ./projects/todo_demo")
    parser.add_argument("--goal", required=True, help="本次交付目标")
    parser.add_argument("--workflow", help="可选受控 Workflow id；不传则使用动态 Planner")
    parser.add_argument("--plan-id", default="cli-run", help="计划标识")
    args = parser.parse_args(argv)
    project_path = str(Path(args.project).resolve())
    project = Path(project_path)
    if not project.is_dir():
        Project(name=project.name, base_dir=str(project.parent)).create()

    container = build_container(project_path)
    preflight = SandboxController().preflight(project_path)
    if not preflight["ok"]:
        print(f"Sandbox 预检：{preflight.get('status')} - {preflight.get('message')}")
    goal = args.goal
    planner = container.planner
    try:
        if args.workflow:
            planning = planner.plan_controlled_workflow(
                goal=goal, plan_id=args.plan_id, workflow_id=args.workflow
            )
        else:
            planning = planner.plan(goal=goal, plan_id=args.plan_id)
    except PlannerFailure as error:
        print(f"❌ 规划失败: {error}")
        return 2

    plan = planning.plan
    print("规划工作项: " + " -> ".join(item.agent_id for item in plan.work_items))
    runner = container.runner
    container.traces.record_plan_baseline(plan)
    result = runner.run(plan)
    for repair_attempt in range(1, 3):
        if result.status is not GraphRunStatus.NEEDS_REPLAN:
            break
        if result.failure_signal is None:
            break
        try:
            planning = planner.plan_repair(
                previous_plan=plan,
                failure=result.failure_signal,
                plan_id=f"{plan.id}-repair-{repair_attempt}",
            )
        except PlannerFailure as error:
            print(f"❌ 修复规划失败: {error}")
            break
        plan = planning.plan
        result = runner.run(plan)

    print("=" * 60)
    print(result.state.artifacts.get("review") or result.error or "流程未返回内容")
    print("=" * 60)
    if result.status is GraphRunStatus.COMPLETED:
        print(f"✅ 流程完成，产物位于 {project_path}")
    elif result.status is GraphRunStatus.WAITING_FOR_CAPABILITY_APPROVAL:
        names = ", ".join(source.name for source in result.candidate_sources)
        print(f"等待授权使用外部能力，可选来源: {names}；请通过 API capabilities/approve 恢复")
    else:
        print(f"流程状态: {result.status.value}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
