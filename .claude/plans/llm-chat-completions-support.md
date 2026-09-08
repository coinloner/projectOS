# LLM 连接逻辑重写计划：支持 Chat Completions API

## 问题分析

**当前状态:**
- ProjectOS 的 LLM 模块当前主要支持 **Responses API** (`wire_api: "responses"`)
- 新添加的 provider (uuapi, totoken, portdan) 都是 **Chat Completions API** 兼容的
- 当使用 `wire_api: "responses"` 时,这些 provider 返回 401 错误
- 原有的 FHL provider 也是 Chat Completions 模式

**根本原因:**
- `app/llm/factory.py` 在 `wire_api == "responses"` 时使用 `OpenAIResponsesLLM`
- `OpenAIResponsesLLM` 调用 `/responses` endpoint,但这些 provider 只支持 `/chat/completions`
- 默认的 `wire_api` 是 `"chat_completions"`,但实际上使用的是普通 CrewAI `LLM` 类

## 实现方案

### 1. 配置层调整

**文件:** `app/llm/config.py`

**修改:**
- 保持 `wire_api` 字段,明确区分两种模式:
  - `"responses"` - 使用 OpenAI Responses API
  - `"chat_completions"` - 使用标准 Chat Completions API (默认)
- 更新所有非 Responses provider 的配置,确保它们使用 `"chat_completions"` 或省略该字段

**具体改动:**
```python
# 确保以下 provider 使用 chat_completions (或省略,因为这是默认值)
"deepseek": { ... },  # 不需要 wire_api,默认就是 chat_completions
"siliconflow": { ... },  # 同上
"fhl": { ... },  # 同上
"portdan": { ... },  # 同上
"totoken": { ... },  # 同上
"uuapi": { ... },  # 同上
```

### 2. Factory 层保持不变

**文件:** `app/llm/factory.py`

**当前逻辑:**
```python
if selected.wire_api == "responses":
    return OpenAIResponsesLLM(**llm_kwargs)
return LLM(**llm_kwargs)  # 默认使用 CrewAI 标准 LLM,支持 Chat Completions
```

**结论:** Factory 层已经正确处理了两种模式,不需要修改

### 3. 验证和测试

**步骤:**
1. 确认所有 Chat Completions provider 的配置中移除 `"wire_api": "responses"`
2. 或者显式设置为 `"wire_api": "chat_completions"`
3. 运行端到端测试验证

## 实施步骤

### Step 1: 清理配置
检查 `app/llm/config.py` 中的 `_PROVIDERS` 字典:
- `uuapi` - 移除 `"wire_api": "responses"`
- `totoken` - 移除 `"wire_api": "responses"` 
- `portdan` - 移除 `"wire_api": "responses"`
- 其他 provider - 确保没有错误的 `wire_api` 设置

### Step 2: 验证默认值
确认 `LLMSelection` dataclass 中的默认值:
```python
wire_api: str = "chat_completions"  # 已经是正确的默认值
```

### Step 3: 测试
使用 uuapi provider 运行端到端测试:
```bash
PROJECTOS_LLM_PROVIDER=uuapi python main.py \
  --project /tmp/test-delivery-default \
  --goal "实现一个命令行 Todo 应用" \
  --workflow delivery_default
```

## 预期结果

修改后:
- ✅ uuapi, totoken, portdan 等 Chat Completions provider 应该能正常工作
- ✅ 如果有使用 Responses API 的 provider,仍然可以通过 `"wire_api": "responses"` 配置
- ✅ 默认行为是使用 Chat Completions API,与大多数 OpenAI 兼容服务匹配

## 风险评估

**低风险改动:**
- 只是移除错误的配置项
- Factory 逻辑已经正确,不需要改动
- 不影响现有的 Responses API provider

**需要验证:**
- FHL provider 之前是否能正常工作
- Deepseek provider 是否使用正确的模式
