from app.requirement.requirement import Requirement, RequirementDocument


class RequirementToolSet:
    """Requirement 工具集 —— 将 Requirement 的能力封装为 Tool，供 Agent 调用。

    Agent 通过 ToolSet 访问 Tool，不直接依赖 Requirement 内部实现。
    """

    def __init__(self, project_path: str) -> None:
        self._project_path = project_path

    # ── 工具 ──────────────────────────────────

    def save(self, content: str) -> str:
        """保存需求文档到项目目录。

        Args:
            content: 需求文档的 Markdown 内容

        Returns:
            操作结果描述
        """
        doc = RequirementDocument(content=content)
        Requirement.save(self._project_path, doc)
        return "✅ 需求文档已保存"

    def load(self) -> str:
        """读取已有的需求文档。

        Returns:
            文档内容，如不存在则返回提示信息
        """
        try:
            doc = Requirement.load(self._project_path)
            return doc.content
        except FileNotFoundError:
            return "（尚未创建需求文档）"
