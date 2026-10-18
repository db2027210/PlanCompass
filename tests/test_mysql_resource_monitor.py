import unittest

from mysql_resource_monitor import _working_set_bytes


class WorkingSetMemoryTest(unittest.TestCase):
    def test_subtracts_cgroup_v1_total_inactive_file(self):
        stat = "cache 500\ntotal_inactive_file 300\ntotal_active_file 200\n"
        self.assertEqual(_working_set_bytes(1000, stat), 700)

    def test_missing_or_oversized_inactive_file_is_safe(self):
        self.assertEqual(_working_set_bytes(1000, "cache 500\n"), 1000)
        self.assertEqual(_working_set_bytes(100, "total_inactive_file 300\n"), 0)


if __name__ == "__main__":
    unittest.main()
