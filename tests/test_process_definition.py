import unittest

from app.process import (
    ProcessDefinition,
    ProcessLimits,
    ProcessRegistry,
    StageDefinition,
    TransitionRule,
    default_process_registry,
)


class ProcessDefinitionTest(unittest.TestCase):
    def test_default_process_contains_only_stage_rules(self) -> None:
        process = default_process_registry().get("software_delivery")
        assert process is not None
        self.assertEqual(process.limits.max_architecture_depth, 3)
        self.assertEqual(process.stage("architecture_module").output_kind, "ModuleDesign")
        self.assertTrue(process.transition_allowed("requirement", "architecture_blueprint"))
        self.assertFalse(process.transition_allowed("requirement", "review"))
        self.assertNotIn("domain", process.as_dict())

    def test_registry_rejects_duplicate_and_unknown_transition(self) -> None:
        process = ProcessDefinition(
            id="custom",
            name="Custom",
            stages=(StageDefinition("a", "agent", output_kind="A"),),
        )
        registry = ProcessRegistry()
        registry.register(process)
        with self.assertRaises(ValueError):
            registry.register(process)
        with self.assertRaises(ValueError):
            ProcessDefinition(
                id="bad",
                name="Bad",
                stages=(StageDefinition("a", "agent", output_kind="A"),),
                transitions=(TransitionRule("a", "missing"),),
            )

    def test_limits_are_positive(self) -> None:
        with self.assertRaises(ValueError):
            ProcessLimits(max_modules=0)


if __name__ == "__main__":
    unittest.main()
