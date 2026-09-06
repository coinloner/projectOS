"""测试旧模板的 deprecation 警告。"""

import unittest
import warnings
from app.workflow.templates import (
    project_delivery_template,
    project_delivery_dynamic_template,
    project_delivery_layered_template,
    project_delivery_minimal_template,
)


class DeprecatedTemplatesTest(unittest.TestCase):
    """验证旧模板会发出 deprecation warning。"""

    def test_project_delivery_template_deprecated(self) -> None:
        """project_delivery_template 应该发出 DeprecationWarning。"""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            template = project_delivery_template()

            # 验证模板仍然可用 (向后兼容)
            self.assertEqual(template.id, "project_delivery")

            # 验证发出了 DeprecationWarning
            self.assertEqual(len(w), 1)
            self.assertTrue(issubclass(w[0].category, DeprecationWarning))
            self.assertIn("已废弃", str(w[0].message))
            self.assertIn("delivery_default", str(w[0].message))

    def test_project_delivery_dynamic_template_deprecated(self) -> None:
        """project_delivery_dynamic_template 应该发出 DeprecationWarning。"""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            template = project_delivery_dynamic_template()

            # 验证模板仍然可用 (向后兼容)
            self.assertEqual(template.id, "project_delivery_dynamic")

            # 验证发出了 DeprecationWarning
            self.assertGreater(len(w), 0, "应该发出 deprecation warning")
            # 可能有多个 warning (例如调用了其他 deprecated 函数),只检查至少有一个
            deprecation_warnings = [warning for warning in w if issubclass(warning.category, DeprecationWarning)]
            self.assertGreater(len(deprecation_warnings), 0)

    def test_project_delivery_layered_template_deprecated(self) -> None:
        """project_delivery_layered_template 应该发出 DeprecationWarning。"""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            template = project_delivery_layered_template()

            # 验证模板仍然可用 (向后兼容)
            self.assertEqual(template.id, "project_delivery_layered")

            # 验证发出了 DeprecationWarning
            deprecation_warnings = [warning for warning in w if issubclass(warning.category, DeprecationWarning)]
            self.assertGreater(len(deprecation_warnings), 0, "应该发出至少一个 deprecation warning")

    def test_project_delivery_minimal_template_deprecated(self) -> None:
        """project_delivery_minimal_template 应该发出 DeprecationWarning。"""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            template = project_delivery_minimal_template()

            # 验证模板仍然可用 (向后兼容)
            self.assertEqual(template.id, "project_delivery_minimal")

            # 验证发出了 DeprecationWarning
            self.assertEqual(len(w), 1)
            self.assertTrue(issubclass(w[0].category, DeprecationWarning))
            self.assertIn("已废弃", str(w[0].message))


if __name__ == "__main__":
    unittest.main()
