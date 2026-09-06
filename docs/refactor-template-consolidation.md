# 模板职责收束与入口统一 - 重构设计文档

**文档版本:** v1.0  
**创建日期:** 2026-09-07  
**状态:** 设计中

---

## 1. 重构动机

### 1.1 当前问题

**问题 A: 模板职责混乱**

当前模板同时承担了三件不应该混在一起的事:

1. **描述项目生命周期** (应该做):需求 → 架构 → 实现 → 测试 → 审查
2. **描述控制面权限** (应该做):哪个阶段用什么执行模式、可以读写哪些路径
3. **预先描述项目内部结构** (不应该做):`domain/api/runtime` 这种固定模块名

**结果:**
- 模板颗粒度过细,把"某项目有 3 个固定模块"硬编码在模板里
- 每个项目类型需要一个新模板 (`project_delivery_layered`, `project_delivery_frontend_backend`...)
- 模板数量膨胀,维护成本高

**问题 B: 入口过多,缺乏标准路径**

当前存在至少 7 个模板:
- `project_delivery_template`
- `project_delivery_dynamic_template`
- `project_delivery_layered_template`
- `project_delivery_minimal_template`
- `architecture_parallel_template`
- `architecture_layered_template`
- `architecture_compact_template`

**不清楚:**
- 哪个是"完整运行系统"的标准入口?
- 新项目应该选哪个?
- 各模板之间的差异是什么?
- 哪些是实验性质、哪些是生产就绪?

**问题 C: 设计意图偏离**

模板本该控制:
- 流程阶段顺序
- 阶段职责边界
- 输入输出类型
- 授权模式
- 质量门

但当前模板却在控制:
- 项目有几个模块
- 模块叫什么名字
- 有哪些具体文件
- 每个文件由谁写

**这导致:** Blueprint 和 Contract 的动态能力被架空,因为模板已经把结构写死了。

### 1.2 期望的目标状态

**目标 A: 模板只描述生命周期骨架**

顶层模板收束为 7-8 个阶段:

```
Requirement
  ↓
Architecture
  ↓
Contract
  ↓
Tasks
  ↓
Environment
  ↓
Implementation
  ↓
Test
  ↓
Review
```

**阶段内部动态展开:**
- Architecture 阶段:简单项目单节点,复杂项目 Blueprint→ModuleDesign→ImplementationDesign
- Contract 阶段:控制面确定性编译,不再让 LLM 手写大 JSON
- Code 阶段:由 Contract 的 `implementation_units` 动态生成文件级 WorkItem

**目标 B: 只保留 3 种交付模式**

1. **`delivery_default`** (新项目完整生命周期)
   ```
   Requirement → Architecture → Contract → Tasks → Environment → Code → Test → Review
   ```

2. **`delivery_incremental`** (已有项目增量开发)
   ```
   (跳过 Requirement/Architecture)
   已有基线 → Tasks → Environment → Code → Test → Review
   ```

3. **`architecture_only`** (仅架构设计)
   ```
   Requirement → Architecture → Architecture Quality Gate
   ```

**目标 C: 清晰的职责边界**

```
模板:      决定流程阶段和执行方式
Blueprint: 决定项目模块 (由 LLM 根据需求决定)
Contract:  决定实现文件和接口 (由 Blueprint 编译得到)
Runner:    决定实际展开和调度
Agent:     完成单个 WorkItem
```

一句话:**模板从"项目详细执行计划"收束为"生命周期骨架 + 阶段展开策略"。**

---

## 2. 改动范围

### 2.1 核心改动

| 组件 | 改动性质 | 具体内容 |
|------|----------|----------|
| `app/workflow/templates.py` | **新增 + 标记废弃** | 新增 `delivery_default_template()`,标记旧模板为 `@deprecated` |
| `app/planner/planner.py` | **修改选择逻辑** | 默认选 `delivery_default`,只在明确信号时选其他 |
| `app/orchestration/runner.py` | **增强动态展开** | 确保 Blueprint→Modules、Contract→Files 的展开逻辑健全 |
| `docs/templates.md` | **新建** | 说明 3 种模式、何时选哪个、如何显式指定 |
| `tests/test_workflow_compiler.py` | **新增测试** | 覆盖 `delivery_default` 的编译和动态展开 |

### 2.2 影响范围

