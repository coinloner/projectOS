from app.project.project import Project
from app.tool_registry.registry import ToolRegistry
from app.requirement.requirement_tool import RequirementToolSet
from app.agent.requirement_agent import RequirementAgent


def main():
    project_path = "./projects/Mega_Shit"

    # 准备项目
    if not Project.exists("Mega_Shit", "./projects"):
        Project(name="Mega_Shit", base_dir="./projects").create()
        print()

    # 方案 A：启动时集中注册工具
    tools = RequirementToolSet(project_path)
    registry = ToolRegistry()
    registry.register(
        name="save_requirement",
        fn=tools.save,
        description="保存需求文档到项目目录。调用前确保内容已经用户确认。",
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "需求文档的完整 Markdown 内容",
                },
            },
            "required": ["content"],
        },
    )
    registry.register(
        name="load_requirement",
        fn=tools.load,
        description="读取已有的需求文档，用于了解当前状态或在修改前获取原文",
        parameters={"type": "object", "properties": {}},
    )

    # Agent 接收已注册好的 registry
    agent = RequirementAgent(registry)
    result = agent.run("我要做一个检测粪便健康的网站，请帮我生成需求文档。")

    print("=" * 60)
    print(result)
    print("=" * 60)
    print(f"✅ 已保存到 {project_path}/requirement.md")


if __name__ == "__main__":
    main()
