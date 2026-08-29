# Database Repository

适用于数据库模型、迁移和仓储实现。

## 做法

- 数据库会话和连接池只存在于 infrastructure 层。
- 仓储接口由 domain/application 定义，具体 ORM 查询由 infrastructure 实现。
- 初始化和迁移必须幂等；不要在 import 时执行 DDL 或写数据。
- 并发更新使用明确的事务边界、唯一约束或版本条件，不能只依赖内存锁。

## 自检

- 空库能否通过标准初始化命令建立？
- 重复执行迁移是否安全？
- 并发冲突是否有测试证据和明确错误语义？
