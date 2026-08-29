import json
import os
import tempfile
import unittest
from pathlib import Path

from app.runtime.port_allocator import PortAllocator
from app.runtime.port_lifecycle import PortLifecycleManager


class FakePortAllocator(PortAllocator):
    def _is_free(self, port: int) -> bool:
        return True


class PortLifecycleTest(unittest.TestCase):
    def test_acquire_release_persists_and_reuses_ports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "leases.json"
            manager = PortLifecycleManager(str(registry))
            allocator = FakePortAllocator(port_range=(49150, 49151))

            lease = manager.acquire("run-1", 1, allocator=allocator)
            self.assertEqual(manager.active_leases()[0].owner_id, "run-1")
            self.assertEqual(manager.release(lease.lease_id, allocator=allocator), lease.ports)
            self.assertEqual(manager.active_leases(), ())

            reused = allocator.allocate(1)
            self.assertEqual(reused, list(lease.ports))
            allocator.release(reused)

    def test_active_registry_excludes_ports_allocated_by_another_allocator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = PortLifecycleManager(str(Path(directory) / "leases.json"))
            first = FakePortAllocator(port_range=(49152, 49153))
            second = FakePortAllocator(port_range=(49152, 49153))
            lease = manager.acquire("run-1", 1, allocator=first)
            other = manager.acquire("run-2", 1, allocator=second)
            self.assertNotEqual(lease.ports, other.ports)
            manager.release(lease.lease_id, allocator=first)
            manager.release(other.lease_id, allocator=second)

    def test_dead_process_leases_are_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "leases.json"
            registry.write_text(
                json.dumps({
                    "lease-dead": {
                        "lease_id": "lease-dead",
                        "owner_id": "dead",
                        "ports": [49154],
                        "pid": 999999,
                        "created_at": "2020-01-01T00:00:00+00:00",
                        "expires_at": "2999-01-01T00:00:00+00:00",
                    }
                }),
                encoding="utf-8",
            )
            manager = PortLifecycleManager(str(registry))
            self.assertEqual(manager.reclaim(), ("lease-dead",))


if __name__ == "__main__":
    unittest.main()
