"""测试技术栈验证器。"""

import pytest

from app.orchestration.tech_stack_verification import (
    TechStackVerifier,
    TechStackVerificationResult,
    format_verification_errors,
)


class TestTechStackVerificationResult:
    """测试验证结果类。"""

    def test_valid_result(self):
        """测试有效结果。"""
        result = TechStackVerificationResult({
            "tech_stack": "react",
            "exists": True,
            "normalized_name": "react",
            "category": "frontend_framework",
            "runtime": "browser",
            "confidence": "high",
            "reason": "React is a popular frontend framework",
        })

        assert result.is_valid
        assert not result.should_warn
        assert not result.should_block

    def test_low_confidence_result(self):
        """测试低置信度结果。"""
        result = TechStackVerificationResult({
            "tech_stack": "obscure-lib",
            "exists": True,
            "confidence": "low",
            "reason": "Limited documentation found",
        })

        assert not result.is_valid
        assert result.should_warn
        assert not result.should_block

    def test_non_existent_result(self):
        """测试不存在的技术栈。"""
        result = TechStackVerificationResult({
            "tech_stack": "fake-stack",
            "exists": False,
            "reason": "No evidence found",
        })

        assert not result.is_valid
        assert not result.should_warn
        assert result.should_block


class TestTechStackVerifier:
    """测试技术栈验证器。"""

    def test_verify_mainstream_stacks(self):
        """测试验证主流技术栈。"""
        stacks = ["react", "fastapi", "postgresql", "docker"]

        results = TechStackVerifier.verify_batch(stacks)

        assert len(results) == 4
        for stack in stacks:
            assert stack in results
            result = results[stack]
            assert result.exists
            assert result.confidence in ["high", "medium"]

    def test_verify_invalid_stacks(self):
        """测试验证无效技术栈。"""
        stacks = ["python", "javascript", "fake-lib-12345"]

        results = TechStackVerifier.verify_batch(stacks)

        # 编程语言和假技术栈应该被标记为不存在
        for stack in stacks:
            assert stack in results
            result = results[stack]
            # 编程语言应该被拒绝
            if stack in ["python", "javascript"]:
                assert not result.exists

    def test_normalize_case(self):
        """测试自动规范化大小写。"""
        stacks = ["React", "FastAPI", "PostgreSQL"]

        results = TechStackVerifier.verify_batch(stacks)

        # 应该自动规范化为小写
        for stack in stacks:
            result = results[stack]
            if result.exists:
                assert result.normalized_name == stack.lower()

    def test_verify_with_context(self):
        """测试带上下文的验证。"""
        stacks = ["react"]
        context = {
            "blueprint_id": "test-blueprint",
            "module_id": "frontend-ui"
        }

        results = TechStackVerifier.verify_batch(stacks, context)

        assert "react" in results
        assert results["react"].exists

    def test_verify_single(self):
        """测试单个技术栈验证。"""
        result = TechStackVerifier.verify_single("vite")

        assert result.exists
        assert result.tech_stack == "vite"

    def test_empty_list(self):
        """测试空列表。"""
        results = TechStackVerifier.verify_batch([])

        assert results == {}


class TestFormatVerificationErrors:
    """测试错误格式化。"""

    def test_format_errors(self):
        """测试格式化错误。"""
        results = {
            "fake-lib": TechStackVerificationResult({
                "tech_stack": "fake-lib",
                "exists": False,
                "reason": "Not found",
            }),
            "python": TechStackVerificationResult({
                "tech_stack": "python",
                "exists": False,
                "reason": "Programming language not accepted",
            }),
        }

        formatted = format_verification_errors(results)

        assert "fake-lib" in formatted
        assert "python" in formatted
        assert "❌" in formatted

    def test_format_warnings(self):
        """测试格式化警告。"""
        results = {
            "obscure-lib": TechStackVerificationResult({
                "tech_stack": "obscure-lib",
                "exists": True,
                "confidence": "low",
                "reason": "Limited documentation",
            }),
        }

        formatted = format_verification_errors(results)

        assert "obscure-lib" in formatted
        assert "⚠️" in formatted

    def test_format_mixed(self):
        """测试混合错误和警告。"""
        results = {
            "fake-lib": TechStackVerificationResult({
                "tech_stack": "fake-lib",
                "exists": False,
                "reason": "Not found",
            }),
            "obscure-lib": TechStackVerificationResult({
                "tech_stack": "obscure-lib",
                "exists": True,
                "confidence": "low",
                "reason": "Limited documentation",
            }),
            "react": TechStackVerificationResult({
                "tech_stack": "react",
                "exists": True,
                "confidence": "high",
                "reason": "Popular framework",
            }),
        }

        formatted = format_verification_errors(results)

        # 应该同时包含错误和警告
        assert "fake-lib" in formatted
        assert "obscure-lib" in formatted
        # 有效的技术栈不应该出现
        assert "react" not in formatted

    def test_format_no_errors(self):
        """测试没有错误的情况。"""
        results = {
            "react": TechStackVerificationResult({
                "tech_stack": "react",
                "exists": True,
                "confidence": "high",
                "reason": "Popular framework",
            }),
        }

        formatted = format_verification_errors(results)

        assert formatted == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
