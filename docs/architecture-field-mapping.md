# 架构设计数据模型字段映射分析

## 数据流向

```
Architecture Agent (depth 0-2)
  ↓
ArchitectureDesignBundle (集成验证)
  ↓
ProjectContract (持久化)
  ↓
Code Agent (实现)
```

## 核心数据模型对比

### 1. 接口相关模型

#### InterfaceRef (ModuleDesign 和 ImplementationDesign 中使用)
```python
class InterfaceRef:
    interface_id: str
    direction: Literal["provided", "consumed"]
    summary: str
    consumption_type: Literal["import_code", "http_call", "process_spawn", "shared_schema"] | None  # ✅ 新增
    confidence: Literal["high", "medium", "low"] | None  # ✅ 新增
    to_be_verified: bool = False  # ✅ 新增
```

#### ContractInterfaceInput (ProjectContract 中的 provided 接口)
```python
class ContractInterfaceInput:
    interface_id: str
    kind: str  # ⚠️ 不同：这里是 kind，InterfaceRef 没有
    name: str  # ⚠️ 不同：InterfaceRef 没有
    owner_unit: str  # ⚠️ 不同：InterfaceRef 没有
    owner_file: str | None
    signature: str | None
    input_schema: str | None
    output_schema: str | None
    errors: list[str]
    constraints: list[str]
    consumption_type: Literal["import_code", "http_call", "process_spawn", "shared_schema"] | None  # ✅ 新增
    confidence: Literal["high", "medium", "low"] | None  # ✅ 新增
    to_be_verified: bool = False  # ✅ 新增
```

#### ConsumedInterfaceRefInput (ProjectContract 中的 consumed 接口)
```python
class ConsumedInterfaceRefInput:
    interface_id: str
    usage: str | None  # ⚠️ 对应 InterfaceRef.summary
    required: bool
    # ❌ 缺少: consumption_type, confidence, to_be_verified
```

### 2. 模块相关模型

#### ModuleRef (Blueprint 中的模块引用)
```python
class ModuleRef:
    module_id: str
    responsibility: str
    purpose: str | None
    depends_on_modules: list[str]
    requirement_ids: list[str]
    # ❌ 缺少: tech_stack (但我们的验证逻辑依赖了它)
    # ❌ 缺少: runtime (但我们的验证逻辑依赖了它)
```

#### ModuleDesign (depth=1)
```python
class ModuleDesign:
    schema_version: Literal[1]
    design_id: str
    depth: Literal[1]
    parent_design_id: str
    module_id: str
    purpose: str | None
    responsibilities: list[str]
    provided_interfaces: list[InterfaceRef]  # ✅ 使用 InterfaceRef
    consumed_interfaces: list[InterfaceRef]  # ✅ 使用 InterfaceRef
    entities: list[str]
    depends_on_modules: list[str]
    acceptance_criteria: list[str]
    requirement_ids: list[str]
    # ❌ 缺少: tech_stack
    # ❌ 缺少: runtime
```

#### ImplementationDesign (depth=2)
```python
class ImplementationDesign:
    schema_version: Literal[1]
    design_id: str
    depth: Literal[2]
    parent_design_id: str
    module_id: str
    provided_interfaces: list[ContractInterfaceInput]  # ⚠️ 使用更详细的结构
    consumed_interfaces: list[ConsumedInterfaceRefInput]  # ⚠️ 不同的类型
    implementation_units: list[ContractImplementationUnitInput]
    required_test_types: list[str]
    requirement_ids: list[str]
    # ❌ 缺少: tech_stack
    # ❌ 缺少: runtime
```

## 问题汇总

### ❌ 严重问题

1. **ConsumedInterfaceRefInput 缺少消费方式字段**
   - 问题：consumed_interfaces 无法声明 consumption_type
   - 影响：消费者无法明确说明如何消费接口
   - 位置：`contract_input.py` line 83-101

2. **架构模型缺少 tech_stack 和 runtime 字段**
   - 问题：验证器需要这些信息来推断消费方式
   - 影响：无法在架构阶段进行技术栈驱动的验证
   - 位置：`ModuleRef`, `ModuleDesign`, `ImplementationDesign`

3. **InterfaceRef 和 ContractInterfaceInput 的语义不一致**
   - InterfaceRef：轻量级引用（只有 interface_id + summary）
   - ContractInterfaceInput：完整定义（包含 kind, name, owner_unit, signature 等）
   - 问题：depth=1 使用 InterfaceRef，depth=2 使用 ContractInterfaceInput，字段不统一

### ⚠️ 中等问题

4. **consumed_interfaces 的类型不一致**
   - ModuleDesign：使用 `list[InterfaceRef]`
   - ImplementationDesign：使用 `list[ConsumedInterfaceRefInput]`
   - 问题：同样的语义概念用了不同的类型

5. **字段命名不一致**
   - InterfaceRef 使用 `summary`
   - ConsumedInterfaceRefInput 使用 `usage`
   - 问题：虽然有迁移逻辑，但概念上是同一个东西

## 建议的修复方案

### 方案 1：最小侵入式修复（推荐）

1. **为 ConsumedInterfaceRefInput 添加消费方式字段**
   ```python
   class ConsumedInterfaceRefInput(_ContractModel):
       interface_id: str
       usage: str | None
       required: bool
       consumption_type: Literal["import_code", "http_call", "process_spawn", "shared_schema"] | None = None
       # 新增字段，可选
   ```

2. **移除验证器对 tech_stack 的依赖**
   - 只验证已声明的 consumption_type 是否一致
   - 不进行技术栈驱动的推理（那是 Architecture Agent 的职责）

3. **更新 Architecture Agent 的 backstory**
   - 明确要求为 consumed_interfaces 也声明 consumption_type
   - 提供清晰的示例

### 方案 2：彻底重构（长期）

1. **统一接口表示**
   ```python
   class InterfaceDeclaration:
       interface_id: str
       consumption_type: Literal["import_code", "http_call", "process_spawn", "shared_schema"]
       confidence: Literal["high", "medium", "low"] = "high"
       # provided 独有字段
       kind: str | None = None
       signature: str | None = None
       # consumed 独有字段
       usage: str | None = None
       required: bool = True
   ```

2. **为模块添加技术栈字段**
   ```python
   class ModuleRef:
       # ... 现有字段
       tech_stack: list[str] = Field(default_factory=list)
       runtime: Literal["browser", "server", "docker", "static"] | None = None
   ```

## 下一步行动

1. ✅ 已完成：为 InterfaceRef 和 ContractInterfaceInput 添加 consumption_type 字段
2. 🔄 进行中：修复验证器对不存在字段的依赖
3. ⏳ 待办：为 ConsumedInterfaceRefInput 添加 consumption_type 字段
4. ⏳ 待办：更新 Architecture Agent 要求为 consumed_interfaces 声明消费方式
