"""测试消费方式推理引擎。"""

import pytest

from app.orchestration.consumption_type_inference import (
    ConsumptionType,
    ConsumptionTypeInferenceEngine,
    create_interface_declaration,
)


def test_infer_react_frontend():
    """测试 React 前端的推理。"""
    module_info = {
        "module_id": "expense-frontend",
        "tech_stack": ["react", "vite"],
        "runtime": "browser",
        "purpose": "用户界面"
    }

    result = ConsumptionTypeInferenceEngine.infer(module_info)

    assert result.consumption_type == ConsumptionType.HTTP_CALL
    assert result.confidence == "high"
    assert "HTTP 服务" in result.rationale or "dev server" in result.rationale
    assert result.interface_suggestion is not None
    assert result.interface_suggestion.get("protocol") == "http"


def test_infer_fastapi_backend():
    """测试 FastAPI 后端的推理。"""
    module_info = {
        "module_id": "expense-api",
        "tech_stack": ["fastapi"],
        "runtime": "server",
        "purpose": "REST API"
    }

    result = ConsumptionTypeInferenceEngine.infer(module_info)

    assert result.consumption_type == ConsumptionType.HTTP_CALL
    assert result.confidence == "high"
    assert "8000" in str(result.interface_suggestion.get("typical_port"))


def test_infer_multiple_fastapi():
    """测试 FastAPI 的多种消费方式。"""
    module_info = {
        "module_id": "expense-api",
        "tech_stack": ["fastapi"],
        "runtime": "server"
    }

    results = ConsumptionTypeInferenceEngine.infer_multiple(module_info)

    # FastAPI 应该提供 HTTP_CALL 和 IMPORT_CODE 两种方式
    types = [r.consumption_type for r in results]
    assert ConsumptionType.HTTP_CALL in types
    assert ConsumptionType.IMPORT_CODE in types


def test_infer_sqlite():
    """测试 SQLite 嵌入式数据库的推理。"""
    module_info = {
        "module_id": "expense-persistence",
        "tech_stack": ["sqlite"],
        "runtime": "server",
        "purpose": "数据持久化"
    }

    result = ConsumptionTypeInferenceEngine.infer(module_info)

    assert result.consumption_type == ConsumptionType.IMPORT_CODE
    assert result.confidence == "high"
    assert "嵌入式" in result.rationale or "sqlite3" in result.rationale


def test_infer_postgresql():
    """测试 PostgreSQL 独立进程的推理。"""
    module_info = {
        "module_id": "database",
        "tech_stack": ["postgresql"],
        "runtime": "docker",
        "purpose": "数据库"
    }

    result = ConsumptionTypeInferenceEngine.infer(module_info)

    assert result.consumption_type == ConsumptionType.PROCESS_SPAWN
    assert result.confidence == "high"


def test_infer_schema_registry():
    """测试 Schema Registry 的推理。"""
    module_info = {
        "module_id": "expense-schema",
        "tech_stack": ["json-schema"],
        "runtime": "static",
        "purpose": "数据模型定义"
    }

    result = ConsumptionTypeInferenceEngine.infer(module_info)

    assert result.consumption_type == ConsumptionType.SHARED_SCHEMA
    assert result.confidence == "high"


def test_infer_from_purpose():
    """测试从用途推断消费方式。"""
    module_info = {
        "module_id": "unknown-module",
        "tech_stack": [],
        "runtime": "unknown",
        "purpose": "提供 REST API 服务"
    }

    result = ConsumptionTypeInferenceEngine.infer(module_info)

    assert result.consumption_type == ConsumptionType.HTTP_CALL
    assert result.confidence == "medium"
    assert result.requires_code_verification is True


def test_infer_unknown_low_confidence():
    """测试未知模块返回低置信度。"""
    module_info = {
        "module_id": "mystery-module",
        "tech_stack": ["unknown-tech"],
        "runtime": "unknown",
        "purpose": "未知用途"
    }

    result = ConsumptionTypeInferenceEngine.infer(module_info)

    assert result.confidence == "low"
    assert result.requires_code_verification is True


def test_validate_browser_cannot_import():
    """测试浏览器环境不能 import 服务器代码。"""
    result = ConsumptionTypeInferenceEngine.validate_consumption(
        provider_type=ConsumptionType.IMPORT_CODE,
        consumer_context={
            "runtime": "browser",
            "module_id": "frontend"
        }
    )

    assert result["is_valid"] is False
    assert "浏览器" in result["reason"]
    assert "HTTP" in result["suggestion"]


def test_validate_cross_process_import():
    """测试跨进程不能 import。"""
    result = ConsumptionTypeInferenceEngine.validate_consumption(
        provider_type=ConsumptionType.IMPORT_CODE,
        consumer_context={
            "runtime": "browser",
            "provider_runtime": "server",
            "module_id": "frontend"
        }
    )

    assert result["is_valid"] is False
    assert "不同运行时" in result["reason"] or "不同进程" in result["reason"]


def test_validate_shared_schema_not_callable():
    """测试 SHARED_SCHEMA 不能被调用。"""
    result = ConsumptionTypeInferenceEngine.validate_consumption(
        provider_type=ConsumptionType.SHARED_SCHEMA,
        consumer_context={
            "runtime": "server",
            "consumption_intent": "function_call"
        }
    )

    assert result["is_valid"] is False
    assert "数据定义" in result["reason"]


def test_validate_valid_http_call():
    """测试合法的 HTTP 调用。"""
    result = ConsumptionTypeInferenceEngine.validate_consumption(
        provider_type=ConsumptionType.HTTP_CALL,
        consumer_context={
            "runtime": "browser",
            "provider_runtime": "server"
        }
    )

    assert result["is_valid"] is True


def test_create_interface_declaration():
    """测试创建接口声明。"""
    module_info = {
        "tech_stack": ["react", "vite"],
        "runtime": "browser"
    }

    inference = ConsumptionTypeInferenceEngine.infer(module_info)

    declaration = create_interface_declaration(
        module_id="expense-frontend",
        inference=inference,
        capability_description="Display expense list and create form"
    )

    assert declaration["interface_id"] == "expense-frontend.http_call"
    assert declaration["consumption_type"] == "http_call"
    assert declaration["confidence"] == "high"
    assert declaration["capability"] == "Display expense list and create form"
    assert "protocol" in declaration
    assert declaration["protocol"] == "http"


def test_runtime_constraint_overrides_tech_stack():
    """测试运行时约束优先于技术栈。"""
    module_info = {
        "module_id": "unknown",
        "tech_stack": ["unknown"],
        "runtime": "browser"  # 硬约束
    }

    result = ConsumptionTypeInferenceEngine.infer(module_info)

    # 浏览器环境必须是 HTTP_CALL
    assert result.consumption_type == ConsumptionType.HTTP_CALL
    assert result.confidence == "high"


def test_docker_runtime_constraint():
    """测试 Docker 运行时约束。"""
    module_info = {
        "module_id": "service",
        "tech_stack": [],
        "runtime": "docker"
    }

    result = ConsumptionTypeInferenceEngine.infer(module_info)

    assert result.consumption_type == ConsumptionType.PROCESS_SPAWN
    assert result.confidence == "high"
    assert "容器" in result.rationale or "进程" in result.rationale
