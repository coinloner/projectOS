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
- 在 `workspace/` 下执行 `python -m venv .venv`
- 写入 `base_dir/name/project.yaml`
- 失败抛出 `RuntimeError`，`returncode` 见异常信息

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

- 递归删除整个项目目录（含 workspace 和虚拟环境）

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

1. **返回值结构不得改变** —— 公共方法的返回值类型一旦定义，后续版本只能扩展（加字段），不得修改或删除已有字段。
2. **异常类型不得降级** —— 当前声明为 `FileNotFoundError` 的错误，后续不得改为静默忽略或无提示返回。
3. **新增方法自由** —— 在保持已有方法不变的前提下，可以增加新的静态/实例方法。
4. **依赖 Runtime 层** —— 所有 `subprocess` 调用必须通过 `Runtime.run()`，方便集中管控。
