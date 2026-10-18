#!/usr/bin/env python3
"""Regression tests for fail-closed PostgreSQL data-file cache eviction."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import binary_plan_resource_tuner as tuner


class CacheEvictionFailClosedTest(unittest.TestCase):
    def test_empty_directory_is_not_reported_as_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = tuner.evict_file_cache_under_path(temp_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["files"], 0)
        self.assertEqual(result["skipped"], "no_files_evicted")

    def test_regular_file_is_evicted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_file = Path(temp_dir) / "relation"
            data_file.write_bytes(b"postgres-cache-test")
            result = tuner.evict_file_cache_under_path(temp_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["files"], 1)
        self.assertEqual(result["bytes"], len(b"postgres-cache-test"))

    def test_postgres_container_fallback_runs_as_postgres(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"files": 3, "bytes": 4096, "errors": 0, "rc": 0}\n',
            stderr="",
        )
        tuner._FADVISE_HELPER_INSTALLED.add("pg-test-worker")
        with mock.patch.object(tuner, "ensure_fadvise_helper", return_value=(Path("/tmp/helper"), "")):
            with mock.patch.object(tuner, "run_docker_cmd", return_value=completed) as run:
                result = tuner.evict_file_cache_in_container(
                    "pg-test-worker",
                    container_user="postgres",
                )

        command = run.call_args.args[0]
        self.assertEqual(command[command.index("-u") + 1], "postgres")
        self.assertTrue(result["ok"])
        self.assertEqual(result["files"], 3)
        self.assertEqual(
            result["method"],
            "container_postgres_posix_fadvise_DONTNEED",
        )


if __name__ == "__main__":
    unittest.main()
