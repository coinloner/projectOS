# Project 模块

## 概述

`app.project.project.Project` 是 ProjectOS 的项目管理层，负责项目的创建、加载、删除、存在性判断及列表扫描。新项目写入 `runtime.yaml` 声明默认 sandbox 运行时，不在宿主机创建项目虚拟环境。

## 依赖

| 模块 | 用途 |
|---|---|
| `shutil` | 递归删除项目目录 |
| `yaml` (PyYAML) | 项目配置文件读写 |
| `pathlib.Path` | 跨平台路径操作 |
| `app.runtime.manifest.RuntimeManifest` | 写入默认运行时声明 |

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
├── runtime.yaml          ← sandbox 运行时声明
└── project.yaml          ← 项目元信息
```

## 方法签名

### `__init__(name: str, base_dir: str, language: str = "Python", version: str = "0.1")`

构造项目对象，不执行任何 I/O。

### `create()`

1. 创建 `project_path` 和 `workspace_path`
2. 写入默认 `runtime.yaml`（`python-stdlib`）
3. 写入 `project.yaml`

运行时依赖、构建和测试由 Sandbox 管理，不回退到宿主机 `.venv`。

### `load(project_dir: str) -> Project`

从已有项目目录读取 `project.yaml` 并重建 `Project` 对象。

- 项目目录不存在 → `FileNotFoundError`

### `delete(name: str, base_dir: str)`

递归删除项目目录。项目不存在 → `FileNotFoundError`。

### `exists(name: str, base_dir: str) -> bool`

检查 `base_dir/name` 是否是一个存在的目录。

### `list(base_dir: str) -> list[str]`

扫描 `base_dir` 下所有包含 `project.yaml` 的子目录名，按字母序返回。
