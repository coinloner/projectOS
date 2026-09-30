# ProjectOS 完整执行计划：闭环测试与工程设计修改

> 版本：2026-09-26
> 目标：把 `Requirement → Architecture → Contract → Code → Integration → Runtime → API E2E → Browser E2E` 变成可重复、可恢复、可审计的完整交付闭环。
> Provider 固定：`wanfa / gpt-6-sol / responses`。

## 1. 先定义两个闭环，不混淆验收口径

### 1.1 语义最小闭环（L0）

```text
Requirement
  → Architecture Blueprint / ModuleDesign / ImplementationDesign
  → Architecture Integration / Quality Gate
  → Contract Compiler
  → project-contract.json + implementation-plan.json
```

L0 只回答一个问题：**需求是否已经被编译成一个没有歧义、可执行、可追踪的实现合同？**

L0 必须包含 Contract，因为 Architecture 的 Markdown 或自然语言描述不能直接作为 CodeAgent 的权限和输入。`Contract` 是前三个语义节点之间的机器边界，也是后续 Code、Test、Runtime 的单一事实来源。

### 1.2 可执行最小闭环（L1）

```text
Requirement
  → Architecture
  → Contract
  → 1 个 CodeAgent（1 个 owned_file）
  → ChangeSet
  → Wave 0 Integration
  → Runtime smoke (/health 或最小 API)
```

L1 用于回答：**L0 的合同能否真正驱动代码生成、Git 交付、确定性集成和运行时启动？**

因此不能把“前三个节点通过”直接称为需求到代码闭环；正确口径是：

- L0：语义闭环通过；
- L1：最小可执行垂直切片通过；
- L2：完整交付闭环通过。

后续 Tests、Review、API E2E、Browser E2E 不需要重新定义前三个节点，但必须消费 L0/L1 产物并作为 L2 的最终交付门。

## 2. 当前真实基线（2026-09-27）

### 2.1 已完成并有证据的工程修改

| 范围 | 已完成修改 | 直接证据 |
|---|---|---|
| Architecture | 已拆为 Blueprint → ModuleDesign → ImplementationDesign → Integration → Quality Gate → Contract，每层有独立 WorkItem、artifact、checkpoint 和 ownership | `app/agent/architecture_agent.py`、`app/planner/dynamic_builder.py`、Architecture 测试 |
| Provider | 已增加 Responses/streaming/tool schema/terminal event/API key preflight；403 policy/capability 不重试 | `app/llm/preflight.py`、`tests/test_provider_preflight.py` |
| Contract | 已有 canonical normalizer、严格 DTO、Project Contract 校验和 implementation plan 持久化 | `app/domain/architecture/*`、`app/workflow/compiler.py` |
| Code/Wave | CodeAgent 按 concrete `owned_file` 交付；Wave 是真实 baseline barrier；Wave 失败可局部 repair | `app/domain/code/*`、`app/application/runs.py` |
| Workspace | 已允许 `.jsx`，修复真实 L2 中 `frontend/src/App.jsx` 无法写入的问题 | `app/workspace/store.py`、`tests/test_workspace_toolset.py` |
| Resume | 已从所有 checkpoint 和事件日志合并成功 frontier，避免 stale forced-rerun 覆盖后续成功状态 | `app/application/runs.py`、`ResumeCheckpointMergeTest` |
| Repair Patch | repair scope 放宽到控制面上限 32；支持 verify/validate/check 操作归一化；接受 advisory `repair_paths`/`owner_files` 但不授予权限 | `app/planner/patch.py`、`tests/test_planner_patch.py` |

### 2.2 当前验证结果

- 全量测试：最近一次基线为 `766 passed, 138 warnings, 23 subtests passed`（`validation-evidence/pytest-after-http-contract-20260927.log`）。
- Repair patch 定向测试：`24 passed, 23 deselected`。
- `git diff --check`：通过。
- `python -m compileall -q app tests`：通过。
- 失败注入矩阵：`7/7 passed`。
- 已保留历史失败 Trace，不执行 `git reset --hard` 或 `git checkout .`。

