import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from mysql_system_variable_grid_runner import (
    _acquire_target_lock,
    _device_limit,
    _wait_for_quiescence,
)


class TargetLockTest(unittest.TestCase):
    def test_same_container_is_exclusive_and_different_containers_parallelize(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            first = _acquire_target_lock("unit-lock-a", output)
            try:
                with self.assertRaisesRegex(RuntimeError, "already owns"):
                    _acquire_target_lock("unit-lock-a", output)
                second = _acquire_target_lock("unit-lock-b", output)
                second.close()
            finally:
                first.close()

    def test_quiescence_requires_consecutive_empty_checks(self):
        with patch("mysql_system_variable_grid_runner._mysql",
                   side_effect=["3", "0", "0"]) as mysql, \
             patch("mysql_system_variable_grid_runner.time.sleep") as sleep:
            _wait_for_quiescence({}, timeout_s=30, stable_checks=2)
        self.assertEqual(mysql.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_device_limit_selects_only_requested_device(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "limit"
            path.write_text("8:0 1048576\n259:5 104857600\n")
            self.assertEqual(_device_limit(path, "259:5"), 104857600)
            self.assertIsNone(_device_limit(path, "259:6"))


if __name__ == "__main__":
    unittest.main()
