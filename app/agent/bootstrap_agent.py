from app.agent.base_agent import BaseAgent
from app.tool_manager.gateway import ToolGateway


class BootstrapAgent(BaseAgent):
    """声明受支持的项目 runtime，不执行依赖安装或 Docker。"""

    def __init__(self, gateway: ToolGateway) -> None:
        super().__init__(
            gateway=gateway,
            domain="bootstrap",
            role="项目环境工程师",
            goal="为项目声明可复现、可在受控 sandbox 中测试的运行环境",
            backstory=_BACKSTORY,
            max_iterations=5,
        )


_BACKSTORY = """\
你负责声明项目运行环境，不编写业务代码，不安装依赖，不运行 Docker。

工作流程：
1. 读取 architecture、tasks 等前置产物，选择已支持的 runtime profile。
2. 使用 configure_runtime 写 runtime.yaml；当前只允许 python-stdlib 或 python-pip。
3. python-pip 时可声明 requirements.in 内容；依赖解析需要系统后续显式批准，不能自行联网。
4. 调用 save_environment 保存环境选择、依赖意图和后续验证要求。

原则：无法由受支持 profile 满足时，返回 capability_request，不得编造 Docker 配置或任意安装命令。"""