### 2.3 当前尚未完成的事实

当前不能宣布完整 L2 已完成。Live L2 Trace：

```text
项目：projects/live-l2-20260926-rerun/wanfa-live-l2-todo-20260927
Trace：tr-2e4924e985cf
```

该 Trace 保留了此前的真实集成失败：

```text
backend/app/main.py
  引用 app.task_management.service.TaskManagementService
```

而服务实现实际为：

```text
backend/app/task_service.py
class TaskService
```

这证明核心问题是 **Implementation Contract 没有把“文件 → canonical Python module → provided symbols → consumer import”编译为强制合同**，不是 Git 合并本身的问题。

在最新恢复尝试中，Architecture、Contract 等已完成节点能够从 durable output 恢复；但 `wi-09-environment` 又遇到 Wanfa 临时 `502 upstream service temporarily unavailable` 并耗尽该节点重试额度。因此当前状态是：

```text
控制面和设计修复大部分通过；Live L2 仍被 Provider transport 失败和跨单元 import 合同缺口阻断。
```

## 3. 必须落地的设计修改

### 3.1 Provider 前置门和失败分类

**修改：**

1. Run 创建前执行 bounded Provider Preflight。
2. 记录 `provider_preflight_started/passed/blocked` 事件。
3. 把错误保留为 `failure_kind + retryable + scope + code + evidence_id`。
4. `403/upstream_policy_rejected`、model capability mismatch、缺 terminal event 的不可恢复情况不能被重新包装成普通 agent retry。
5. transport 502/timeout 只能重试当前 WorkItem，不得重跑已成功兄弟节点。

**改善原因：**

将外部服务问题前移，避免运行 2 小时后才发现模型或接口不支持；同时保证“Provider 故障”和“ProjectOS 设计故障”在报告中分开，便于制定策略。

### 3.2 Architecture checkpoint 分层

**修改：**

```text
Blueprint checkpoint
  → ModuleDesign checkpoint（按 module_id）
  → ImplementationDesign checkpoint（按 implementation_unit）
  → Architecture Integration checkpoint
  → Quality Gate checkpoint
  → Contract checkpoint
```

每个 checkpoint 必须带：

- `trace_id / work_item_id / parent_design_id`；
- artifact digest；
- dependency refs；
- ownership map；
- validation receipt；
- candidate/final 发布状态。

**改善原因：**

Architecture 失败时只重算受影响模块；上游完成产物可复用；不会把一个大节点的自然语言输出作为无法定位的黑盒。

### 3.3 Canonical Implementation Contract：增加跨单元 binding

当前 `owner_file/provided_symbols/required_symbols` 不足以阻止模型自由猜测模块路径。应在 Contract Compiler 中生成 canonical binding：

```json
{
  "interface_id": "task_management.task_service",
  "kind": "symbol",
  "owner_unit": "task-management-service",
  "owner_file": "backend/app/task_service.py",
  "binding": {
    "language": "python",
    "module": "app.task_service",
    "symbols": ["TaskService"]
  }
}
```

Consumer 侧必须收到：

```json
{
  "interface_id": "task_management.task_service",
  "consumption": {
    "mode": "import_code",
    "module": "app.task_service",
    "symbols": ["TaskService"]
  }
}
```

落地规则：

1. `backend/app/task_service.py` 的 canonical module 由路径确定性推导为 `app.task_service`；禁止 CodeAgent 自己从接口名称猜路径。
2. 每个 `import_code` consumer 必须显式列出 canonical module 和 required symbols。
3. CodeAgent prompt 直接给出 canonical import 示例，并明确禁止 dynamic import fallback、候选模块扫描和重命名服务类。
4. Contract Compiler 在生成 WorkItem 时将该 binding 放进 `delivery_contract`。
5. Integration 在合并前执行确定性 AST 检查：
   - import module 是否与 producer binding 相等或是受控的相对导入等价形式；
   - imported symbol 是否存在于 producer 文件 AST；
   - consumer 所声明的调用符号是否属于 producer binding。
