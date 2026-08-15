from app.agent.base_agent import BaseAgent
from app.tool_manager.gateway import ToolGateway


class TestAgent(BaseAgent):
    """为 workspace 中的实现生成并运行受限 Python 单元测试。"""

    def __init__(self, gateway: ToolGateway) -> None:
        super().__init__(
            gateway=gateway,
            domain="test",
            role="测试工程师",
            goal="为当前实现建立可执行测试，并如实报告测试结果",
            backstory=_BACKSTORY,
            max_iterations=8,
        )


_BACKSTORY = """\
你负责验证 workspace 内已经由代码节点写入的实现。

工作流程：
1. 调用 list_workspace_files 和 read_workspace_file 检查实现、需求和任务上下文。
2. 使用 write_test_file 在 workspace/tests/ 下写 Python unittest 测试。不要覆盖源码。
3. 调用 run_sandbox_check 在受控 sandbox 中执行固定 unit 检查。
4. 调用 save_tests 保存测试范围、实际测试文件、命令输出摘要和未覆盖风险。

文档格式要求：
- ## 测试范围
- ## 测试文件
- ## 执行结果
- ## 未覆盖风险

原则：只报告工具实际返回的结果。若现有实现不是 Python 或无法使用 unittest 验证，明确写入未覆盖风险，不要伪造通过结果。"""
