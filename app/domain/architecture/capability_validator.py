"""LLM-driven capability validation for architecture decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.llm.factory import build_llm
from app.llm.config import LLMSelection


@dataclass
class ValidationResult:
    """Result of a capability validation check."""

    is_valid: bool
    reason: str
    confidence: Literal["high", "medium", "low"]
    metadata: dict[str, str] | None = None


class CapabilityValidator:
    """Validates whether requested capabilities are legitimate and implementable."""

    def __init__(self, llm_selection: LLMSelection | None = None):
        """Initialize the validator with an LLM instance."""
        self._llm = build_llm(llm_selection or LLMSelection())

    def validate_tech_stack(
        self,
        stack_name: str,
        category: str,
        context: str | None = None
    ) -> ValidationResult:
        """
        Validate whether a technology stack is legitimate and safe to use.

        Args:
            stack_name: The name of the technology stack (e.g., "vanilla-js", "react")
            category: The module category (e.g., "frontend", "backend")
            context: Optional context about how it will be used

        Returns:
            ValidationResult indicating whether the stack should be allowed
        """
        prompt = self._build_validation_prompt(stack_name, category, context)

        try:
            response = self._llm.invoke(prompt)
            return self._parse_validation_response(response.content, stack_name)
        except Exception as e:
            # On LLM failure, default to rejecting for safety
            return ValidationResult(
                is_valid=False,
                reason=f"验证失败: {str(e)}",
                confidence="low"
            )

    def _build_validation_prompt(
        self,
        stack_name: str,
        category: str,
        context: str | None
    ) -> str:
        """Build the LLM prompt for validating a technology stack."""
        return f"""你是一个技术栈验证专家。请判断以下技术栈是否应该被允许使用。

技术栈名称: {stack_name}
模块类别: {category}
使用场景: {context or "未提供"}

请从以下维度评估:

1. **真实性**: 这是一个真实存在的、被广泛认可的技术栈吗？
   - 是主流技术或标准实践
   - 有清晰的文档和社区支持
   - 不是杜撰或拼写错误

2. **可实现性**: LLM 是否有能力基于此技术栈生成可工作的代码？
   - 技术栈足够成熟和稳定
   - 有明确的实现模式
   - 不需要特殊的开发环境或工具链

3. **安全性**: 使用此技术栈是否存在安全风险？
   - 不是恶意软件或已知的不安全库
   - 不包含明显的安全漏洞
   - 符合最佳安全实践

4. **适用性**: 对于 {category} 类别，这个技术栈是否合适？
   - 技术栈与类别匹配
   - 不是明显的错误选择

请用以下 JSON 格式回复（只输出 JSON，不要其他文字）:

{{
  "is_valid": true/false,
  "reason": "简短说明为什么允许或拒绝（一句话）",
  "confidence": "high/medium/low",
  "details": {{
    "is_real_technology": true/false,
    "is_implementable": true/false,
    "is_safe": true/false,
    "is_appropriate": true/false
  }}
}}

示例 1 - 允许:
输入: stack_name="vanilla-js", category="frontend"
输出: {{"is_valid": true, "reason": "纯 JavaScript 是标准前端技术，无需框架", "confidence": "high", "details": {{"is_real_technology": true, "is_implementable": true, "is_safe": true, "is_appropriate": true}}}}

示例 2 - 拒绝:
输入: stack_name="hack-framework-2000", category="frontend"
输出: {{"is_valid": false, "reason": "不是真实存在的技术栈", "confidence": "high", "details": {{"is_real_technology": false, "is_implementable": false, "is_safe": false, "is_appropriate": false}}}}

现在请评估上述技术栈。"""

    def _parse_validation_response(
        self,
        response: str,
        stack_name: str
    ) -> ValidationResult:
        """Parse the LLM's validation response."""
        import json
        import re

        # Extract JSON from response
        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response, re.DOTALL)
        if not json_match:
            # If no JSON found, default to rejection
            return ValidationResult(
                is_valid=False,
                reason=f"无法验证技术栈 '{stack_name}'",
                confidence="low"
            )

        try:
            data = json.loads(json_match.group())
            return ValidationResult(
                is_valid=data.get("is_valid", False),
                reason=data.get("reason", "未提供原因"),
                confidence=data.get("confidence", "low"),
                metadata=data.get("details")
            )
        except json.JSONDecodeError:
            return ValidationResult(
                is_valid=False,
                reason=f"验证响应格式错误",
                confidence="low"
            )


__all__ = ["CapabilityValidator", "ValidationResult"]
