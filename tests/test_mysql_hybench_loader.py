import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mysql_hybench_loader as loader


class HyBenchLoaderTests(unittest.TestCase):
    def make_tree(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "conf").mkdir()
        (root / "conf" / "ddl_mysql.sql").write_text("CREATE TABLE x(id int);", encoding="utf-8")
        data = root / "Data_10x"
        data.mkdir()
        for spec in loader.TABLE_SPECS.values():
            (data / spec.file_name).write_text("1,2,3\n", encoding="utf-8")
        return temp, root

    def args(self, root, **changes):
        values = dict(host="127.0.0.1", port=13306, database="hybench_sf10x", user="root",
                      hybench_root=root, scale="10x", mysql_bin="mysql",
                      password_env="TEST_HYBENCH_PASSWORD", timeout=10,
                      execute=False, validate_only=False, confirm_database=None,
                      resume_missing=False)
        values.update(changes)
        return argparse.Namespace(**values)

    def test_dry_run_never_connects(self):
        temp, root = self.make_tree()
        self.addCleanup(temp.cleanup)
        with patch("mysql_hybench_loader.subprocess.run") as run:
            details = loader.plan(self.args(root))
        run.assert_not_called()
        self.assertEqual(details["mode"], "dry-run")
        self.assertEqual(len(details["tables"]), 8)

    def test_load_sql_handles_generated_empty_fields(self):
        sql = loader.load_sql(Path("/tmp/data"))
        self.assertIn("fresh_ts=NULLIF(@fresh_ts,'')", sql)
        self.assertIn("contract_timestamp=NULLIF(@contract_timestamp,'')", sql)
        self.assertIn("@ignored_trailing", sql)
        self.assertEqual(sql.count("LOAD DATA LOCAL INFILE"), 8)

    def test_execute_requires_exact_confirmation_before_connecting(self):
        temp, root = self.make_tree()
        self.addCleanup(temp.cleanup)
        args = self.args(root, execute=True, confirm_database="wrong")
        with patch("mysql_hybench_loader.subprocess.run") as run:
            with self.assertRaisesRegex(RuntimeError, "confirm-database"):
                loader.execute(args, loader.plan(args))
        run.assert_not_called()

    def test_nonempty_table_aborts_before_ddl_or_load(self):
        temp, root = self.make_tree()
        self.addCleanup(temp.cleanup)
        args = self.args(root, execute=True, confirm_database="hybench_sf10x")
        with patch("mysql_hybench_loader.existing_counts", return_value={"customer": 4}), \
             patch("mysql_hybench_loader.run_mysql") as mysql:
            with self.assertRaisesRegex(RuntimeError, "non-empty"):
                loader.execute(args, loader.plan(args))
        mysql.assert_not_called()

    def test_password_is_not_in_command(self):
        temp, root = self.make_tree()
        self.addCleanup(temp.cleanup)
        args = self.args(root)
        command = loader.mysql_command(args)
        self.assertFalse(any("password" in item.lower() for item in command))

    def test_existing_counts_uses_exact_counts_not_innodb_estimate(self):
        temp, root = self.make_tree()
        self.addCleanup(temp.cleanup)
        args = self.args(root)
        with patch("mysql_hybench_loader.run_mysql", side_effect=["customer\t0\n", "customer\t9\n"]) as mysql:
            counts = loader.existing_counts(args)
        self.assertEqual(counts, {"customer": 9})
        self.assertEqual(mysql.call_count, 2)

    def test_resume_missing_loads_only_empty_tables(self):
        temp, root = self.make_tree()
        self.addCleanup(temp.cleanup)
        args = self.args(root, execute=True, confirm_database="hybench_sf10x", resume_missing=True)
        present = {name: 1 for name in loader.TABLE_ORDER if name != "loantrans"}
        with patch("mysql_hybench_loader.existing_counts", return_value=present), \
             patch("mysql_hybench_loader.run_mysql", side_effect=["", "", "\n".join(f"{n}\t1" for n in loader.TABLE_ORDER)]) as mysql:
            result = loader.execute(args, loader.plan(args))
        self.assertTrue(result["validated"])
        load_statement = mysql.call_args_list[1].args[1]
        self.assertIn("INTO TABLE `loantrans`", load_statement)
        self.assertNotIn("INTO TABLE `customer`", load_statement)


if __name__ == "__main__":
    unittest.main()
