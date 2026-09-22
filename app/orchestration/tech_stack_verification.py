"""技术栈验证工具 - 使用分层验证机制。

这是一个轻量级验证工具，在架构设计阶段验证声明的技术栈是否真实存在。
采用分层重试机制：
- 第0次：只允许主流技术栈（从工具选择）
- 第1次：允许自定义技术栈，但验证格式
- 第2次：使用 LLM 验证真实性
"""

from __future__ import annotations

import json
import re
from typing import Any

from crewai import Agent, Task, Crew
from app.llm.factory import build_llm
from app.agent.tools.select_tech_stack import ALL_MAINSTREAM_STACKS


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
    """技术栈验证器 - 分层验证机制。"""

    # 禁止使用的技术栈（编程语言、泛泛词汇等）
    BLACKLIST = {
        # 编程语言
        "python", "javascript", "typescript", "java", "go", "rust", "c", "cpp", "c++",
        # 泛泛词汇
        "frontend", "backend", "database", "framework", "library", "tool",
        # 文件扩展名
        ".json", ".js", ".py", ".ts", ".html", ".css",
    }

    # 自定义技术栈的格式规则
    CUSTOM_STACK_PATTERN = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
    CUSTOM_STACK_MIN_LENGTH = 2
    CUSTOM_STACK_MAX_LENGTH = 30

    @classmethod
    def validate_with_retry(
        cls,
        tech_stacks: list[str],
        retry_level: int = 0,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        验证技术栈（支持重试级别）。

        Args:
            tech_stacks: 待验证的技术栈列表
            retry_level: 重试级别 (0=首次, 1=第一次重试, 2=第二次重试)
            context: 上下文信息

        Returns:
            {
                "valid": bool,
                "errors": [str],
                "warnings": [str],
                "retry_instruction": str | None,
            }
        """
        errors = []
        warnings = []
        retry_instruction = None

        for stack in tech_stacks:
            # 检查黑名单
            if stack.lower() in cls.BLACKLIST or any(stack.endswith(ext) for ext in [".json", ".js", ".py"]):
                errors.append(
                    f"'{stack}' 不是有效的技术栈"
                    f"（{'编程语言' if stack.lower() in ['python', 'javascript', 'java'] else '不合法的声明'}）"
                )
                continue

            # 第0次：只允许主流技术栈
            if retry_level == 0:
                if stack not in ALL_MAINSTREAM_STACKS:
                    errors.append(
                        f"'{stack}' 不在主流技术栈列表中"
                    )
            # 第1次：允许自定义，但验证格式
            elif retry_level == 1:
                if stack not in ALL_MAINSTREAM_STACKS:
                    if not cls._is_valid_custom_format(stack):
                        errors.append(
                            f"'{stack}' 格式不符合规范"
                            f"（必须是小写字母、数字、短横线，长度2-30）"
                        )
                    else:
                        warnings.append(
                            f"'{stack}' 是自定义技术栈，将使用 LLM 验证真实性"
                        )
            # 第2次：使用 LLM 验证
            else:
                # 这个级别会在下面的 LLM 验证中处理
                pass

        # 生成重试指令
        if errors:
            if retry_level == 0:
                retry_instruction = (
                    "首次验证失败。你必须调用 select_tech_stack 工具获取可用的主流技术栈列表，"
                    "然后从返回的 available_stacks 中选择。"
                    "\n禁止声明："
                    "\n- 编程语言（python, javascript, java）"
                    "\n- 文件名（schemas.json）"
                    "\n- 泛泛词汇（frontend, backend）"
                )
            elif retry_level == 1:
                retry_instruction = (
                    "第一次重试失败。如果你需要使用主流列表中没有的技术栈，"
                    "请确保格式符合规范：小写字母、数字、短横线分隔，长度2-30字符。"
                    "\n并说明为什么主流技术栈无法满足需求。"
                )
            else:
                retry_instruction = (
                    "已达到最大重试次数。请联系管理员审核你声明的技术栈。"
                )

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "retry_instruction": retry_instruction,
        }

    @classmethod
    def _is_valid_custom_format(cls, stack: str) -> bool:
        """验证自定义技术栈的格式。"""
        if not cls.CUSTOM_STACK_PATTERN.match(stack):
            return False
        if len(stack) < cls.CUSTOM_STACK_MIN_LENGTH or len(stack) > cls.CUSTOM_STACK_MAX_LENGTH:
            return False
        return True

    @classmethod
    def verify_batch(
        cls,
        tech_stacks: list[str],
        context: dict[str, Any] | None = None,
        verification_model: str | None = None,
    ) -> dict[str, TechStackVerificationResult]:
        """
        批量验证技术栈（使用 LLM，用于第2次重试）。

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
        unique_stacks = list(dict.fromkeys(tech_stacks))
        canonical = {stack: stack.strip().lower() for stack in unique_stacks}
        mainstream = {str(item).lower(): item for item in ALL_MAINSTREAM_STACKS}

        # 过滤出需要 LLM 验证的技术栈（不在主流列表中的）
        custom_stacks = [s for s in unique_stacks if canonical[s] not in mainstream and canonical[s] not in cls.BLACKLIST]

        results = {}

        # 黑名单在边界处确定性拒绝，并且不进入外部验证。
        for stack in unique_stacks:
            if canonical[stack] in cls.BLACKLIST:
                results[stack] = TechStackVerificationResult({
                    "tech_stack": stack, "exists": False,
                    "normalized_name": canonical[stack], "confidence": "high",
                    "reason": "不是可接受的技术栈声明",
                })

        # 主流技术栈直接通过
        for stack in unique_stacks:
            if canonical[stack] in mainstream:
                results[stack] = TechStackVerificationResult({
                    "tech_stack": stack,
                    "exists": True,
                    "normalized_name": canonical[stack],
                    "confidence": "high",
                    "reason": "主流技术栈",
                })

        # 自定义技术栈使用 LLM 验证
        if custom_stacks:
            llm_results = cls._verify_with_llm(custom_stacks, context)
            results.update(llm_results)

        return results

    @classmethod
    def _verify_with_llm(
        cls,
        tech_stacks: list[str],
        context: dict[str, Any] | None = None,
    ) -> dict[str, TechStackVerificationResult]:
        """使用 LLM 验证自定义技术栈。"""

        prompt = f"""你是技术栈验证专家。请验证以下自定义技术栈是否真实存在。

技术栈列表: {tech_stacks}
模块上下文: {context or {}}

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

请基于你的知识库判断，不要猜测。如果不确定，标记 confidence: "low"。
"""

        # 使用 CrewAI 调用 LLM
        try:
            llm = build_llm(selection=None)

            verifier_agent = Agent(
                role="Tech Stack Verifier",
                goal="Verify if custom tech stacks are real and valid",
                backstory="You are an expert in validating technology stacks.",
                llm=llm,
                allow_delegation=False,
                verbose=False,
            )

            verify_task = Task(
                description=prompt,
                agent=verifier_agent,
                expected_output="JSON object with verification results"
            )

            crew = Crew(
                agents=[verifier_agent],
                tasks=[verify_task],
                verbose=False
            )

            result = crew.kickoff()
            response = str(result)

        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Tech stack LLM verification failed: {e}")

            # LLM 调用失败，标记为低置信度
            return {
                stack: TechStackVerificationResult({
                    "tech_stack": stack,
                    "exists": True,
                    "normalized_name": stack.lower(),
                    "confidence": "low",
                    "reason": f"LLM 验证失败，无法确认技术栈: {str(e)[:50]}",
                })
                for stack in tech_stacks
            }

        # 解析结果
        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            return {
                stack: TechStackVerificationResult({
                    "tech_stack": stack,
                    "exists": True,
                    "normalized_name": stack.lower(),
                    "confidence": "low",
                    "reason": "验证响应解析失败，无法确认技术栈",
                })
                for stack in tech_stacks
            }

        results = {}
        for item in data.get("results", []):
            result = TechStackVerificationResult(item)
            results[result.tech_stack] = result

        # 检查是否所有输入都有结果
        for stack in tech_stacks:
            if stack not in results:
                results[stack] = TechStackVerificationResult({
                    "tech_stack": stack,
                    "exists": True,
                    "normalized_name": stack.lower(),
                    "confidence": "low",
                    "reason": "验证器未返回结果，状态为未确认",
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
