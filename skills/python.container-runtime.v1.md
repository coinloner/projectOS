# Python Container Runtime

适用于 FastAPI + PostgreSQL 项目的 Docker 运行配置。该 Skill 只提供实现约束，实际镜像准备、依赖解析和容器启动由 ProjectOS Environment/Runtime 控制面执行。

## 标准约定

- 后端依赖声明必须包含 `fastapi`、`uvicorn[standard]` 和实际使用的数据库驱动。
- FastAPI 入口固定为 `backend/app/main.py`，模块对象固定为 `app`。
- 容器内使用 `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000` 启动，不使用宿主机 Python 环境。
- 容器启动前执行 `alembic upgrade head`，或执行可重复的 `migrate.py` / `init_db.py`。
- `/health` 是进程存活探针；依赖数据库的 `/ready` 用于就绪探针。
- Dockerfile、Compose、端口和镜像由 Bootstrap/Runtime 节点生成或维护，CodeAgent 不得自行扩大写入范围。

## 交付前自检

- `requirements.in` 是否包含 Uvicorn？
- Compose 或受信 Runtime profile 是否明确调用 Uvicorn？
- `app.main:app` 是否能在 `backend` 工作目录下导入？
- 数据库迁移是否在服务启动前执行且可重复？
- 健康检查是否访问 `/health`，而不是依赖业务数据？
