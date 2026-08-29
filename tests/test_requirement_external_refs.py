import tempfile
import unittest

from app.project.project import Project
from app.orchestration.trace import TraceStore


class RequirementExternalRefsTest(unittest.TestCase):
    def test_only_explicit_section_is_persisted(self) -> None:
        directory = tempfile.TemporaryDirectory()
        try:
            Project.create_at(directory.name, name="refs-demo")
            traces = TraceStore(directory.name)
            trace = traces.start_trace("goal")
            traces.snapshot_requirement(trace, "# 需求\n\n普通 REST 接口\n")
            self.assertEqual(traces.external_references(), ())
            traces.snapshot_requirement(trace, "# 需求\n\n## 外部规范\n- REF: RFC 9110\n")
            self.assertEqual(traces.external_references(), ("RFC 9110",))
        finally:
            directory.cleanup()


if __name__ == "__main__":
    unittest.main()
