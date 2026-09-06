"""测试模板选择逻辑。"""

import unittest
import tempfile
from pathlib import Path
from app.planner.template_selector import select_template


class TemplateSelectorTest(unittest.TestCase):
    """测试根据项目状态和用户意图选择模板。"""

    def test_select_default_for_new_project(self) -> None:
        """新项目(无 contract)应该选择 delivery_default。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            template_id = select_template(tmpdir, "实现一个 Todo 应用")
            self.assertEqual(template_id, "delivery_default")

    def test_select_incremental_when_contract_exists(self) -> None:
        """已有 project-contract.json 应该选择 delivery_incremental。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建 contract 文件
            contract_dir = Path(tmpdir) / ".projectos" / "architecture"
            contract_dir.mkdir(parents=True)
            contract_file = contract_dir / "project-contract.json"
            contract_file.write_text("{}")

            template_id = select_template(tmpdir, "添加新功能")
            self.assertEqual(template_id, "delivery_incremental")

    def test_select_architecture_only_when_explicitly_requested(self) -> None:
        """明确请求只要架构设计应该选择 architecture_only。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_cases = [
                "只要架构设计",
                "我只需要架构,不要实现",
                "仅架构设计即可",
                "architecture only please",
                "只做架构设计,不需要代码",
            ]
            for goal in test_cases:
                with self.subTest(goal=goal):
                    template_id = select_template(tmpdir, goal)
                    self.assertEqual(template_id, "architecture_only",
                                   f"goal='{goal}' 应该选择 architecture_only")

    def test_default_trumps_architecture_only_when_contract_exists(self) -> None:
        """已有 contract 时,即使请求架构也应该选择 incremental (因为基线已确定)。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建 contract 文件
            contract_dir = Path(tmpdir) / ".projectos" / "architecture"
            contract_dir.mkdir(parents=True)
            contract_file = contract_dir / "project-contract.json"
            contract_file.write_text("{}")

            # 即使请求架构,因为 contract 存在,也应该走增量
            template_id = select_template(tmpdir, "只要架构设计")
            self.assertEqual(template_id, "delivery_incremental",
                           "已有基线时应优先选择 incremental")

    def test_case_insensitive_matching(self) -> None:
        """关键词匹配应该不区分大小写。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            template_id = select_template(tmpdir, "ARCHITECTURE ONLY")
            self.assertEqual(template_id, "architecture_only")


if __name__ == "__main__":
    unittest.main()
