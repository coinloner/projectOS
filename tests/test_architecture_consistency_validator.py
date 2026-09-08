"""测试架构一致性验证器。"""

import pytest

from app.orchestration.architecture_consistency_validator import (
    ArchitectureConsistencyValidator,
    format_violations,
)


def test_validate_missing_interface():
    """测试检测缺失的接口。"""
    designs = [
        {
            "module_id": "frontend",
            "tech_stack": ["react"],
            "runtime": "browser",
            "provided_interfaces": [],
            "consumed_interfaces": [
                {
                    "interface_id": "backend-api.rest",
                    "consumption_type": "http_call"
                }
            ]
        },
        {
            "module_id": "backend-api",
            "tech_stack": ["fastapi"],
            "runtime": "server",
            "provided_interfaces": [],  # 忘记声明接口
            "consumed_interfaces": []
        }
    ]

    validator = ArchitectureConsistencyValidator()
    violations = validator.validate(designs)

    # 应该发现 backend-api.rest 接口不存在
    assert len(violations) > 0
    assert any(v.rule_name == "interface_must_exist" for v in violations)
    assert any("backend-api.rest" in v.message for v in violations)


def test_validate_frontend_no_code_interface():
    """测试前端不能提供代码接口。"""
    designs = [
        {
            "module_id": "frontend",
            "tech_stack": ["react", "vite"],
            "runtime": "browser",
            "provided_interfaces": [
                {
                    "interface_id": "frontend.ui",
                    "consumption_type": "import_code"  # 错误：前端不能提供 import_code
                }
            ],
            "consumed_interfaces": []
        }
    ]

    validator = ArchitectureConsistencyValidator()
    violations = validator.validate(designs)

    # 应该发现前端错误地提供了代码接口
    assert len(violations) > 0
    assert any(v.rule_name == "frontend_no_code_interface" for v in violations)
    assert any(v.severity == "error" for v in violations)


def test_validate_cross_process_import():
    """测试跨进程不能使用 import_code。"""
    designs = [
        {
            "module_id": "frontend",
            "tech_stack": ["react"],
            "runtime": "browser",
            "provided_interfaces": [],
            "consumed_interfaces": [
                {
                    "interface_id": "backend.service",
                    "consumption_type": "import_code"  # 错误：浏览器不能 import 服务器代码
                }
            ]
        },
        {
            "module_id": "backend",
            "tech_stack": ["fastapi"],
            "runtime": "server",
            "provided_interfaces": [
                {
                    "interface_id": "backend.service",
                    "consumption_type": "import_code"
                }
            ],
            "consumed_interfaces": []
        }
    ]

    validator = ArchitectureConsistencyValidator()
    violations = validator.validate(designs)

    # 应该发现跨进程的 import
    assert len(violations) > 0
    assert any(v.rule_name == "no_cross_process_import" for v in violations)


def test_validate_consumption_type_mismatch():
    """测试消费方式类型不匹配。"""
    designs = [
        {
            "module_id": "consumer",
            "tech_stack": ["python"],
            "runtime": "server",
            "provided_interfaces": [],
            "consumed_interfaces": [
                {
                    "interface_id": "provider.api",
                    "consumption_type": "http_call"  # 期望 HTTP
                }
            ]
        },
        {
            "module_id": "provider",
            "tech_stack": ["python"],
            "runtime": "server",
            "provided_interfaces": [
                {
                    "interface_id": "provider.api",
                    "consumption_type": "import_code"  # 但提供的是 import
                }
            ],
            "consumed_interfaces": []
        }
    ]

    validator = ArchitectureConsistencyValidator()
    violations = validator.validate(designs)

    # 应该发现类型不匹配
    assert len(violations) > 0
    assert any(v.rule_name == "consumption_type_mismatch" for v in violations)


def test_validate_circular_dependency():
    """测试检测循环依赖。"""
    designs = [
        {
            "module_id": "module-a",
            "tech_stack": [],
            "runtime": "server",
            "provided_interfaces": [
                {"interface_id": "module-a.service", "consumption_type": "import_code"}
            ],
            "consumed_interfaces": [
                {"interface_id": "module-b.service", "consumption_type": "import_code"}
            ]
        },
        {
            "module_id": "module-b",
            "tech_stack": [],
            "runtime": "server",
            "provided_interfaces": [
                {"interface_id": "module-b.service", "consumption_type": "import_code"}
            ],
            "consumed_interfaces": [
                {"interface_id": "module-c.service", "consumption_type": "import_code"}
            ]
        },
        {
            "module_id": "module-c",
            "tech_stack": [],
            "runtime": "server",
            "provided_interfaces": [
                {"interface_id": "module-c.service", "consumption_type": "import_code"}
            ],
            "consumed_interfaces": [
                {"interface_id": "module-a.service", "consumption_type": "import_code"}  # 循环
            ]
        }
    ]

    validator = ArchitectureConsistencyValidator()
    violations = validator.validate(designs)

    # 应该发现循环依赖
    assert len(violations) > 0
    assert any(v.rule_name == "no_circular_dependency" for v in violations)
    assert any("循环" in v.message for v in violations)


