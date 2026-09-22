# 项目交付模板指南

本文档说明 ProjectOS 的项目交付模板体系,包括 3 种标准模式、如何选择、以及如何显式指定。

---

## 概述

ProjectOS 提供 3 种标准交付模式,涵盖从新项目到增量开发、从完整交付到架构探索的各种场景:

| 模板 ID | 用途 | 生命周期阶段 |
|---------|------|-------------|
| `delivery_default` | 新项目完整交付 | Requirement → Architecture → Contract → Tasks → Environment → Implementation → Test → Review |
| `delivery_incremental` | 已有基线的增量开发 | Tasks → Environment → Implementation → Test → Review |
| `architecture_only` | 仅架构设计和探索 | Requirement → Architecture → Quality Gate |

---

## 1. delivery_default - 通用项目交付

**适用场景:**
- 全新项目,从需求到代码的完整交付
- 需要系统性设计架构
- 需要自动生成测试和审查报告

**生命周期:**
```
Requirement (需求澄清)
    ↓
Architecture (架构设计 - 动态展开)
    ↓
Contract (架构合同编译)
    ↓
Tasks (任务规划)
    ↓
Environment (环境准备)
    ↓
Implementation (代码实现 - 动态展开)
    ↓
Test (测试验证)
    ↓
Review (交付审查)
```

**核心特性:**
- **动态模块展开**: Architecture 阶段不预设 `domain/api/runtime` 等固定模块,而是根据 Blueprint 动态生成
- **动态文件展开**: Implementation 阶段根据 Contract 的 `implementation_units` 动态生成文件级 WorkItem
- **模块数量和名称由 LLM 决定**: 一个 Todo 应用可能生成 3 个模块,一个订单系统可能生成 8 个模块

**示例:**
```bash
# 自动选择 delivery_default
python main.py --project /tmp/my-project --goal "实现一个命令行 Todo 应用"
```

---

## 2. delivery_incremental - 增量项目交付

**适用场景:**
- 项目已有架构基线 (`project-contract.json` 存在)
- 只需要增量开发新功能或修复 bug
- 架构不需要重新设计

**生命周期:**
```
Tasks (任务规划)
    ↓
Environment (环境准备)
    ↓
Implementation (代码实现)
    ↓
Test (测试验证)
    ↓
Review (交付审查)
```

**核心特性:**
- **跳过需求和架构阶段**: 假设 `requirement.md` 和 `project-contract.json` 已存在
- **基于已有 Contract**: 从已有的架构合同读取实现单元和文件所有权
- **更快的迭代**: 只关注本次迭代的任务和实现

**自动触发条件:**
- 检测到 `.projectos/architecture/project-contract.json` 存在

**示例:**
```bash
# 如果 project-contract.json 存在,自动选择 delivery_incremental
python main.py --project /tmp/existing-project --goal "添加导出功能"
```

---

## 3. architecture_only - 仅架构设计

**适用场景:**
- 只需要架构设计,不需要代码实现
- 需要先评审架构,再决定是否实施
- 架构探索和原型设计

**生命周期:**
```
Requirement (需求澄清)
    ↓
Architecture (架构设计)
    ↓
Quality Gate (架构质量门)
```

**核心特性:**
- **不生成代码**: 只输出架构文档,不创建实现文件
- **不生成测试**: 没有测试验证阶段
- **快速迭代**: 适合快速探索多个架构方案

**自动触发条件:**
- 用户明确表示"只要架构设计"、"不需要代码"等

**示例:**
```bash
# 自动选择 architecture_only
python main.py --project /tmp/design-only --goal "只要架构设计,不需要实现"
```

---

## 如何选择模板

### 自动选择 (推荐)

ProjectOS 会根据以下信号自动选择合适的模板:

1. **检测到 `project-contract.json` 存在** → `delivery_incremental`
2. **用户明确表示"只要架构"** → `architecture_only`
3. **默认** → `delivery_default`

关键词识别 (用于 `architecture_only`):
- "只要架构"、"仅架构"、"只需要架构"
- "architecture only"、"design only"
- "只做架构设计"、"不要实现"、"不需要代码"

### 显式指定 (API/CLI)

如果需要覆盖自动选择逻辑,可以显式指定模板:

```python
# Python API
from app.application.runs import RunService

run_service = RunService(coordinator=coordinator)
run = run_service.start_controlled_workflow(
    project_path="/tmp/my-project",
    goal="实现一个 Todo 应用",
    workflow_id="delivery_default",  # 显式指定
)
```

```bash
# CLI (未来支持)
python main.py --project /tmp/my-project --goal "..." --template delivery_default
```

---

## 模板对比

| 特性 | delivery_default | delivery_incremental | architecture_only |
|------|-----------------|---------------------|-------------------|
| 需求澄清 | ✅ | ❌ (假设已有) | ✅ |
| 架构设计 | ✅ | ❌ (假设已有) | ✅ |
| 架构合同 | ✅ | ❌ (使用已有) | ❌ |
| 任务规划 | ✅ | ✅ | ❌ |
| 代码实现 | ✅ | ✅ | ❌ |
| 测试验证 | ✅ | ✅ | ❌ |
| 交付审查 | ✅ | ✅ | ❌ |
| 预设模块结构 | ❌ (动态) | ❌ (基于 Contract) | ❌ (动态) |
| 适合场景 | 新项目 | 增量开发 | 架构探索 |

---

## 旧模板 (已废弃)

以下模板已废弃,仅供历史 Trace 恢复使用,**不建议新项目使用**:

| 模板 ID | 废弃原因 | 替代方案 |
|---------|----------|----------|
| `project_delivery` | 预设固定模块结构,不灵活 | `delivery_default` |
| `project_delivery_dynamic` | 功能已整合到 `delivery_default` | `delivery_default` |
| `project_delivery_layered` | 实验性模板,预设分层结构 | `delivery_default` |
| `project_delivery_minimal` | 实验性模板,功能重叠 | `delivery_default` 或 `delivery_incremental` |

**废弃日期:** 2026-09-07

**向后兼容性:** 旧 Trace 可以正常恢复,但调用旧模板时会发出 `DeprecationWarning`。

---

## 常见问题

### Q: 如何知道当前使用的是哪个模板?

A: 查看 Run 的元数据或日志,会显示 `template_id`。

### Q: 可以在运行中切换模板吗?

A: 不可以。模板在 Run 启动时确定,运行过程中不可更改。

### Q: 如何为特定项目类型创建自定义模板?

A: 不建议创建项目类型模板 (例如"微服务模板"、"前后端分离模板")。正确做法是:
- 让 Blueprint 决定模块数量和名称
- 通过 `ExpansionPolicy` 自定义展开策略
- 保持模板通用,避免模板数量膨胀

### Q: 为什么不再有 `domain/api/runtime` 固定模块?

A: 因为不是所有项目都适合这个结构。例如:
- 一个命令行工具可能只需要 `cli`、`core`、`storage`
- 一个微服务可能需要 `user_service`、`order_service`、`payment_service`
- 一个前端应用可能需要 `components`、`pages`、`state`

新模板让 LLM 根据需求和架构原则决定模块划分,而不是强制套用固定结构。

---

## 参考资料

- [重构设计文档](refactor-template-consolidation.md) - 详细说明为什么要做这次重构
- [EVOLUTION.md](../docs/EVOLUTION.md) - 记录模板演进历史
- [工作流编译器](../app/workflow/compiler.py) - 模板编译实现
- [模板选择器](../app/planner/template_selector.py) - 自动选择逻辑

---

**最后更新:** 2026-09-07  
**文档版本:** 1.0
