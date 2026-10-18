#!/usr/bin/env python3
"""Regression tests for fail-closed startup baseline SQL reuse."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from parameter_optimizer import (
    SEALED_BASELINE_SQL_SNAPSHOT_CONTRACT,
    _load_sealed_baseline_sql_snapshot,
    _validated_read_only_model_paths,
)


class SealedStartupBaselineSqlSnapshotTests(unittest.TestCase):
    def _write_snapshot(self, root: Path, queries: list[dict]) -> tuple[Path, str]:
        path = root / "baseline_sql_snapshot.json"
        payload = {
            "schema_version": 1,
            "contract": SEALED_BASELINE_SQL_SNAPSHOT_CONTRACT,
            "queries": queries,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _config(path: Path, sha256: str, count: int) -> dict:
        return {
            "baseline_sql_snapshot_path": str(path),
            "baseline_sql_snapshot_sha256": sha256,
            "baseline_sql_expected_query_count": count,
        }

    def test_reuse_returns_only_sealed_job_queries(self) -> None:
        job_queries = [
            {
                "queryid": str(index),
                "calls": 1.0,
                "query": f"SELECT MIN(t.title) FROM title AS t WHERE t.id = ${index}",
            }
            for index in range(1, 9)
        ]
        controls = [
            {"query": f"ALTER SYSTEM SET p{index} = '{index}'"}
            for index in range(1, 8)
        ] + [
            {"query": "SELECT pg_reload_conf()"},
            {"query": "BEGIN"},
            {"query": "COMMIT"},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path, sha256 = self._write_snapshot(
                Path(temporary),
                job_queries + controls,
            )
            loaded, audit = _load_sealed_baseline_sql_snapshot(
                self._config(path, sha256, 8)
            )

        self.assertEqual(loaded, job_queries)
        self.assertEqual(audit["source_query_count"], 18)
        self.assertEqual(audit["workload_query_count"], 8)
        self.assertEqual(audit["filtered_control_query_count"], 10)
        loaded_sql = "\n".join(record["query"].lower() for record in loaded)
        self.assertNotIn("alter system", loaded_sql)
        self.assertNotIn("pg_reload_conf", loaded_sql)

    def test_path_sha_and_expected_query_count_are_all_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, sha256 = self._write_snapshot(
                Path(temporary),
                [{"query": "SELECT MIN(t.title) FROM title AS t"}],
            )
            valid = self._config(path, sha256, 1)
            for missing_key in (
                "baseline_sql_snapshot_path",
                "baseline_sql_snapshot_sha256",
                "baseline_sql_expected_query_count",
            ):
                invalid = dict(valid)
                invalid.pop(missing_key)
                with self.subTest(missing_key=missing_key):
                    with self.assertRaises(RuntimeError):
                        _load_sealed_baseline_sql_snapshot(invalid)

            wrong_sha = dict(valid)
            wrong_sha["baseline_sql_snapshot_sha256"] = "0" * 64
            with self.assertRaisesRegex(RuntimeError, "SHA mismatch"):
                _load_sealed_baseline_sql_snapshot(wrong_sha)

            wrong_count = dict(valid)
            wrong_count["baseline_sql_expected_query_count"] = 2
            with self.assertRaisesRegex(RuntimeError, "query count mismatch"):
                _load_sealed_baseline_sql_snapshot(wrong_count)

    def test_control_sql_cannot_satisfy_workload_query_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, sha256 = self._write_snapshot(
                Path(temporary),
                [
                    {"query": "ALTER SYSTEM SET random_page_cost = '4.0'"},
                    {"query": "SELECT pg_reload_conf()"},
                ],
            )
            with self.assertRaisesRegex(RuntimeError, "query count mismatch"):
                _load_sealed_baseline_sql_snapshot(
                    self._config(path, sha256, 1)
                )

    def test_model_paths_require_complete_sha_bound_read_only_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "sealed_models"
            bundle.mkdir()
            expected_hashes = {}
            for resource in ("cpu", "memory", "io"):
                filename = f"{resource}_resource_models.json"
                path = bundle / filename
                path.write_text(
                    json.dumps({"resource": resource}) + "\n",
                    encoding="utf-8",
                )
                path.chmod(0o444)
                expected_hashes[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
            config = {
                resource: {
                    "learned_model": {
                        "enabled": True,
                        "required": True,
                        "path": str(bundle / f"{resource}_resource_models.json"),
                    }
                }
                for resource in ("cpu", "memory", "io")
            }
            config["model_retraining"] = {
                "sealed_reuse_bundle": {
                    "path": str(bundle),
                    "model_sha256": expected_hashes,
                }
            }
            paths, audit = _validated_read_only_model_paths(config)
            self.assertTrue(audit["validated"])
            self.assertEqual(set(paths), {"cpu", "memory", "io"})

            mismatched = json.loads(json.dumps(config))
            mismatched["model_retraining"]["sealed_reuse_bundle"][
                "model_sha256"
            ]["cpu_resource_models.json"] = "0" * 64
            paths, audit = _validated_read_only_model_paths(mismatched)
            self.assertEqual(paths, {})
            self.assertFalse(audit["validated"])
            self.assertEqual(audit["reason"], "cpu_model_sha256_mismatch")


if __name__ == "__main__":
    unittest.main()
