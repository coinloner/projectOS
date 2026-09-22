"""验证项目启动命令的安全性。

防止 Agent 通过 project-contract.json 注入恶意 Docker 命令。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationResult:
    """命令验证结果。"""

    is_valid: bool
    reason: str | None = None


class PythonCommandValidator:
    """验证 Python 启动命令的安全性。

    允许的格式:
    - python -m module.name
    - python -m module.name arg1 arg2
    - PYTHONPATH=path python -m module.name
    - PYTHONPATH=path1:path2 python -m module.name

    不允许:
    - Shell 注入字符: ; | & $ ` \ ( ) { } [ ] < > \n
    - 任意文件路径访问
    - 非 -m 模块导入形式
    """

    # 危险的 shell 字符
    _SHELL_INJECTION_CHARS = {';', '|', '&', '$', '`', '\\', '(', ')', '{', '}', '[', ']', '<', '>', '\n', '\r'}

    # 允许的 Python 命令格式
    _PYTHON_MODULE_PATTERN = re.compile(
        r'^(?:PYTHONPATH=(?P<pythonpath>[a-zA-Z0-9_/.:,\-]+)\s+)?'
        r'python(?:3(?:\.\d+)?)?'
        r'\s+-m\s+'
        r'(?P<module>[a-zA-Z0-9_.\-]+)'
        r'(?:\s+(?P<args>[a-zA-Z0-9_.,=:/\-\s]+))?$'
    )

    def validate(self, command: str) -> ValidationResult:
        """验证命令是否安全。

        Args:
            command: 待验证的启动命令

        Returns:
            ValidationResult: 验证结果
        """
        if not command or not command.strip():
            return ValidationResult(is_valid=False, reason="命令为空")

        # 检查 shell 注入字符
        for char in self._SHELL_INJECTION_CHARS:
            if char in command:
                return ValidationResult(
                    is_valid=False,
                    reason=f"命令包含不安全字符: {repr(char)}"
                )

        # 检查是否匹配允许的格式
        match = self._PYTHON_MODULE_PATTERN.match(command.strip())
        if not match:
            return ValidationResult(
                is_valid=False,
                reason="命令格式不符合要求，仅支持 'python -m module.name' 或 'PYTHONPATH=path python -m module.name'"
            )

        # 验证 PYTHONPATH (如果存在)
        pythonpath = match.group('pythonpath')
        if pythonpath:
            if not self._validate_pythonpath(pythonpath):
                return ValidationResult(
                    is_valid=False,
                    reason=f"PYTHONPATH 包含不安全路径: {pythonpath}"
                )

        # 验证模块名
        module = match.group('module')
        if not self._validate_module_name(module):
            return ValidationResult(
                is_valid=False,
                reason=f"模块名不合法: {module}"
            )

        # 验证参数 (如果存在)
        args = match.group('args')
        if args and not self._validate_args(args):
            return ValidationResult(
                is_valid=False,
                reason=f"命令参数包含不安全内容: {args}"
            )

        return ValidationResult(is_valid=True)

    def _validate_pythonpath(self, pythonpath: str) -> bool:
        """验证 PYTHONPATH 是否安全。

        允许: backend, frontend, src, app, lib 等常见目录
        允许多路径用 : 分隔
        不允许: 绝对路径 /, 父目录 .., 特殊字符
        """
        if not pythonpath:
            return False

        # 分割多个路径
        paths = pythonpath.split(':')
        for path in paths:
            # 不允许空路径
            if not path.strip():
                return False

            # 不允许绝对路径
            if path.startswith('/'):
                return False

            # 不允许父目录引用
            if '..' in path:
                return False

            # 只允许字母、数字、下划线、连字符、点、斜杠
            if not re.match(r'^[a-zA-Z0-9_/.-]+$', path):
                return False

        return True

    def _validate_module_name(self, module: str) -> bool:
        """验证模块名是否合法。

        允许: my_module, my.module, my_module.sub
        不允许: 特殊字符、空模块名
        """
        if not module:
            return False

        # Python 模块名规则: 字母、数字、下划线、点
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_.]*$', module):
            return False

        # 不允许以点开头或结尾
        if module.startswith('.') or module.endswith('.'):
            return False

        # 不允许连续的点
        if '..' in module:
            return False

        return True

    def _validate_args(self, args: str) -> bool:
        """验证命令行参数是否安全。

        允许: --host 0.0.0.0, --port 8000, --workers 4
        不允许: 特殊字符、引号
        """
        if not args:
            return True

        # 只允许字母、数字、常见参数字符
        # 允许: 空格、等号、冒号、点、逗号、斜杠、连字符、下划线
        if not re.match(r'^[a-zA-Z0-9_.,=:/\-\s]+$', args):
            return False

        return True


class CommandValidatorFactory:
    """命令验证器工厂。"""

    _VALIDATORS = {
        'python': PythonCommandValidator(),
    }

    @classmethod
    def get_validator(cls, language: str) -> PythonCommandValidator | None:
        """获取指定语言的命令验证器。

        Args:
            language: 语言标识 (python, node, go 等)

        Returns:
            对应的验证器，如果不支持则返回 None
        """
        return cls._VALIDATORS.get(language.lower())
