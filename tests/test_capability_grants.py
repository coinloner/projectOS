import tempfile
import unittest

from app.project.project import Project
from app.tool_manager.grants import CapabilityGrantStore


class CapabilityGrantStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        Project.create_at(self.directory.name, name="grant-demo")
        self.store = CapabilityGrantStore(self.directory.name, "tr-grant")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_grant_persists_and_revoke_survives_reload(self) -> None:
        grant = self.store.grant(capability="external_documentation", source_name="docs-mcp")
        self.assertEqual(self.store.list()[0].grant_id, grant.grant_id)
        revoked = self.store.revoke(grant.grant_id)
        self.assertEqual(revoked.status, "revoked")
        self.assertEqual(self.store.list(), ())
        self.assertEqual(self.store.list(include_inactive=True)[0].status, "revoked")

    def test_project_grant_is_visible_from_other_trace(self) -> None:
        project_grant = self.store.grant(
            capability="external_documentation",
            source_name="docs-mcp",
            scope="project",
            work_item_id="node-a",
        )
        other = CapabilityGrantStore(self.directory.name, "tr-other")
        self.assertEqual(other.list()[0].grant_id, project_grant.grant_id)

    def test_trace_grant_is_not_visible_from_other_trace(self) -> None:
        grant = self.store.grant(
            capability="external_documentation", source_name="docs-mcp", scope="trace"
        )
        other = CapabilityGrantStore(self.directory.name, "tr-other")
        self.assertEqual(self.store.list()[0].grant_id, grant.grant_id)
        self.assertEqual(other.list(), ())


if __name__ == "__main__":
    unittest.main()