**不影响的部分:**
- Agent 实现 (ArchitectureAgent, CodeAgent, TestAgent...)
- 产物格式 (Blueprint, Contract, ChangeSet...)
- API 接口 (`POST /runs`, `GET /runs/{id}`)
- 已有 Trace 的恢复 (旧模板仍可加载,只是标记为 deprecated)

**受影响的部分:**
- 新项目的模板选择逻辑
- 文档和示例 (需要更新为推荐 `delivery_default`)
- 未来的模板扩展 (应该走 `ExpansionPolicy`,而不是新建模板)

---

## 3. 分步实施计划

### 第 1 步:验证动态展开基础设施 (不改模板)

**目标:** 确认 `DynamicPlanBuilder` 和 Contract 编译器的动态展开能力已经 ready。

**任务清单:**
- [x] 写一个测试:`test_blueprint_to_dynamic_modules`
  - 给定一个 Blueprint (3 个模块)
  - 验证 `DynamicPlanBuilder.expand_modules` 生成 3 个 ModuleDesign WorkItem
- [x] 写一个测试:`test_contract_to_dynamic_files`
  - 给定一个 Contract (10 个 implementation_units)
  - 验证编译器生成 10 个文件级 Code WorkItem
- [x] 检查 `runner.py` 的 `_expand_architecture_modules_if_ready` 是否完整

**验收标准:**
- ✅ 两个测试通过
- ✅ 不改动任何现有模板
- ✅ 不影响现有 Trace 运行

**完成状态:** ✅ 已完成 (2026-09-07)

**预估工作量:** 2-3 小时

---

### 第 2 步:新增 `delivery_default` 模板

**目标:** 创建新的标准入口,只保留 7-8 个顶层阶段。

**任务清单:**
- [ ] 在 `app/workflow/templates.py` 新增 `delivery_default_template()`
  - 只定义 7 个阶段:Requirement, Architecture, Contract, Tasks, Environment, Implementation, Test, Review
  - Architecture 阶段:单个 Blueprint 锚点,不预设 `domain/api/runtime`
  - Contract 阶段:单个确定性编译锚点
  - Implementation 阶段:单个动态展开锚点 (不在模板里列文件)
- [ ] Tasks 阶段改成内部子流程
  - 在 Runner 里处理 `tasks-plan → tasks-integration → tasks-quality-gate` 的展开
  - 顶层模板只看到 `Tasks` 一个阶段
- [ ] 添加测试:`test_delivery_default_compiles`
  - 验证模板可以编译成 ExecutionPlan
  - 验证 Architecture 阶段没有预设模块名
- [ ] 添加集成测试:`test_delivery_default_todo_project`
  - 用最小 stub Agent 模拟一个 Todo 项目的完整流程
  - 验证模块名由 Blueprint 决定 (不是模板写死的)

**验收标准:**
- `delivery_default_template()` 定义完成
- 编译测试通过
- 集成测试证明动态展开生效

**预估工作量:** 4-6 小时

---

### 第 3 步:标记旧模板为 deprecated

**目标:** 让代码和文档明确"旧模板不再推荐"。

**任务清单:**
- [ ] 给以下模板添加 `@deprecated` 装饰器和 docstring 警告:
  - `project_delivery_template` → "已被 delivery_default 替代,仅供历史 Trace 兼容"
  - `project_delivery_dynamic_template` → "已被 delivery_default 替代"
  - `project_delivery_layered_template` → "实验性模板,不建议新项目使用"
  - `project_delivery_minimal_template` → "实验性模板"
- [ ] 保留 `architecture_*` 系列模板 (它们是显式架构设计路径,不是交付路径)
- [ ] 添加日志:当加载 deprecated 模板时,记录 warning 级别日志

**验收标准:**
- 旧模板仍可正常加载 (向后兼容)
- 日志里能看到 deprecation warning
- 新代码不再引用旧模板

**预估工作量:** 1-2 小时

---

### 第 4 步:新增 `delivery_incremental` 和 `architecture_only`

**目标:** 补齐另外两种标准模式。

**任务清单:**
- [ ] 新增 `delivery_incremental_template()`
  - 跳过 Requirement 和 Architecture
  - 从 Tasks 阶段开始
  - 假设已有 `project-contract.json`
- [ ] 新增 `architecture_only_template()`
  - 只包含 Requirement → Architecture → Quality Gate
  - 不生成 Contract、Code、Test
- [ ] 添加对应的编译和集成测试

**验收标准:**
- 两个新模板定义完成
- 测试覆盖

**预估工作量:** 3-4 小时

---

### 第 5 步:调整 Planner 的模板选择逻辑

