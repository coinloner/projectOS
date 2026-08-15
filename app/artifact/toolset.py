"""供领域 Agent 使用的受限项目产物工具。"""

from __future__ import annotations

from app.artifact.store import ArtifactStore


class ArtifactToolSet:
    """一个节点的最小文件能力：写自己的产物，读已声明的前置产物。"""

    def __init__(
        self,
        project_path: str,
        *,
        output_artifact: str,
        readable_artifacts: tuple[str, ...] = (),
        read_char_limit: int | None = None,
    ) -> None:
        ArtifactStore.filename_for(output_artifact)
        for artifact in readable_artifacts:
            ArtifactStore.filename_for(artifact)
        if read_char_limit is not None and read_char_limit < 200:
            raise ValueError("read_char_limit 至少为 200")

        self._store = ArtifactStore(project_path)
        self._output_artifact = output_artifact
        self._readable_artifacts = frozenset(readable_artifacts)
        self._read_char_limit = read_char_limit

    def save(self, content: str) -> str:
        """写入该 Agent 的固定目标产物。"""
        self._store.save(self._output_artifact, content)
        return f"已保存 {ArtifactStore.filename_for(self._output_artifact)}"

    def load(self, artifact: str) -> str:
        """读取该 Agent 明确获准读取的前置产物。"""
        if artifact not in self._readable_artifacts:
            allowed = ", ".join(sorted(self._readable_artifacts)) or "无"
            raise PermissionError(
                f"当前 Agent 无权读取产物 '{artifact}'，允许读取: {allowed}"
            )
        content = self._store.load(artifact)
        if self._read_char_limit is None or len(content) <= self._read_char_limit:
            return content
        return _excerpt(content, self._read_char_limit)


def _excerpt(content: str, limit: int) -> str:
    """保留文档开头和结尾，供只需审查证据的节点节省上下文。"""
    head_length = limit // 2
    tail_length = limit - head_length
    omitted = len(content) - limit
    return (
        content[:head_length]
        + f"\n\n（中间已省略 {omitted} 个字符；此节点仅获授权读取摘要）\n\n"
        + content[-tail_length:]
    )
