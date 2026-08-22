# Project API 接口文档

> **定位**：定义 `Project` 模块对外的公共契约。所有实现变更不得破坏此文档中声明的签名、返回值结构和异常语义。

## 1. 构造项目

```python
Project(name: str, base_dir: str, language: str = "Python", version: str = "0.1") -> Project
```

| 参数 | 类型 | 默认值 | 必填 |
|---|---|---|---|
| `name` | `str` | — | 是 |
| `base_dir` | `str` | — | 是 |
| `language` | `str` | `"Python"` | 否 |
| `version` | `str` | `"0.1"` | 否 |

- 不产生 I/O，仅初始化内存对象。

---

## 2. 创建项目

```python
Project.create() -> None
```

- 创建 `base_dir/name/`、`base_dir/name/workspace/`
- 写入 `runtime.yaml`，默认 Profile 为 `python-stdlib`
- 写入 `base_dir/name/project.yaml`
- 不在宿主机创建项目虚拟环境；依赖与测试由 Sandbox 管理

---

## 3. 加载项目

```python
Project.load(project_dir: str) -> Project
```

| 异常 | 触发条件 |
|---|---|
| `FileNotFoundError` | `project_dir/project.yaml` 不存在 |

- 读取 YAML 中的 `name`、`language`、`version`
- `base_dir` 自动推导为 `project_dir` 的父目录

---

## 4. 删除项目

```python
Project.delete(name: str, base_dir: str) -> None
```

| 异常 | 触发条件 |
|---|---|
| `FileNotFoundError` | `base_dir/name` 不存在 |

- 递归删除整个项目目录（含 workspace 和 sandbox 缓存）

---

## 5. 判断存在

```python
Project.exists(name: str, base_dir: str) -> bool
```

- 不抛异常

---

## 6. 列出项目

```python
Project.list(base_dir: str) -> list[str]
```

- `base_dir` 不存在 → 返回空列表 `[]`
- 只返回包含 `project.yaml` 的子目录名
- 结果按字母序排列

---

## 兼容性约定

## HTTP 创建路径

`POST /api/v1/projects` 接受：

```json
{"name": "todo_demo"}
```

不传 `path` 时创建在配置的 `projects_root/<name>`。也可以指定本地绝对路径：

```json
{"name": "todo_local", "path": "/tmp/projectos/todo_local"}
```

路径映射保存在 `projects_root/.projectos/project-paths.json`，后续 API 仍通过
`project_id` 定位自定义目录；项目根目录和其父目录不能作为项目路径。

已有项目目录使用 `POST /api/v1/projects/import` 登记，不会改写其中的文件：

```json
{"name": "existing_demo", "path": "/tmp/projectos/existing_demo"}
```

导入目录必须包含 `project.yaml`，且同一 `project_id` 不能映射到另一目录。导入成功后，
环境审批、运行、恢复和对话接口与新建项目完全一致。

1. **返回值结构不得改变** —— 公共方法的返回值类型一旦定义，后续版本只能扩展（加字段），不得修改或删除已有字段。
2. **异常类型不得降级** —— 当前声明为 `FileNotFoundError` 的错误，后续不得改为静默忽略或无提示返回。
3. **新增方法自由** —— 在保持已有方法不变的前提下，可以增加新的静态/实例方法。
4. **运行时隔离** —— 生成项目的构建、运行和测试必须经由 SandboxController，不得调用宿主机 `Runtime.run()`。