def test_validate_valid_architecture():
    """测试验证合法的架构。"""
    designs = [
        {
            "module_id": "frontend",
            "tech_stack": ["react"],
            "runtime": "browser",
            "provided_interfaces": [
                {
                    "interface_id": "frontend.ui",
                    "consumption_type": "http_call"  # 正确：前端提供 HTTP 服务
                }
            ],
            "consumed_interfaces": [
                {
                    "interface_id": "backend.api",
                    "consumption_type": "http_call"
                }
            ]
        },
        {
            "module_id": "backend",
            "tech_stack": ["fastapi"],
            "runtime": "server",
            "provided_interfaces": [
                {
                    "interface_id": "backend.api",
                    "consumption_type": "http_call"
                }
            ],
            "consumed_interfaces": [
                {
                    "interface_id": "database.repo",
                    "consumption_type": "import_code"
                }
            ]
        },
        {
            "module_id": "database",
            "tech_stack": ["sqlite"],
            "runtime": "server",
            "provided_interfaces": [
                {
                    "interface_id": "database.repo",
                    "consumption_type": "import_code"
                }
            ],
            "consumed_interfaces": []
        }
    ]

    validator = ArchitectureConsistencyValidator()
    violations = validator.validate(designs)

    # 不应该有违规
    assert len(violations) == 0


def test_suggest_missing_interface_fix_frontend():
    """测试为前端缺失接口提供修复建议。"""
    designs = [
        {
            "module_id": "integration-test",
            "tech_stack": ["pytest"],
            "runtime": "server",
            "provided_interfaces": [],
            "consumed_interfaces": [
                {
                    "interface_id": "expense-frontend.ui",
                    "consumption_type": "http_call"
                }
            ]
        },
        {
            "module_id": "expense-frontend",
            "tech_stack": ["react", "vite"],
            "runtime": "browser",
            "purpose": "用户界面",
            "provided_interfaces": [],  # 忘记声明
            "consumed_interfaces": []
        }
    ]

    validator = ArchitectureConsistencyValidator()
    violations = validator.validate(designs)

    # 应该有违规
    assert len(violations) > 0
    violation = violations[0]

    # 建议应该提到前端是 HTTP 服务
    assert "前端" in violation.suggestion or "HTTP" in violation.suggestion
    assert "test_target" in violation.suggestion or "runtime_dependency" in violation.suggestion


def test_format_violations_no_violations():
    """测试格式化空违规列表。"""
    violations = []
    text = format_violations(violations)

    assert "✅" in text
    assert "通过" in text


def test_format_violations_with_errors():
    """测试格式化有错误的违规列表。"""
    from app.orchestration.architecture_consistency_validator import ArchitectureViolation

    violations = [
        ArchitectureViolation(
            severity="error",
            rule_name="interface_must_exist",
            module_id="module-a",
            message="接口不存在",
            suggestion="请声明接口",
            interface_id="module-b.api"
        ),
        ArchitectureViolation(
            severity="warning",
            rule_name="low_confidence",
            module_id="module-c",
            message="推理置信度低",
            suggestion="需要代码验证"
        )
    ]

    text = format_violations(violations)

    assert "❌" in text
    assert "1 个错误" in text
    assert "1 个警告" in text
    assert "interface_must_exist" in text
    assert "接口不存在" in text
    assert "请声明接口" in text


def test_same_runtime_import_allowed():
    """测试同一运行时环境的 import 是允许的。"""
    designs = [
        {
            "module_id": "module-a",
            "tech_stack": ["python"],
            "runtime": "server",
            "provided_interfaces": [],
            "consumed_interfaces": [
                {
                    "interface_id": "module-b.service",
                    "consumption_type": "import_code"
                }
            ]
        },
        {
            "module_id": "module-b",
            "tech_stack": ["python"],
            "runtime": "server",  # 同一运行时
            "provided_interfaces": [
                {
                    "interface_id": "module-b.service",
                    "consumption_type": "import_code"
                }
            ],
            "consumed_interfaces": []
        }
    ]

    validator = ArchitectureConsistencyValidator()
    violations = validator.validate(designs)

    # 不应该有跨进程 import 的违规
    assert not any(v.rule_name == "no_cross_process_import" for v in violations)


def test_integration_test_consuming_frontend():
    """测试集成测试消费前端的场景（原始问题）。"""
    designs = [
        {
            "module_id": "integration-test",
            "tech_stack": ["pytest"],
            "runtime": "server",
            "provided_interfaces": [],
            "consumed_interfaces": [
                {
                    "interface_id": "expense-frontend.ui",
                    "consumption_type": "http_call"  # 尝试消费前端
                }
            ]
        },
        {
            "module_id": "expense-frontend",
            "tech_stack": ["react", "vite"],
            "runtime": "browser",
            "provided_interfaces": [],  # 前端没有声明接口
            "consumed_interfaces": []
        }
    ]

    validator = ArchitectureConsistencyValidator()
    violations = validator.validate(designs)

    # 应该发现接口不存在
    assert len(violations) > 0
    violation = violations[0]
    assert violation.rule_name == "interface_must_exist"

    # 建议应该提到使用 test_target
    assert "test_target" in violation.suggestion or "runtime_dependency" in violation.suggestion