**目标:** 让 Planner 默认选 `delivery_default`,只在明确信号时选其他。

**任务清单:**
- [ ] 修改 `app/planner/planner.py` 的模板选择逻辑:
  ```python
  def select_template(project_path: str, goal: str) -> str:
      # 检测到已有 project-contract.json → delivery_incremental
      if has_existing_contract(project_path):
          return "delivery_incremental"
      
      # 明确请求只要架构 → architecture_only
      if "只要架构" in goal or "architecture only" in goal.lower():
          return "architecture_only"
      
      # 默认 → delivery_default
      return "delivery_default"
  ```
- [ ] 添加测试:`test_planner_template_selection`
  - 验证各种输入下选择的模板正确

**验收标准:**
- Planner 测试通过
- 新项目默认走 `delivery_default`

**预估工作量:** 2-3 小时

---

### 第 6 步:更新文档和示例

**目标:** 让用户知道"3 种模式、何时选哪个"。

**任务清单:**
- [ ] 新建 `docs/templates.md`:
  - 说明 3 种交付模式的用途
  - 说明如何显式指定模板
  - 说明旧模板的状态 (deprecated, 仅历史兼容)
- [ ] 更新 `README.md`:
  - 示例改为使用 `delivery_default`
  - 移除对旧模板的引用
- [ ] 更新 `docs/architecture/workflow.md`:
  - 说明"模板只描述生命周期骨架,内部由动态展开决定"

**验收标准:**
- 文档完整且清晰
- 用户能快速找到"我应该用哪个模板"

**预估工作量:** 2-3 小时

---

### 第 7 步:删除冗余模板 (可选,后期做)

**目标:** 彻底清理旧代码。

**前置条件:**
- 第 1-6 步全部完成
- `delivery_default` 在真实项目中稳定运行至少 2 周
- 没有用户反馈"旧模板被误删"

**任务清单:**
- [ ] 删除以下模板定义:
  - `project_delivery_template`
  - `project_delivery_dynamic_template`
  - `project_delivery_layered_template`
  - `project_delivery_minimal_template`
- [ ] 删除对应的测试
- [ ] 更新 EVOLUTION.md,记录删除原因

**验收标准:**
- 代码库更简洁
- 测试覆盖没有下降 (因为新模板有自己的测试)

**预估工作量:** 2-3 小时

**建议:** 这一步非必需,可以等新模板稳定后再做。

---

## 4. 风险与缓解

### 4.1 风险

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| 动态展开逻辑不完整 | 新模板无法生成完整 DAG | 中 | 第 1 步专门验证基础设施 |
| 旧 Trace 无法恢复 | 已有项目中断 | 低 | 保留旧模板定义,只标记 deprecated |
| Planner 选错模板 | 用户体验差 | 中 | 第 5 步写完整的选择逻辑和测试 |
| 文档不清晰 | 用户不知道选哪个 | 高 | 第 6 步专门写用户文档 |

### 4.2 回退策略

**如果新模板有严重问题:**
- 保留旧模板定义 (不删除),用户可以显式指定旧模板
- Planner 可以临时改回默认选旧模板
- 旧 Trace 的恢复不受影响

**回退成本:** 低 (因为我们分步实施,每一步都有独立验收)

---

## 5. 验收标准

### 5.1 技术验收

- [ ] 所有新增测试通过 (预计新增 10-15 个测试)
- [ ] 现有测试不受影响 (395 passed 保持或增长)
- [ ] `delivery_default` 可以编译成合法的 ExecutionPlan
- [ ] `delivery_default` 可以用 stub Agent 跑通完整流程
- [ ] 动态展开逻辑在真实场景验证通过 (Blueprint→Modules, Contract→Files)

### 5.2 用户体验验收

- [ ] 用户执行 `python main.py --project /tmp/test --goal "..."` 时,默认走 `delivery_default`
- [ ] 用户可以在文档里找到"我应该用哪个模板"的清晰答案
- [ ] 旧 Trace 可以正常恢复 (兼容性)

### 5.3 代码质量验收

- [ ] `compileall` 通过
- [ ] `git diff --check` 通过
- [ ] 新代码有清晰的注释和 docstring
- [ ] `docs/EVOLUTION.md` 记录本次重构的动机和结果

---

## 6. 时间估算