6. 检查失败必须返回 `owner_unit_ids/owner_files`，repair 只重跑责任 CodeAgent 及必要验证节点。

**改善原因：**

把“人能看懂的服务依赖”变成“机器能验证的 import 合同”，在 Integration 前消灭本次真实发现的 `TaskManagementService`/`TaskService`、模块路径不一致问题。

### 3.4 HTTP 跨单元接口冻结和确定性消费检查

新增 `HttpOperationContract`，API 接口必须在合同中冻结 `method/path`（可选请求/响应 schema）。
Contract Compiler 将 consumer 的 `required_http_interfaces` 放入 WorkItem；CodeAgent 不再根据
interface id 猜 `/api` 前缀、统计别名或状态切换路径。Integration 在 Wave 发布前解析 FastAPI
路由和 Python/JS/JSX 的静态 HTTP 调用，发现 provider/consumer 漂移时返回具体 owner 文件。
历史合同无 operations 时保留兼容模式，但仍会用已发布 provider 路由检查实际 consumer。

直接证据：`app/domain/architecture/implementation_contract.py`、`app/domain/code/http_contract.py`、
`app/workflow/compiler.py`、`tests/test_http_contract_validation.py`、
`validation-evidence/live-l2-http-contract-gate-20260927.json`。

**改善原因：**

把“接口支持任务 CRUD”变成可比对的 HTTP 操作集合，在 Docker 测试之前阻止 `/tasks/statistics` vs
`/tasks/stats`、`/tasks/{id}/status` vs `/tasks/{id}/complete`、`/tasks` vs `/api/tasks` 这类跨单元漂移。

### 3.5 Repair scope 从“邻接图”改为“证据归因图”

当前失败节点的直接依赖和下游可能被全部放入 repair scope，最终 Integration Anchor 失败时范围偏大。新规则：

```text
Integration finding
  → parse owner_file / producer_unit / consumer_unit
  → rerun consumer CodeAgent（必要时 producer）
  → rerun affected test/review
  → re-integrate current wave
```

只有以下情况才允许扩大范围：

- Contract digest 改变；
- ownership 改变；
- producer binding 改变；
- Wave baseline 被污染；
- 失败证据明确指向多个 owner。

**改善原因：**

减少重跑节点、缩短恢复时间，避免一个单文件 import 错误把 11 个 WorkItem 都标记为 repair。

### 3.6 Environment 节点确定性优先

当前 Live L2 恢复在 `wi-09-environment` 遇到临时 502，说明环境节点仍承担了不必要的模型调用风险。应拆成：

```text
Environment Contract Compile（确定性）
  → filesystem/runtime prerequisite check（确定性）
  → optional LLM decision（只有存在未决技术选择时）
  → environment receipt
```

已知运行命令、端口、入口、依赖缓存、workspace 路径等不得交给模型猜测。模型仅用于处理合同中明确标为 `decision_required` 的选择。

**改善原因：**

把可重复的环境准备从 Provider 波动中隔离；即使模型短暂 502，也不应阻断已经可以确定执行的环境检查。

### 3.7 Resume 和 checkpoint 统一为单一 frontier

恢复规则固定为：

1. 读取 `checkpoint.json`、`delivery-checkpoint.json` 和 append-only `events.jsonl`。
2. 以事件中最后一次成功事件重建 WorkItem frontier。
3. checkpoint 只补充尚未完成节点的 forced-rerun 标记。
4. 已有后续成功事件的节点不得被 stale forced-rerun 覆盖。
5. 每次 resume 必须记录 `resume_frontier_rebuilt`，包含 completed/replayed/skipped IDs。

