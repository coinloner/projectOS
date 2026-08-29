import tempfile
import unittest
from pathlib import Path

from app.skill.store import SkillStore
from app.skill.module import SkillModule


class SkillStoreTest(unittest.TestCase):
    def test_builtin_skills_are_discoverable_and_rendered(self) -> None:
        store = SkillStore()
        self.assertIn("python.http-service.v1", store.refs())
        rendered = store.render(("python.http-service.v1", "security.baseline.v1"))
        self.assertIn("Python HTTP Service", rendered)
        self.assertIn("Security Baseline", rendered)

    def test_project_skill_overrides_builtin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / ".projectos" / "skills"
            skill_dir.mkdir(parents=True)
            (skill_dir / "python.http-service.v1.md").write_text(
                "# Project HTTP\n\n项目约束", encoding="utf-8"
            )
            document = SkillStore(directory).load("python.http-service.v1")
            self.assertEqual(document.source, "project")
            self.assertIn("项目约束", document.content)

    def test_unknown_skill_is_explicit_error(self) -> None:
        with self.assertRaises(FileNotFoundError):
            SkillStore().load("missing.skill.v1")

    def test_agent_assignment_merges_with_work_item_refs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            module = SkillModule(directory)
            module.assign("code_agent", ["python.http-service.v1"])
            self.assertEqual(
                module.resolve("code_agent", ("security.baseline.v1",)),
                ("security.baseline.v1", "python.http-service.v1"),
            )

    def test_natural_language_skill_label_is_ignored_as_non_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            module = SkillModule(directory)
            self.assertEqual(
                module.resolve("code_agent", ("领域建模", "python.http-service.v1")),
                ("python.http-service.v1",),
            )

    def test_unknown_contract_skill_is_not_rendered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            module = SkillModule(directory)
            guidance, refs = module.render_for("code_agent", ("PostgreSQL", "python.http-service.v1"))
            self.assertEqual(refs, ("python.http-service.v1",))
            self.assertIn("Python HTTP Service", guidance)


if __name__ == "__main__":
    unittest.main()