| 步骤 | 预估工作量 | 累计工作量 |
|------|-----------|-----------|
| 第 1 步:验证基础设施 | 2-3 小时 | 2-3 小时 |
| 第 2 步:新增 delivery_default | 4-6 小时 | 6-9 小时 |
| 第 3 步:标记旧模板 deprecated | 1-2 小时 | 7-11 小时 |
| 第 4 步:新增另外两种模式 | 3-4 小时 | 10-15 小时 |
| 第 5 步:调整 Planner 逻辑 | 2-3 小时 | 12-18 小时 |
| 第 6 步:更新文档 | 2-3 小时 | 14-21 小时 |
| 第 7 步:删除旧模板 (可选) | 2-3 小时 | 16-24 小时 |

**总计:** 14-21 小时 (不含第 7 步)

**建议节奏:**
- 每天 2-3 小时,一周完成前 6 步
- 第 7 步可以等新模板稳定 2 周后再做

---

## 7. 后续演进方向

完成本次重构后,未来的扩展应该走以下路径:

### 7.1 不再新建项目类型模板

**错误做法:**
```python
def project_delivery_microservices_template():  # ❌ 不要这样做
    return WorkflowTemplate(
        stages=[...],
        work_items=[
            WorkItem(id="service-user", ...),
            WorkItem(id="service-order", ...),
            WorkItem(id="service-payment", ...),
        ]
    )
```

**正确做法:**

让 Blueprint 决定服务数量和名称,模板保持通用:

```python
# 模板仍然是 delivery_default
# Blueprint 里 LLM 会写:
modules = [
    {"module_id": "user_service", ...},
    {"module_id": "order_service", ...},
    {"module_id": "payment_service", ...},
]
# 控制面动态生成 3 个 ModuleDesign WorkItem
```

### 7.2 通过 ExpansionPolicy 扩展

如果某类项目需要特殊的展开策略 (例如微服务需要每个服务独立测试),应该:

1. 在 Blueprint 里标记项目类型:`project_type: "microservices"`
2. Runner 根据 `project_type` 选择 `ExpansionPolicy`
3. Policy 决定如何展开 Code、Test 等阶段

**不要:** 为每个项目类型新建一个顶层模板。

### 7.3 StageTemplate 抽象 (后续可选)

可以进一步抽象:

```python
@dataclass
class StageTemplate:
    name: str
    input_types: tuple[str, ...]
    output_types: tuple[str, ...]
    expansion_policy: str | None
    quality_gate: Callable | None

WorkflowTemplate(
    stages=[
        StageTemplate("architecture", expansion="blueprint_dynamic"),
        StageTemplate("contract", expansion="deterministic_compile"),
        StageTemplate("implementation", expansion="contract_files"),
    ]
)
```

但这是下一阶段的工作,本次重构不涉及。

---

## 8. 决策记录

| 决策 | 理由 | 日期 |
|------|------|------|
| 保留旧模板定义,只标记 deprecated | 保证向后兼容,降低回退成本 | 2026-09-07 |
| 默认模板改为 `delivery_default` | 统一入口,降低用户选择成本 | 2026-09-07 |
| Tasks 阶段内部子流程不拆到顶层 | 顶层只显示 7-8 个阶段,内部展开对用户透明 | 2026-09-07 |
| 第 7 步 (删除旧模板) 标记为可选 | 等新模板稳定后再删,降低风险 | 2026-09-07 |

---

## 9. 附录

### 9.1 当前模板列表

| 模板名 | 用途 | 节点数 | 状态 |
|--------|------|--------|------|
| `project_delivery` | 旧的通用交付 | ~12 | 待废弃 |
| `project_delivery_dynamic` | 动态交付 (未完全验证) | ~10 | 待废弃 |
| `project_delivery_layered` | 分层架构交付 | ~15 | 实验性,待废弃 |
| `project_delivery_minimal` | 最小交付 | ~8 | 实验性,待废弃 |
| `architecture_parallel` | 并行架构设计 | ~5 | 保留 (显式架构路径) |
| `architecture_layered` | 分层架构设计 | ~10 | 保留 (显式架构路径) |
| `architecture_compact` | 紧凑架构设计 | ~3 | 保留 (显式架构路径) |

### 9.2 参考资料

- EVOLUTION.md § 35: 恢复原始八阶段路线
- EVOLUTION.md § 29: 分层架构对象协议
- 与 GPT 的讨论记录 (本文档第 1 节引用)

---

**文档作者:** Claude Opus 5  
**审阅者:** (待填写)  
**批准者:** (待填写)  
**最后更新:** 2026-09-07
