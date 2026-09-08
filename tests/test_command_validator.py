"""测试命令验证器的安全性检查。"""

import pytest

from app.runtime.command_validator import PythonCommandValidator, CommandValidatorFactory


class TestPythonCommandValidator:
    """测试 Python 命令验证器。"""

    def setup_method(self) -> None:
        self.validator = PythonCommandValidator()

    def test_valid_simple_module(self) -> None:
        """测试简单模块导入格式。"""
        result = self.validator.validate("python -m todo.cli")
        assert result.is_valid
        assert result.reason is None

    def test_valid_with_pythonpath(self) -> None:
        """测试带 PYTHONPATH 的命令。"""
        result = self.validator.validate("PYTHONPATH=backend python -m todo.cli")
        assert result.is_valid

    def test_valid_with_multiple_pythonpath(self) -> None:
        """测试多个 PYTHONPATH。"""
        result = self.validator.validate("PYTHONPATH=backend:src python -m app.main")
        assert result.is_valid

    def test_valid_with_args(self) -> None:
        """测试带参数的命令。"""
        result = self.validator.validate("python -m uvicorn app.main:app --host 0.0.0.0 --port 8000")
        assert result.is_valid

    def test_valid_python3_version(self) -> None:
        """测试 python3 版本命令。"""
        result = self.validator.validate("python3 -m todo.cli")
        assert result.is_valid

        result = self.validator.validate("python3.11 -m todo.cli")
        assert result.is_valid

    def test_reject_empty_command(self) -> None:
        """拒绝空命令。"""
        result = self.validator.validate("")
        assert not result.is_valid
        assert "空" in result.reason

    def test_reject_shell_injection_semicolon(self) -> None:
        """拒绝包含分号的命令（shell 注入）。"""
        result = self.validator.validate("python -m todo.cli; rm -rf /")
        assert not result.is_valid
        assert "不安全字符" in result.reason

    def test_reject_shell_injection_pipe(self) -> None:
        """拒绝包含管道的命令。"""
        result = self.validator.validate("python -m todo.cli | malicious")
        assert not result.is_valid
        assert "不安全字符" in result.reason

    def test_reject_shell_injection_ampersand(self) -> None:
        """拒绝包含 & 的命令。"""
        result = self.validator.validate("python -m todo.cli & malicious")
        assert not result.is_valid

    def test_reject_shell_injection_dollar(self) -> None:
        """拒绝包含 $ 的命令（变量替换）。"""
        result = self.validator.validate("python -m todo.cli $MALICIOUS")
        assert not result.is_valid

    def test_reject_shell_injection_backtick(self) -> None:
        """拒绝包含反引号的命令。"""
        result = self.validator.validate("python -m todo.cli `malicious`")
        assert not result.is_valid

    def test_reject_shell_injection_redirect(self) -> None:
        """拒绝包含重定向的命令。"""
        result = self.validator.validate("python -m todo.cli > /tmp/output")
        assert not result.is_valid

    def test_reject_non_module_import(self) -> None:
        """拒绝非 -m 模块导入形式。"""
        result = self.validator.validate("python script.py")
        assert not result.is_valid
        assert "格式不符合" in result.reason

    def test_reject_absolute_pythonpath(self) -> None:
        """拒绝绝对路径的 PYTHONPATH。"""
        result = self.validator.validate("PYTHONPATH=/etc/passwd python -m todo.cli")
        assert not result.is_valid
        assert "不安全路径" in result.reason

    def test_reject_parent_directory_in_pythonpath(self) -> None:
        """拒绝包含父目录引用的 PYTHONPATH。"""
        result = self.validator.validate("PYTHONPATH=../../../etc python -m todo.cli")
        assert not result.is_valid
        assert "不安全路径" in result.reason

    def test_reject_invalid_module_name(self) -> None:
        """拒绝不合法的模块名。"""
        result = self.validator.validate("python -m .todo")
        assert not result.is_valid
        assert "模块名不合法" in result.reason

    def test_reject_double_dots_in_module(self) -> None:
        """拒绝模块名包含连续的点。"""
        result = self.validator.validate("python -m todo..cli")
        assert not result.is_valid

    def test_reject_unsafe_args(self) -> None:
        """拒绝包含不安全字符的参数。"""
        result = self.validator.validate("python -m todo.cli --arg='malicious'")
        assert not result.is_valid
        # 单引号会被正则表达式拒绝,所以错误信息是格式不符合
        assert "格式不符合" in result.reason or "不安全" in result.reason


class TestCommandValidatorFactory:
    """测试命令验证器工厂。"""

    def test_get_python_validator(self) -> None:
        """测试获取 Python 验证器。"""
        validator = CommandValidatorFactory.get_validator('python')
        assert validator is not None
        assert isinstance(validator, PythonCommandValidator)

    def test_get_python_case_insensitive(self) -> None:
        """测试获取验证器时大小写不敏感。"""
        validator = CommandValidatorFactory.get_validator('PYTHON')
        assert validator is not None
        assert isinstance(validator, PythonCommandValidator)

    def test_get_unsupported_language(self) -> None:
        """测试获取不支持的语言验证器。"""
        validator = CommandValidatorFactory.get_validator('unsupported')
        assert validator is None
