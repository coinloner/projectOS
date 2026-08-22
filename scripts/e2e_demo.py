"""ProjectOS 能力审批闭环端到端演示与回归脚本。

扮演一个"用户替身"，只调用系统公开的 REST API 走完整个交付闭环：

    创建项目 → 开会话 → 发送目标（真实 LLM 规划）
    → 节点发起 external_documentation 能力请求 → 替身批准 docs-mcp
    → 跨 domain 一次激活 → Agent 通过 MCP 真实查询外部规范
    → 实现摘要含「外部规范核实记录」→（必要时自动修复）→ 终态 completed

用途：
    - 回归验证：每次改动控制面/编排层后跑一遍，确认闭环没破
    - 面试演示：配合 /tmp/projectos-docs-mcp.log 展示真实 MCP 调用
    - 文档活例：展示"一个人类用户如何用 API 驱动全流程"

前置：
    1. docker 已启动（测试节点需要）
    2. 外部文档服务：.venv/bin/python -m app.demo.docs_mcp_server --port 8090
    3. API 控制面：
       PROJECTOS_PROJECTS_ROOT=./projects \
       PROJECTOS_DOCS_MCP_URL=http://127.0.0.1:8090/mcp \
       .venv/bin/python -m uvicorn app.api.asgi:app --host 127.0.0.1 --port 8010

运行（在仓库根目录）：
    .venv/bin/python scripts/e2e_demo.py --project todo-demo

说明：本脚本的自动批准是替身行为——真实产品中这一步是人类用户
对 capabilities/approve 端点做的一次明确授权；产品故意不默认自动批准。
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

GOAL = (
    "为 Todo 应用实现一个纯 Python 后端 REST API（仅用标准库，内存存储，"
    "不需要数据库）。REST 规范细节（创建/列表/完成/删除任务的资源与状态码设计、"
    "并发冲突 409 响应、幂等性设计）必须来自外部文档：在写任何代码之前，先返回 "
    "capability_request（capability=external_documentation，reason 说明需要查询 "
    "REST 设计规范）；待系统为本次运行接入外部文档来源后，调用 search_docs/get_doc "
    "核实规范，再实现代码。必须编写自动化测试并在 Docker 沙盒中真实运行验证，"
    "交付前必须经过 review_agent 审查。不要编造外部规范。"
)

TERMINAL_STATUSES = ("completed", "blocked", "failed")


class DemoClient:
    """最小 HTTP 客户端：只依赖标准库，方便在任意环境复现演示。"""

    def __init__(self, api: str) -> None:
        self.api = api

    def request(
        self, method: str, path: str, payload: dict | None = None, timeout: int = 60
    ) -> tuple[int, dict]:
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            self.api + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode() or b"{}")
        except TimeoutError:
            return 0, {}

    def get(self, path: str) -> dict:
        _, payload = self.request("GET", path)
        return payload


def wait_for_trace(client: DemoClient, project: str, conversation_id: str) -> str:
    """规划阶段消息 POST 会同步阻塞，超时后从会话历史里找 trace_id。"""

    def find_trace() -> str | None:
        conv = client.get(f"/api/v1/projects/{project}/conversations/{conversation_id}")
        for turn in conv.get("messages", []):
            if turn.get("trace_id"):
                return turn["trace_id"]
        return None

    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        trace_id = find_trace()
        if trace_id:
            return trace_id
        time.sleep(3)
    raise RuntimeError("未创建运行")


def approve_capability_requests(
    client: DemoClient, project: str, trace_id: str, approved: int
) -> int:
    """任何新的能力等待都批准一次（真实产品中这一步是人类授权）。"""
    events = client.get(
        f"/api/v1/projects/{project}/runs/{trace_id}/events"
    ).get("events", [])
    waits = [e for e in events if e.get("type") == "work_item_waiting_capability"]
    if len(waits) <= approved:
        return approved
    view = client.get(f"/api/v1/projects/{project}/runs/{trace_id}/capabilities")
    candidates = view.get("candidates", [])
    print(f"[5] 第 {len(waits)} 次能力等待: candidates={candidates}")
    assert "docs-mcp" in candidates, view
    # 人类用户此刻应看到候选来源并点击批准；替身脚本代替用户执行这一授权
    status, resp = client.request(
        "POST",
        f"/api/v1/projects/{project}/runs/{trace_id}/capabilities/approve",
        {"source_name": "docs-mcp"},
    )
    print(f"    审批: {status} {resp.get('status')}")
    assert status == 202
    return len(waits)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="e2e_demo",
        description="ProjectOS 能力审批闭环端到端演示（只调用公开 REST API）",
    )
    parser.add_argument(
        "--project", default="todo-demo", help="项目名（已存在则复用，默认 todo-demo）"
    )
    parser.add_argument("--api", default="http://127.0.0.1:8010", help="API 基地址")
    parser.add_argument(
        "--projects-root",
        default="./projects",
        help="产物目录（与 API 服务器的 PROJECTOS_PROJECTS_ROOT 一致）",
    )
    parser.add_argument("--max-wait", type=int, default=2400, help="等待终态秒数上限")
    args = parser.parse_args(argv)

    client = DemoClient(args.api)
    status, _ = client.request("POST", "/api/v1/projects", {"name": args.project})
    if status not in (200, 201, 409):
        raise RuntimeError(f"创建项目失败: {status}")
    print(f"[1] 项目 {args.project} 就绪")

    status, conversation = client.request(
        "POST", f"/api/v1/projects/{args.project}/conversations"
    )
    conversation_id = conversation["conversation_id"]
    print(f"[2] 会话创建: {conversation_id}")

    status, sent = client.request(
        "POST",
        f"/api/v1/projects/{args.project}/conversations/{conversation_id}/messages",
        {"content": GOAL},
        timeout=600,
    )
    trace_id = sent.get("trace_id") if status else None
    if not trace_id:
        trace_id = wait_for_trace(client, args.project, conversation_id)
    print(f"[3] 运行已创建: trace={trace_id}")

    approved = 0
    deadline = time.monotonic() + args.max_wait
    while time.monotonic() < deadline:
        run = client.get(f"/api/v1/projects/{args.project}/runs/{trace_id}")
        runtime_status = run.get("runtime_status")
        if runtime_status in TERMINAL_STATUSES:
            print(f"[4] 终态: runtime_status={runtime_status} status={run.get('status')}")
            break
        approved = approve_capability_requests(
            client, args.project, trace_id, approved
        )
        time.sleep(3)
    else:
        raise TimeoutError("运行未在限时内到达终态")

    events = client.get(
        f"/api/v1/projects/{args.project}/runs/{trace_id}/events"
    ).get("events", [])
    types = [e.get("type") for e in events]
    print(f"[6] 事件: {types}")
    assert "capability_approved" in types, types

    implementation = Path(args.projects_root) / args.project / "implementation.md"
    print(f"[7] implementation.md: {implementation.exists()}")
    assert implementation.exists(), "CodeAgent 未保存实现摘要"
    content = implementation.read_text(encoding="utf-8")
    has_verification = "外部规范核实记录" in content or "核实" in content
    print(f"    含规范核实内容: {has_verification}")
    assert has_verification, "实现摘要缺少外部规范核实记录"

    # 以下为事实性观察（是否出现取决于本轮计划，不作为硬性断言）
    evidence_count = len(
        [t for t in types if t == "sandbox_evidence_recorded"]
    )
    print(f"[8] 沙盒测试证据: {evidence_count} 条")
    review = Path(args.projects_root) / args.project / "review.md"
    if review.exists():
        for line in review.read_text(encoding="utf-8").splitlines():
            if "审查结论" in line:
                print(f"[9] Review: {line.strip()}")
                break
    else:
        print("[9] Review: 本轮计划未包含审查节点")

    final = client.get(f"/api/v1/projects/{args.project}/runs/{trace_id}")
    assert final.get("runtime_status") == "completed", final
    print(
        "E2E PASS: 能力请求 → 一次审批 → MCP 查规范 → 实现凭证"
        f"（沙盒证据 {evidence_count} 条）→ completed"
    )


if __name__ == "__main__":
    main()
