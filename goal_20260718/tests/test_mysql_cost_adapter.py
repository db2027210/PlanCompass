from __future__ import annotations

import unittest

from mysql_cost_adapter import CostSnapshotItem, MySQLCLIClient, MySQLCostAdapter


class ResizeClient:
    def __init__(self, observed_sizes):
        self.observed_sizes = iter(observed_sizes)
        self.statements = []

    def execute(self, sql):
        self.statements.append(sql)
        if sql == "SELECT @@GLOBAL.innodb_buffer_pool_size":
            return str(next(self.observed_sizes))
        return ""


class CostAdapterTest(unittest.TestCase):
    def test_client_adds_server_side_select_timeout(self):
        client = MySQLCLIClient({"query_timeout_sec": 10})
        sql = client._args("SELECT 1")[-1]
        self.assertEqual(sql, "SET SESSION MAX_EXECUTION_TIME=9000;SELECT 1")

    def test_allowlisted_numeric_globals_are_validated(self):
        MySQLCostAdapter.validate_names({"innodb_adaptive_hash_index": 0})
        MySQLCostAdapter.validate_names({"innodb_thread_concurrency": 0})
        MySQLCostAdapter.validate_names({"innodb_spin_wait_delay": 1000})
        MySQLCostAdapter.validate_names({"innodb_log_spin_cpu_pct_hwm": 50})
        MySQLCostAdapter.validate_names({"innodb_spin_wait_pause_multiplier": 50})
        MySQLCostAdapter.validate_names({"innodb_flush_log_at_trx_commit": 1})
        with self.assertRaises(ValueError):
            MySQLCostAdapter.validate_names({"unsafe_arbitrary_variable": 1})

    def test_apply_waits_until_buffer_pool_resize_is_visible(self):
        target = 3 * 1024**3
        client = ResizeClient([2 * 1024**3, target])
        MySQLCostAdapter(client).apply({"innodb_buffer_pool_size": target})
        self.assertIn(f"SET GLOBAL innodb_buffer_pool_size={target}", client.statements[0])
        self.assertEqual(
            client.statements.count("SELECT @@GLOBAL.innodb_buffer_pool_size"),
            2,
        )

    def test_restore_waits_until_original_buffer_pool_size_is_visible(self):
        target = 2 * 1024**3
        client = ResizeClient([target])
        snapshot = [
            CostSnapshotItem(
                "innodb_buffer_pool_size",
                "global_variable",
                target,
                target,
            )
        ]
        MySQLCostAdapter(client).restore(snapshot)
        self.assertIn(f"SET GLOBAL innodb_buffer_pool_size={target}", client.statements[0])
        self.assertEqual(client.statements[-1], "SELECT @@GLOBAL.innodb_buffer_pool_size")


if __name__ == "__main__":
    unittest.main()
