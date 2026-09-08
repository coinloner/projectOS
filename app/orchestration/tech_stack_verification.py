"""技术栈验证工具 - 使用 LLM 验证技术栈的真实性。

这是一个轻量级验证工具，在架构设计阶段验证声明的技术栈是否真实存在。
采用 LLM 验证而非人工维护白名单，避免维护成本和非即时性问题。
"""

from __future__ import annotations

import json
from typing import Any

from crewai import Agent, Task, Crew
from app.llm.factory import build_llm


class TechStackVerificationResult:
    """技术栈验证结果。"""

    def __init__(self, data: dict[str, Any]):
        self.tech_stack = data["tech_stack"]
        self.exists = data["exists"]
        self.normalized_name = data.get("normalized_name", self.tech_stack)
        self.category = data.get("category")
        self.runtime = data.get("runtime")
        self.confidence = data.get("confidence", "medium")
        self.reason = data.get("reason", "")
        self.warnings = data.get("warnings", [])

    @property
    def is_valid(self) -> bool:
        """是否通过验证。"""
        return self.exists and self.confidence in ["high", "medium"]

    @property
    def should_warn(self) -> bool:
        """是否应该警告（存在但置信度低）。"""
        return self.exists and self.confidence == "low"

    @property
    def should_block(self) -> bool:
        """是否应该阻塞（不存在或不合法）。"""
        return not self.exists

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式。"""
        return {
            "tech_stack": self.tech_stack,
            "exists": self.exists,
            "normalized_name": self.normalized_name,
            "category": self.category,
            "runtime": self.runtime,
            "confidence": self.confidence,
            "reason": self.reason,
            "warnings": self.warnings,
        }


class TechStackVerifier:
    """技术栈验证器。"""

    VERIFICATION_PROMPT = """你是技术栈验证专家。请验证以下技术栈是否真实存在。

技术栈列表: {tech_stacks}
模块上下文: {context}

请返回 JSON 格式的验证结果:
{{
  "results": [
    {{
      "tech_stack": "原始输入",
      "exists": true/false,
      "normalized_name": "规范化名称（小写、短横线分隔）",
      "category": "frontend_framework" | "backend_framework" | "database" | "build_tool" | "schema" | "container" | "other",
      "runtime": "browser" | "server" | "docker" | "static" | "embedded" | null,
      "confidence": "high" | "medium" | "low",
      "reason": "判断理由（50字以内）",
      "warnings": ["注意事项（可选）"]
    }}
  ]
}}

验证标准:
1. 必须是真实存在的技术（有官方文档、npm/PyPI包、GitHub仓库等）
2. 不接受编程语言本身（python, javascript, java, go 等）
3. 不接受过于泛泛的词（frontend, backend, framework, library）
4. 优先使用官方标准名称（小写、短横线分隔）
5. 如果是别名或大小写错误，在 normalized_name 中纠正

示例:
- "React" → exists: true, normalized_name: "react", category: "frontend_framework", runtime: "browser"
- "FastAPI" → exists: true, normalized_name: "fastapi", category: "backend_framework", runtime: "server"
- "python" → exists: false, reason: "编程语言不算技术栈"
- "unknown-lib" → exists: false, reason: "无法找到相关资料"
- "json-schema" → exists: true, normalized_name: "json-schema", category: "schema", runtime: "static"

请基于你的知识库判断，不要猜测。如果不确定，标记 confidence: "low"。
"""

    @classmethod
    def verify_batch(
        cls,
        tech_stacks: list[str],
        context: dict[str, Any] | None = None,
        verification_model: str | None = None,
    ) -> dict[str, TechStackVerificationResult]:
        """
        批量验证技术栈。

        Args:
            tech_stacks: 待验证的技术栈列表
            context: 上下文信息（可选），如 blueprint_id, module_id 等
            verification_model: 验证使用的模型（可选），默认使用配置的模型

        Returns:
            {tech_stack: result} 的字典
        """
        if not tech_stacks:
            return {}

        # 去重
        unique_stacks = list(set(tech_stacks))

        # 构造提示词
        prompt = cls.VERIFICATION_PROMPT.format(
            tech_stacks=unique_stacks,
            context=context or {}
        )

        # 使用 CrewAI 调用 LLM
        try:
            llm = build_llm(selection=None)  # 使用默认配置

            # 创建一个简单的 Agent 来执行验证任务
            verifier_agent = Agent(
                role="Tech Stack Verifier",
                goal="Verify if tech stacks are real and valid",
                backstory="You are an expert in validating technology stacks.",
                llm=llm,
                allow_delegation=False,
                verbose=False,
            )

            # 创建验证任务
            verify_task = Task(
                description=prompt,
                agent=verifier_agent,
                expected_output="JSON object with verification results"
            )

            # 执行任务
            crew = Crew(
                agents=[verifier_agent],
                tasks=[verify_task],
                verbose=False
            )

            result = crew.kickoff()
            response = str(result)

        except Exception as e:
            # LLM 调用失败，标记所有技术栈为低置信度
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Tech stack verification LLM call failed: {e}")

            return {
                stack: TechStackVerificationResult({
                    "tech_stack": stack,
                    "exists": True,
                    "normalized_name": stack.lower(),
                    "confidence": "low",
                    "reason": f"验证服务调用失败: {str(e)[:50]}",
                })
                for stack in unique_stacks
            }

        # 解析结果
        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            # LLM 返回格式错误，标记所有技术栈为低置信度
            return {
                stack: TechStackVerificationResult({
                    "tech_stack": stack,
                    "exists": True,
                    "normalized_name": stack.lower(),
                    "confidence": "low",
                    "reason": "验证响应解析失败",
                })
                for stack in unique_stacks
            }

        results = {}
        for item in data.get("results", []):
            result = TechStackVerificationResult(item)
            results[result.tech_stack] = result

        # 检查是否所有输入都有结果
        for stack in unique_stacks:
            if stack not in results:
                # LLM 遗漏了某些输入，标记为低置信度
                results[stack] = TechStackVerificationResult({
                    "tech_stack": stack,
                    "exists": True,
                    "normalized_name": stack.lower(),
                    "confidence": "low",
                    "reason": "验证器未返回结果",
                })

        return results

    @classmethod
    def verify_single(
        cls,
        tech_stack: str,
        context: dict[str, Any] | None = None,
        verification_model: str | None = None,
    ) -> TechStackVerificationResult:
        """验证单个技术栈。"""
        results = cls.verify_batch([tech_stack], context, verification_model)
        return results.get(tech_stack)


def format_verification_errors(results: dict[str, TechStackVerificationResult]) -> str:
    """格式化验证错误为可读文本。"""
    errors = []
    warnings = []

    for stack, result in results.items():
        if result.should_block:
            errors.append(f"❌ '{stack}': {result.reason}")
        elif result.should_warn:
            warnings.append(f"⚠️  '{stack}': {result.reason}")

    lines = []
    if errors:
        lines.append("技术栈验证失败（以下技术栈不存在或不合法）:")
        lines.extend(f"  {err}" for err in errors)

    if warnings:
        if lines:
            lines.append("")
        lines.append("技术栈验证警告（以下技术栈置信度低）:")
        lines.extend(f"  {warn}" for warn in warnings)

    return "\n".join(lines) if lines else ""


__all__ = [
    "TechStackVerifier",
    "TechStackVerificationResult",
    "format_verification_errors",
]
