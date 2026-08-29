# Skill 目录

ProjectOS 的 Skill 不是 Agent 自己临时编造的提示词，而是版本化的实现方法参考。

## 位置

- 内置 Skill：仓库根目录 `skills/*.md`。
- 项目覆盖：生成项目的 `.projectos/skills/*.md`，同名文件优先于内置版本。
- 加载器：[app/skill/store.py](/Users/coinloner/projectOS/app/skill/store.py)。
- 配置模块：[app/skill/module.py](/Users/coinloner/projectOS/app/skill/module.py)。

## 当前内置 Skill

| Ref | 用途 |
|---|---|
| `python.domain-model.v1` | 领域模型和业务规则隔离 |
| `python.http-service.v1` | FastAPI/标准库 HTTP 服务分层 |
| `web.native-frontend.v1` | 无构建工具前端和交互状态 |
| `database.repository.v1` | 仓储、迁移和并发数据访问 |
| `testing.pytest.v1` | 单元、接口和集成测试组织 |
| `security.baseline.v1` | 凭证、输入、路径和依赖安全基线 |

Architecture 的 `implementation_units[].skill_refs` 指定当前 CodeAgent 可参考的 Skill。
Runner 会在执行前加载内容并放入 `TaskInputPackage`。Skill 只提供推荐做法，不能扩大
`allowed_paths`、绕过 Policy 或改变验收标准。

这些 Skill 的内容参考了常见工程实践，以及公开 curated 技能目录中的安全基线、威胁建模、
Playwright 和 CLI 工程方向；没有直接把面向 Codex 的外部系统提示词复制到生成项目中。

## Pro 用户配置

Skill 与 Policy 处于并列层级：Skill 提供实现方法，Policy 提供硬约束和质量判定。运行时容器
分别暴露 `container.skills` 和 `container.policies`，两者都不直接授予工具权限。Pro 用户
可以通过 API 为已注册 Agent 绑定 Skill：

- `GET /api/v1/projects/{project_id}/skills`
- `GET /api/v1/projects/{project_id}/agents/{agent_id}/skills`
- `PUT /api/v1/projects/{project_id}/agents/{agent_id}/skills`

绑定记录保存在 `.projectos/skills/assignments.json`。Architecture/WorkItem 显式声明的
`skill_refs` 与用户绑定会合并去重；用户配置不能改变路径授权、工具权限、Policy 或验收标准。
