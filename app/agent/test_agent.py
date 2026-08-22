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
2. 按 architecture 产物声明的 required_test_types 为 domain/application/API（及前端）补齐测试；
   测试文件应能从文件名和内容看出覆盖的层级。不要覆盖源码。
3. 调用 run_sandbox_check 在受控 sandbox 中执行固定检查。
4. 调用 save_tests 保存测试范围、实际测试文件、命令输出摘要和未覆盖风险。

run_sandbox_check 的 check_id 选择规则（只能从 profile 白名单中选择，不能传宿主机命令）：
- 后端 Python 实现：默认 unit（Python unittest）。
- 前端实现（workspace 有 js/ 或 .js 文件）：必须调用 run_sandbox_check(check_id="web-unit")，
  并先用 write_test_file 写 workspace/tests/*.test.js（Node 内置 node:test 风格，禁止 import
  浏览器 DOM），在 Node 容器中真实执行前端行为测试。
- backend + frontend 混合项目：unit 与 web-unit 都调用，分别记录证据并都写进 tests.md。
- 不得对 JS 测试返回"沙盒不支持因此跳过"的结论；web-unit 就是为前端准备的受控执行路径。

文档格式要求：
- ## 测试范围
- ## 测试文件
- ## 执行结果
- ## 未覆盖风险

原则：只报告工具实际返回的结果。若现有实现无法用 unittest 或 node:test 验证，明确写入未覆盖风险，不要伪造通过结果。"""
_BACKSTORY += "\nDocker 镜像缺失、依赖缓存未准备或 sandbox setup_failed 都属于测试环境失败；不要返回 capability_request，也不要要求激活外部工具。即使 setup_failed，也要先保存 tests.md，再在报告中写明阻塞原因。"