**改善原因：**

防止恢复时重复执行 Environment、CodeAgent 或 Architecture，避免重复副作用和无意义的成本消耗。

## 4. 闭环测试分层和执行顺序

### Phase 0：冻结基线和旧失败证据

**动作：**

```bash
cd /Users/coinloner/projectOS

git diff --check
.venv/bin/python -m compileall -q app tests
.venv/bin/python -m pytest -q
.venv/bin/python scripts/validate_failure_matrix.py > validation-evidence/failure-matrix-20260927.json
```

**门：** 全量测试通过、Failure Matrix `7/7`、旧 Trace 目录仍存在。

**禁止：** 删除旧 Trace、覆盖历史事件、`git reset --hard`、`git checkout .`。

### Phase 1：L0 Replay 语义闭环

运行确定性 Replay，检查：

1. Requirement artifact 存在且可加载；
2. Blueprint、每个 ModuleDesign、每个 ImplementationDesign 有 parent/dependency；
3. Architecture Integration 和 Quality Gate 成功；
4. Contract Compiler 成功；
5. `project-contract.json` 和 `implementation-plan.json` 可加载；
6. 无 unresolved refs、重复 ownership、循环依赖；
7. 事件包含 `semantic_loop_completed=true`。

通过条件：L0 可在不调用真实 Provider 的情况下重复通过。

### Phase 2：L1 Replay 最小可执行垂直切片

只选一个最小业务切片，例如：

```text
Requirement
  → task-management-service
  → backend/app/main.py
  → ChangeSet
  → Wave 0 merge
  → /health=200
```

必须保存：

- CodeAgent WorkItem ID；
- owned file；
- ChangeSet commit；
- Wave merge commit；
- baseline revision；
- runtime PID/日志；
- `/health` 响应。

通过条件：没有真实 ChangeSet、没有 Wave merge 或只跑静态测试都不能算 L1 通过。

### Phase 3：L1 失败注入

至少注入并验证：

| 故障 | 预期动作 | 不应发生 |
|---|---|---|
| Provider 403 policy | run block，等待 Provider 决策 | 消耗 WorkItem retry |
| SSE timeout | retry current WorkItem | 重跑兄弟节点 |
| terminal event missing | retry current WorkItem | 伪造完成事件 |
| architecture schema mismatch | control-plane block | CodeAgent 自行扩大 schema |
| CodeAgent 无 ChangeSet | retry owned file | 直接标记完成 |
| Wave merge conflict | freeze wave / block | 后续 Wave 消费坏 baseline |
| Runtime startup failure | runtime repair/block | 仅凭静态测试宣布完成 |

验证每个 case 的 `FailurePackage`、`recovery_entrypoint`、`repair_scope`、`completed_siblings_replayed=false`。

### Phase 4：Live L0

Provider preflight 通过后，使用 `wanfa/gpt-6-sol` 运行 Requirement → Architecture → Contract；不进入 Code，除非：

- Architecture Quality Gate 通过；
- Contract digest 已持久化；
- implementation plan 能被重新加载。

已有参考成功 Trace：

```text
projects/wanfa-architecture-protocol-20260926-184240-61159c
```

### Phase 5：Live L1

从 Live L0 的 Contract 产物进入一个最小 CodeAgent 单元；严格验证：

1. prompt 中出现 canonical binding；
2. CodeAgent 只写 owned file；
3. ChangeSet 只包含授权文件；
4. Wave 集成通过；
5. runtime smoke 通过。

如果失败，只恢复当前 Live Trace 的责任 WorkItem，不重跑 Live L0。

### Phase 6：Replay L2 完整交付

完整顺序：

```text
Tasks
  → Environment
  → Code Waves
  → Tests
  → Review
  → Runtime
  → API E2E
  → Browser E2E
```

L2 必须同时具备：

