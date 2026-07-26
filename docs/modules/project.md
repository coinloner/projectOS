# Project 模块

## 概述

`app.project.project.Project` 是 ProjectOS 的项目管理层，负责项目的创建、加载、删除、存在性判断及列表扫描。所有目录和文件操作均通过 `pathlib.Path` 完成，命令执行统一经由 `Runtime` 层。

## 依赖

| 模块 | 用途 |
|---|---|
| `shutil` | 递归删除项目目录 |
| `sys` | 获取当前 Python 解释器路径 |
| `yaml` (PyYAML) | 项目配置文件读写 |
| `pathlib.Path` | 跨平台路径操作 |
| `app.runtime.Runtime` | 命令执行（创建虚拟环境） |

## 类设计

```
Project
├── __init__(name, base_dir, language?, version?)   # 构造项目对象
├── create()                                         # 实例方法：创建项目
├── load(project_dir)                                # 静态工厂：从 YAML 加载
├── delete(name, base_dir)                           # 静态方法：删除项目
├── exists(name, base_dir) -> bool                   # 静态方法：判断存在
└── list(base_dir) -> list[str]                      # 静态方法：列出所有项目
```

## 实例属性

| 属性 | 类型 | 说明 |
|---|---|---|
| `name` | `str` | 项目名称 |
| `project_path` | `Path` | `base_dir / name`，项目根目录 |
| `workspace_path` | `Path` | `project_path / "workspace"`，工作空间目录 |
| `language` | `str` | 语言，默认 `"Python"` |
| `version` | `str` | 版本号，默认 `"0.1"` |
| `state` | `str` | 状态，初始化时为 `"created"` |

## 目录结构（单项目）

```
{base_dir}/{name}/
├── workspace/
│   └── .venv/            ← 项目专属虚拟环境
└── project.yaml          ← 项目元信息
```

## 方法签名

### `__init__(name: str, base_dir: str, language: str = "Python", version: str = "0.1")`

构造项目对象，不执行任何 I/O。

### `create()`

1. 创建 `project_path` 和 `workspace_path`
2. 在 `workspace_path` 下执行 `python -m venv .venv`
3. 写入 `project.yaml`

失败时抛出 `RuntimeError`。

### `load(project_dir: str) -> Project`

从已有项目目录读取 `project.yaml` 并重建 `Project` 对象。

- 项目目录不存在 → `FileNotFoundError`

### `delete(name: str, base_dir: str)`

递归删除项目目录。项目不存在 → `FileNotFoundError`。

### `exists(name: str, base_dir: str) -> bool`

检查 `base_dir/name` 是否是一个存在的目录。

### `list(base_dir: str) -> list[str]`

扫描 `base_dir` 下所有包含 `project.yaml` 的子目录名，按字母序返回。
