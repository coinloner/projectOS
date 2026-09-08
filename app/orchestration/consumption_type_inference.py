"""消费方式推理引擎。

基于技术栈和运行时约束推断模块的消费方式，而不是基于模块类型。
这是架构设计的核心：关注"如何被消费"，而不是"是什么类型"。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ConsumptionType(str, Enum):
    """模块的消费方式类型。"""
    IMPORT_CODE = "import_code"      # 通过 import/require 直接调用代码
    HTTP_CALL = "http_call"          # 通过 HTTP 请求调用
    PROCESS_SPAWN = "process_spawn"  # 启动独立进程
    SHARED_SCHEMA = "shared_schema"  # 共享数据结构定义


@dataclass(frozen=True)
class ConsumptionInference:
    """消费方式推理结果。"""
    consumption_type: ConsumptionType
    confidence: str  # "high" | "medium" | "low"
    rationale: str
    requires_code_verification: bool = False
    interface_suggestion: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式。"""
        return {
            "consumption_type": self.consumption_type.value,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "requires_code_verification": self.requires_code_verification,
            "interface_suggestion": self.interface_suggestion,
        }


class ConsumptionTypeInferenceEngine:
    """基于技术栈约束推断模块的消费方式。"""

    # 技术栈 → 消费方式的映射规则
    TECH_STACK_RULES = {
        # 前端框架 → HTTP 服务
        "react": {
            "consumption_type": ConsumptionType.HTTP_CALL,
            "confidence": "high",
            "rationale": "React 应用通过 dev server（如 Vite、Webpack）提供 HTTP 服务",
            "interface_suggestion": {
                "protocol": "http",
                "typical_port": 5173,  # Vite default
            }
        },
        "vue": {
            "consumption_type": ConsumptionType.HTTP_CALL,
            "confidence": "high",
            "rationale": "Vue 应用通过 dev server 提供 HTTP 服务",
            "interface_suggestion": {
                "protocol": "http",
                "typical_port": 5173,
            }
        },
        "vite": {
            "consumption_type": ConsumptionType.HTTP_CALL,
            "confidence": "high",
            "rationale": "Vite 是前端开发服务器，提供 HTTP 服务",
            "interface_suggestion": {
                "protocol": "http",
                "typical_port": 5173,
            }
        },

        # 后端框架 → 双重接口（HTTP + import）
        "fastapi": {
            "consumption_type": ConsumptionType.HTTP_CALL,
            "confidence": "high",
            "rationale": "FastAPI 主要提供 HTTP API，也可作为 Python 模块被 import",
            "additional_types": [ConsumptionType.IMPORT_CODE],
            "interface_suggestion": {
                "protocol": "http",
                "typical_port": 8000,
            }
        },
        "flask": {
            "consumption_type": ConsumptionType.HTTP_CALL,
            "confidence": "high",
            "rationale": "Flask 主要提供 HTTP API，也可作为 Python 模块被 import",
            "additional_types": [ConsumptionType.IMPORT_CODE],
            "interface_suggestion": {
                "protocol": "http",
                "typical_port": 5000,
            }
        },
        "express": {
            "consumption_type": ConsumptionType.HTTP_CALL,
            "confidence": "high",
            "rationale": "Express 主要提供 HTTP API，也可作为 Node.js 模块被 require",
            "additional_types": [ConsumptionType.IMPORT_CODE],
            "interface_suggestion": {
                "protocol": "http",
                "typical_port": 3000,
            }
        },

        # 数据库 → 独立进程或嵌入式
        "sqlite": {
            "consumption_type": ConsumptionType.IMPORT_CODE,
            "confidence": "high",
            "rationale": "SQLite 是嵌入式数据库，通过编程语言的库（如 sqlite3）使用",
        },
        "postgresql": {
            "consumption_type": ConsumptionType.PROCESS_SPAWN,
            "confidence": "high",
            "rationale": "PostgreSQL 是独立数据库进程，通过网络协议访问",
            "interface_suggestion": {
                "protocol": "postgresql",
                "typical_port": 5432,
            }
        },
        "redis": {
            "consumption_type": ConsumptionType.PROCESS_SPAWN,
            "confidence": "high",
            "rationale": "Redis 是独立服务进程，通过网络协议访问",
            "interface_suggestion": {
                "protocol": "redis",
                "typical_port": 6379,
            }
        },

        # Schema/配置文件 → 共享数据
        "json-schema": {
            "consumption_type": ConsumptionType.SHARED_SCHEMA,
            "confidence": "high",
            "rationale": "JSON Schema 是纯数据定义，多个模块读取同一份文件",
        },
        "openapi": {
            "consumption_type": ConsumptionType.SHARED_SCHEMA,
            "confidence": "high",
            "rationale": "OpenAPI 规范是纯数据定义，用于生成代码或文档",
        },
    }

    # 运行时环境的约束
    RUNTIME_CONSTRAINTS = {
        "browser": {
            "must_be": ConsumptionType.HTTP_CALL,
            "rationale": "浏览器环境只能通过网络访问服务器资源",
        },
        "docker": {
            "must_be": ConsumptionType.PROCESS_SPAWN,
            "rationale": "Docker 容器是独立进程，通过网络或 IPC 通信",
        },
    }

    @classmethod
    def infer(cls, module_info: dict[str, Any]) -> ConsumptionInference:
        """
        推断模块的消费方式。

        Args:
            module_info: 模块信息，包含:
                - tech_stack: list[str] - 技术栈
                - runtime: str - 运行时环境 (browser, server, docker, etc.)
                - purpose: str - 模块用途描述

        Returns:
            ConsumptionInference: 推理结果
        """
        tech_stack = module_info.get("tech_stack", [])
        runtime = module_info.get("runtime", "unknown")
        purpose = module_info.get("purpose", "")

        # 1. 检查运行时约束（硬约束）
        if runtime in cls.RUNTIME_CONSTRAINTS:
            constraint = cls.RUNTIME_CONSTRAINTS[runtime]
            return ConsumptionInference(
                consumption_type=constraint["must_be"],
                confidence="high",
                rationale=f"运行时约束: {constraint['rationale']}",
                requires_code_verification=False,
            )

        # 2. 检查技术栈规则
        for tech in tech_stack:
            tech_lower = tech.lower()
            if tech_lower in cls.TECH_STACK_RULES:
                rule = cls.TECH_STACK_RULES[tech_lower]
                return ConsumptionInference(
                    consumption_type=rule["consumption_type"],
                    confidence=rule["confidence"],
                    rationale=rule["rationale"],
                    requires_code_verification=False,
                    interface_suggestion=rule.get("interface_suggestion"),
                )

        # 3. 基于用途的启发式推断
        if "schema" in purpose.lower() or "registry" in purpose.lower():
            return ConsumptionInference(
                consumption_type=ConsumptionType.SHARED_SCHEMA,
                confidence="medium",
                rationale="模块用途包含 'schema' 或 'registry'，推测为数据定义",
                requires_code_verification=True,
            )

        if "api" in purpose.lower() or "server" in purpose.lower():
            return ConsumptionInference(
                consumption_type=ConsumptionType.HTTP_CALL,
                confidence="medium",
                rationale="模块用途包含 'api' 或 'server'，推测为 HTTP 服务",
                requires_code_verification=True,
            )

        # 4. 无法推断
        return ConsumptionInference(
            consumption_type=ConsumptionType.IMPORT_CODE,  # 默认假设
            confidence="low",
            rationale="无法从技术栈或运行时推断，需要代码验证",
            requires_code_verification=True,
        )

    @classmethod
    def infer_multiple(cls, module_info: dict[str, Any]) -> list[ConsumptionInference]:
        """
        推断模块可能提供的多种消费方式。

        某些模块（如 FastAPI）可以同时提供多种消费方式。

        Returns:
            list[ConsumptionInference]: 可能的消费方式列表
        """
        primary = cls.infer(module_info)
        results = [primary]

        # 检查是否有额外的消费方式
        tech_stack = module_info.get("tech_stack", [])
        for tech in tech_stack:
            tech_lower = tech.lower()
            if tech_lower in cls.TECH_STACK_RULES:
                rule = cls.TECH_STACK_RULES[tech_lower]
                additional_types = rule.get("additional_types", [])
                for additional_type in additional_types:
                    results.append(ConsumptionInference(
                        consumption_type=additional_type,
                        confidence="medium",
                        rationale=f"{tech} 也可以作为代码模块被 import",
                        requires_code_verification=False,
                    ))

        return results

    @classmethod
    def validate_consumption(cls, provider_type: ConsumptionType,
                            consumer_context: dict[str, Any]) -> dict[str, Any]:
        """
        验证消费方式的可行性。

        Args:
            provider_type: 提供者的消费方式
            consumer_context: 消费者的上下文信息

        Returns:
            dict: 验证结果 {"is_valid": bool, "reason": str}
        """
        consumer_runtime = consumer_context.get("runtime", "unknown")

        # 规则 1: 浏览器环境不能 import 服务器代码
        if consumer_runtime == "browser" and provider_type == ConsumptionType.IMPORT_CODE:
            return {
                "is_valid": False,
                "reason": "浏览器环境不能直接 import 服务器端代码，必须通过 HTTP 调用",
                "suggestion": "将提供者声明为 HTTP_CALL 类型，或使用编译时工具（如 Webpack）打包",
            }

        # 规则 2: 不同进程不能直接 import
        provider_runtime = consumer_context.get("provider_runtime", "unknown")
        if (provider_type == ConsumptionType.IMPORT_CODE and
            consumer_runtime != provider_runtime and
            consumer_runtime != "unknown" and
            provider_runtime != "unknown"):
            return {
                "is_valid": False,
                "reason": f"不同运行时环境（{consumer_runtime} vs {provider_runtime}）不能直接 import 代码",
                "suggestion": "使用 HTTP_CALL 或 PROCESS_SPAWN 进行跨进程通信",
            }

        # 规则 3: SHARED_SCHEMA 只能被读取，不能被调用
        if provider_type == ConsumptionType.SHARED_SCHEMA:
            if consumer_context.get("consumption_intent") == "function_call":
                return {
                    "is_valid": False,
                    "reason": "SHARED_SCHEMA 是数据定义，不能像函数一样调用",
                    "suggestion": "将消费关系改为 'read_schema' 而不是 'call'",
                }

        return {
            "is_valid": True,
            "reason": "消费方式符合约束",
        }


def create_interface_declaration(
    module_id: str,
    inference: ConsumptionInference,
    capability_description: str,
) -> dict[str, Any]:
    """
    根据推理结果创建接口声明。

    Args:
        module_id: 模块 ID
        inference: 消费方式推理结果
        capability_description: 接口提供的能力描述

    Returns:
        dict: 接口声明
    """
    interface_id = f"{module_id}.{inference.consumption_type.value}"

    declaration = {
        "interface_id": interface_id,
        "consumption_type": inference.consumption_type.value,
        "confidence": inference.confidence,
        "capability": capability_description,
        "rationale": inference.rationale,
        "to_be_verified": inference.requires_code_verification,
    }

    # 添加协议建议
    if inference.interface_suggestion:
        declaration.update(inference.interface_suggestion)

    return declaration
