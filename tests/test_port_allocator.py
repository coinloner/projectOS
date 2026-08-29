import socket
import unittest

from app.runtime.port_allocator import PortAllocationError, PortAllocator


class PortAllocatorTest(unittest.TestCase):
    def test_allocates_ports_within_pool_in_order(self) -> None:
        allocator = PortAllocator(port_range=(8300, 8302))
        self.assertEqual(allocator.allocate(2), [8300, 8301])
        self.assertEqual(allocator.allocate(1), [8302])
        allocator.release([8300, 8301, 8302])

    def test_allocated_ports_do_not_overlap(self) -> None:
        allocator = PortAllocator(port_range=(8300, 8301))
        first = allocator.allocate(1)
        second = allocator.allocate(1)
        self.assertNotEqual(first, second)
        allocator.release([*first, *second])

    def test_released_ports_can_be_reused(self) -> None:
        allocator = PortAllocator(port_range=(8300, 8301))
        ports = allocator.allocate(2)
        allocator.release(ports)
        self.assertEqual(allocator.allocate(2), ports)
        allocator.release(ports)

    def test_skips_ports_actually_bound_by_other_processes(self) -> None:
        occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            occupied.bind(("127.0.0.1", 0))
        except OSError as error:
            occupied.close()
            if getattr(error, "errno", None) in {1, 13}:
                self.skipTest("当前沙盒禁止测试进程监听本地端口")
            raise
        occupied.listen(1)
        busy = occupied.getsockname()[1]
        try:
            allocator = PortAllocator(port_range=(busy, busy))
            with self.assertRaises(PortAllocationError):
                allocator.allocate(1)
        finally:
            occupied.close()

    def test_raises_when_pool_has_not_enough_free_ports(self) -> None:
        allocator = PortAllocator(port_range=(8300, 8301))
        allocator.allocate(2)
        with self.assertRaises(PortAllocationError):
            allocator.allocate(1)
        allocator.release([8300, 8301])

    def test_rejects_invalid_range_and_count(self) -> None:
        with self.assertRaises(ValueError):
            PortAllocator(port_range=(0, 10))
        with self.assertRaises(ValueError):
            PortAllocator(port_range=(8301, 8300))
        with self.assertRaises(ValueError):
            PortAllocator().allocate(0)


if __name__ == "__main__":
    unittest.main()
