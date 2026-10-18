#!/usr/bin/env python3
"""Offline regression tests for the v95 Algorithm-C measurement adapter."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts import mysql_algorithm_c_measurement_client_primary_v4 as adapter


def row(worker: str, scene: str, server: float, client: float) -> dict:
    return {
        "worker_id": worker,
        "scene": scene,
        "hard_gate_passed": True,
        "coverage_complete": True,
        "db_qps": server,
        "client_qps_audit": client,
    }


class AdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old_measure = adapter.predecessor.measure_batch
        self.old_load = adapter.predecessor.predecessor.load
        self.old_atomic = adapter.predecessor.predecessor.atomic
        self.old_sha = adapter.predecessor.predecessor.sha
        adapter._NORMALIZATION.clear()

        def load(path: Path):
            return json.loads(path.read_text(encoding="utf-8"))

        def atomic(path: Path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf-8")

        adapter.predecessor.predecessor.load = load
        adapter.predecessor.predecessor.atomic = atomic
        adapter.predecessor.predecessor.sha = lambda path: hashlib.sha256(
            path.read_bytes()
        ).hexdigest()

    def tearDown(self) -> None:
        adapter.predecessor.measure_batch = self.old_measure
        adapter.predecessor.predecessor.load = self.old_load
        adapter.predecessor.predecessor.atomic = self.old_atomic
        adapter.predecessor.predecessor.sha = self.old_sha
        adapter._NORMALIZATION.clear()

    def invoke(self, output: Path, measured: dict):
        output.mkdir(parents=True, exist_ok=True)
        (output / "batch_result.json").write_text(
            json.dumps({"status": "PASS", "measurements": measured}),
            encoding="utf-8",
        )
        adapter.predecessor.measure_batch = lambda *_a, **_k: copy.deepcopy(measured)
        return adapter.measure_batch({}, {}, {}, output, "token")

    def test_mixed_memory_baseline_preserves_job_and_rebinds_tpch(self) -> None:
        measured = {
            "job1": row("job1", "job_memory", 50.0, 49.0),
            "job2": row("job2", "job_memory", 51.0, 50.0),
            "tpch1": row("tpch1", "tpch_sf4_memory", 9.0, 0.10),
            "tpch2": row("tpch2", "tpch_sf4_memory", 10.0, 0.20),
        }
        with tempfile.TemporaryDirectory() as temporary:
            result = self.invoke(Path(temporary) / "formal_incumbent_baseline", measured)
        self.assertEqual(result["job1"], measured["job1"])
        self.assertEqual(result["tpch1"]["server_db_qps_audit"], 9.0)
        self.assertEqual(result["tpch1"]["db_qps"], 0.10)
        self.assertEqual(len(adapter._NORMALIZATION["worker_baseline_client_qps"]), 2)

    def test_candidate_normalizes_only_tpch_subset(self) -> None:
        baseline = {
            "job1": row("job1", "job_memory", 50.0, 49.0),
            "job2": row("job2", "job_memory", 51.0, 50.0),
            "tpch1": row("tpch1", "tpch_sf4_memory", 9.0, 0.10),
            "tpch2": row("tpch2", "tpch_sf4_memory", 10.0, 0.20),
        }
        candidate = copy.deepcopy(baseline)
        candidate["tpch1"]["client_qps_audit"] = 0.20
        candidate["tpch2"]["client_qps_audit"] = 0.30
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.invoke(root / "formal_incumbent_baseline", baseline)
            result = self.invoke(root / "formal_outer_001", candidate)
        self.assertEqual(result["job1"], candidate["job1"])
        self.assertTrue(result["tpch1"]["same_worker_qps_normalization_applied"])
        self.assertAlmostEqual(result["tpch1"]["db_qps"], 0.30)
        self.assertAlmostEqual(result["tpch2"]["db_qps"], 0.225)

    def test_xp_single_scene_is_unchanged(self) -> None:
        measured = {
            "cpu1": row("cpu1", "hybench_xp_cpu", 100.0, 90.0),
            "cpu2": row("cpu2", "hybench_xp_cpu", 101.0, 91.0),
        }
        with tempfile.TemporaryDirectory() as temporary:
            result = self.invoke(Path(temporary) / "formal_incumbent_baseline", measured)
        self.assertEqual(result, measured)

    def test_actual_v93_failed_baseline_is_accepted_by_v94_adapter(self) -> None:
        source = Path(
            "history/mysql_four_scene_20260809/"
            "v1194_to_c_client_primary_four_scene_20260903_memory_pair_job_"
            "invalid_prelaunch_mixed_scene_20260904_0048/"
            "formal_incumbent_baseline/batch_result.json"
        )
        document = json.loads(source.read_text(encoding="utf-8"))
        measured = document["measurements"]
        with tempfile.TemporaryDirectory() as temporary:
            result = self.invoke(Path(temporary) / "formal_incumbent_baseline", measured)
        scenes = {row["scene"] for row in result.values()}
        self.assertEqual(scenes, {"job_memory", "tpch_sf4_memory"})
        self.assertEqual(
            len([row for row in result.values() if row["scene"] == "tpch_sf4_memory"]),
            2,
        )

    def test_actual_v94_full_and_single_tail_batches_all_pass(self) -> None:
        source_root = Path(
            "history/mysql_four_scene_20260809/"
            "v1194_to_c_client_primary_four_scene_20260903_memory_pair_job"
        )
        baseline = json.loads(
            (source_root / "formal_incumbent_baseline/batch_result.json").read_text(
                encoding="utf-8"
            )
        )["measurements"]
        batches = [
            json.loads(
                (source_root / f"outer_001/top5_batch_{index}/batch_result.json")
                .read_text(encoding="utf-8")
            )["measurements"]
            for index in (1, 2, 3)
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.invoke(root / "formal_incumbent_baseline", baseline)
            results = [
                self.invoke(root / f"top5_batch_{index}", measured)
                for index, measured in enumerate(batches, 1)
            ]
        tail = results[-1]
        self.assertEqual(len(tail), 2)
        tpch = [row for row in tail.values() if row["scene"] == "tpch_sf4_memory"]
        job = [row for row in tail.values() if row["scene"] == "job_memory"]
        self.assertEqual(len(tpch), 1)
        self.assertEqual(len(job), 1)
        self.assertTrue(tpch[0]["same_worker_qps_normalization_applied"])
        tail_worker = job[0]["worker_id"]
        self.assertEqual(job[0], batches[-1][tail_worker])


if __name__ == "__main__":
    unittest.main()
