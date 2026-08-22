"""ProjectOS 演示用外部文档 MCP 服务。

扮演"受控网络中的第三方文档服务"，让 capability 审批闭环可以在本地
端到端演示：Agent 请求 external_documentation 能力 → 项目所有者批准
docs-mcp 来源 → Agent 通过 MCP Streamable HTTP 调用这里的工具。

启动：
    .venv/bin/python -m app.demo.docs_mcp_server --port 8090
"""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP

_DOCS = {
    "idempotency": (
        "幂等性设计：写操作（POST/PUT/PATCH）应接受客户端生成的幂等键"
        "（Idempotency-Key 请求头）。服务端对同一键的重复请求返回首次结果，"
        "避免网络重试产生重复副作用；键应在服务端按资源与时间窗口去重。"
    ),
    "conflict": (
        "并发冲突响应：资源版本冲突使用 HTTP 409 Conflict，响应体包含"
        "冲突原因与当前资源版本（如 ETag）。客户端可据此重读资源后重试，"
        "或改用 412 Precondition Failed 表达条件请求不满足。"
    ),
    "rest": (
        "REST 资源设计：URL 使用名词复数（/tasks），动作通过 HTTP 方法表达；"
        "创建成功返回 201 与 Location 头；删除成功返回 204；列表支持分页参数"
        "limit/offset 并返回总数。错误响应使用统一的 problem+json 结构。"
    ),
}


def build_server(*, host: str = "127.0.0.1", port: int = 8090) -> FastMCP:
    server = FastMCP("projectos-docs", host=host, port=port)

    @server.tool()
    def search_docs(query: str) -> str:
        """按关键词搜索外部设计文档，返回最相关的文档片段。"""
        matched = [
            f"[{topic}] {content}"
            for topic, content in _DOCS.items()
            if topic in query.lower() or query.lower() in topic
        ]
        if not matched:
            return "（未找到与查询相关的文档；可用主题: idempotency、conflict、rest）"
        return "\n\n".join(matched)

    @server.tool()
    def get_doc(topic: str) -> str:
        """按主题名读取完整外部文档；主题名来自 search_docs 返回的 [topic]。"""
        content = _DOCS.get(topic.strip().lower())
        if content is None:
            available = ", ".join(sorted(_DOCS))
            return f"（未知主题 '{topic}'；可用: {available}）"
        return f"[{topic.strip().lower()}] {content}"

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="ProjectOS 演示外部文档 MCP 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    server = build_server(host=args.host, port=args.port)
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
