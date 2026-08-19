from app.project.project import Project
from app.bootstrap.runtime import build_container
from app.orchestration.runner import GraphRunStatus
from app.planner.service import PlannerFailure


def main():
    project_path = "./projects/Mega_Shit"

    # 准备项目
    if not Project.exists("Mega_Shit", "./projects"):
        Project(name="Mega_Shit", base_dir="./projects").create()
        print()

    container = build_container(project_path)
    goal = "我要做一个检测粪便健康的网站，请完成需求、架构、任务、首版代码、基础测试和交付审查。"
    planner = container.planner
    try:
        planning = planner.plan(goal=goal, plan_id="project-delivery-demo")
    except PlannerFailure as error:
        print(f"❌ 规划失败: {error}")
        return

    plan = planning.plan
    print("规划工作项: " + " -> ".join(item.agent_id for item in plan.work_items))
    runner = container.runner
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
        print(f"✅ 已生成并审查项目交付，产物位于 {project_path}")
    elif result.status is GraphRunStatus.WAITING_FOR_CAPABILITY_APPROVAL:
        names = ", ".join(source.name for source in result.candidate_sources)
        print(f"等待授权使用外部能力，可选来源: {names}")
    else:
        print(f"流程状态: {result.status.value}")


if __name__ == "__main__":
    main()
