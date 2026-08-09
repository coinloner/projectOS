from app.project.project import Project
from app.tool_manager.manager import ToolManager
from app.tool_manager.source import (
    ExternalDynamicSource,
    ToolDef,
    ToolSetSource,
)
from app.requirement.requirement_tool import RequirementToolSet
from app.agent.requirement_agent import RequirementAgent


def main():
    project_path = "./projects/Mega_Shit"

    # 准备项目
    if not Project.exists("Mega_Shit", "./projects"):
        Project(name="Mega_Shit", base_dir="./projects").create()
        print()

    tools = RequirementToolSet(project_path)

    # ── 工具注册：以 ToolSet 为单位 ─────────────
    manager = ToolManager()

    manager.register_toolset(
        domain="requirement",
        name="base",
        source=ToolSetSource([
            (
                ToolDef(
                    name="save_requirement",
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
                ),
                tools.save,
            ),
            (
                ToolDef(
                    name="load_requirement",
                    description="读取已有的需求文档，用于了解当前状态或在修改前获取原文",
                    parameters={"type": "object", "properties": {}},
                ),
                tools.load,
            ),
        ]),
    )

    # External: 外部 MCP 动态来源，默认锁住，显式启用后才可激活
    manager.register_source(
        domain="requirement",
        name="mcp",
        source=ExternalDynamicSource(),
    )
    # manager.enable_external("requirement")
    # manager.activate_external("requirement")

    # Agent 不参与暴露半径决策 —— Manager 自己知道给多少
    agent = RequirementAgent(manager)
    result = agent.run("我要做一个检测粪便健康的网站，请帮我生成需求文档。")

    print("=" * 60)
    print(result)
    print("=" * 60)
    print(f"✅ 已保存到 {project_path}/requirement.md")


if __name__ == "__main__":
    main()
