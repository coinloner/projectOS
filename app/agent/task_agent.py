from app.agent.base_agent import BaseAgent
from app.agent.result import AgentResult
from app.execution_context import ExecutionContext, ExecutionMode
from app.tool_manager.gateway import ToolGateway


class TaskAgent(BaseAgent):
    """在受控产物工作流中生成可执行、可验证的任务清单。"""

    def __init__(self, gateway: ToolGateway) -> None:
        super().__init__(
            gateway=gateway,
            domain="task",
            role="技术项目经理",
            goal="将需求和架构拆解为有顺序、可验收的实施任务",
            backstory=_BACKSTORY,
            max_iterations=5,
        )

    def run(
        self, task: str, *, context: ExecutionContext | None = None
    ) -> AgentResult:
        """TaskAgent 只能由标准分区或集成 WorkItem 调度。"""
        if context is None or context.execution_mode not in {
            ExecutionMode.PARTITIONED,
            ExecutionMode.INTEGRATION,
        }:
            raise RuntimeError(
                "TaskAgent 必须在 PARTITIONED 或 INTEGRATION WorkItem 中执行"
            )
        return super().run(task, context=context)


_BACKSTORY = """\
你负责将需求和架构转化为工程执行清单。

工作流程：
1. 只通过 load_task_input 读取任务输入包中列出的 ref_id；不要猜测或拼接文件路径。
2. 按依赖关系拆分可交付任务，并为每项列出验收条件。
3. PARTITIONED 节点调用 write_staged_tasks；INTEGRATION 节点调用
   create_tasks_candidate。两者都不能直接发布 tasks.md。

执行边界：
- 只能完成当前 WorkItem 的 objective、constraints 和 acceptance_criteria。
- PARTITIONED 只交付当前分区的任务决策包；INTEGRATION 只整合已授权暂存引用。
- 不编写实现代码，不修改 workspace，不创建未声明的文件。

文档格式要求：
- ## 实施顺序
- ## 任务清单（编号、依赖、产出、验收条件）
- ## 风险与阻塞项

原则：任务应足够具体以支持后续实现；工具调用成功后只简短确认，不重复输出完整 Markdown。"""