- 所有任务节点的完成事件；
- 所有 Code ChangeSet 和 Wave merge；
- TestAgent sandbox evidence；
- ReviewAgent 结论；
- runtime startup/health evidence；
- API E2E 的 create/list/filter/toggle/delete/stats；
- Browser E2E 的加载、创建、完成/取消完成、删除、统计和错误反馈；
- Trace 终态 `completed`。

### Phase 7：Live L2 恢复和最终运行

当前 Trace 不创建新 Trace，先按以下顺序恢复：

1. 先执行 Provider preflight，确认 502 已恢复；
2. 只 resume `tr-2e4924e985cf`；
3. 观察是否从 `wi-09-environment` 继续，而不是重跑已完成 Architecture/Contract；
4. Contract binding 修复后，若出现 import mismatch，只重跑 `wi-code-task-management-http`；
5. 重新集成当前 Wave；
6. 继续 Tests → Review → Runtime → API E2E → Browser E2E；
7. 生成最终 evidence index，不覆盖历史失败事件。

建议观察命令：

```bash
PROJECT=projects/live-l2-20260926-rerun/wanfa-live-l2-todo-20260927
TRACE="$PROJECT/.projectos/runs/tr-2e4924e985cf"

jq '{status,error,finished_at}' "$TRACE/trace.json"
tail -50 "$TRACE/events.jsonl" | jq -c '{created_at,type,work_item_id,details}'
cat "$TRACE/integration-review.json"
```

## 5. 每一层的明确完成定义

### L0 完成

- Requirement 可追溯到 Architecture design；
- Architecture 分层 checkpoint 全部存在；
- Quality Gate 通过；
- Contract digest 和 compiled plan digest 存在；
- ownership、dependency、binding 均可确定性验证；
- `semantic_loop_completed=true`。

### L1 完成

- L0 全部通过；
- 至少一个真实 CodeAgent WorkItem；
- 至少一个真实 ChangeSet；
- Wave merge commit 和新 baseline 存在；
- runtime 启动成功；
- `/health` 或最小业务 API 返回预期结果。

### L2 完成

- L1 全部通过；
- 所有 Code Waves 集成；
- TestAgent sandbox evidence 存在；
- ReviewAgent 不是 BLOCKED；
- Runtime smoke 通过；
- API E2E 和 Browser E2E 通过；
- Trace 终态为 `completed`；
- 失败矩阵和历史失败 Trace 均保留。

## 6. 最终交付物

```text
validation-evidence/
  baseline-20260927.json
  failure-matrix-20260927.json
  replay-l0-20260927.json
  replay-l1-20260927.json
  replay-l2-20260927.json
  live-l0-20260927.json
  live-l1-20260927.json
  live-l2-20260927.json
  evidence-index-20260927.json

docs/
  execution-plan-2026-09-26.md
  final-delivery-report-2026-09-26.md
```

最终报告必须把每个结论绑定到：`trace_id`、`work_item_id`、artifact 路径、ChangeSet commit、Wave merge commit、测试命令和 runtime/API/Browser 原始证据。没有直接证据的项只能标记为 `未验证`，不能写成“已完成”。

## 7. 交付顺序的策略判断

1. **先 L0，再 L1，再 L2**：先证明合同可编译，再证明合同可执行，最后证明完整交付可运行。
2. **先 Replay，再 Live**：Replay 用于验证 ProjectOS 自身，Live 用于验证 Provider + ProjectOS 联合行为。
3. **先确定性门，再 LLM 节点**：能由代码验证的路径、符号、ownership、runtime 检查不得交给模型猜。
4. **失败按证据局部恢复**：Provider 失败恢复 WorkItem；Contract 失败恢复 control plane；Integration 失败恢复 owner CodeAgent；Runtime 失败恢复 runtime 责任节点。
5. **旧失败是证据，不是垃圾**：历史 Trace 用来证明卡点曾经真实发生，成功 Trace 必须并列展示差异。
