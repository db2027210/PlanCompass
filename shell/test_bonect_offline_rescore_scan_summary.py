#!/usr/bin/env python3
"""Verify bonect_offline_rescore_scan emits a precomputed-import-compatible
scan_summary.json (status=completed, total/completed, failure_count=0) AND that
its rescored_candidates.jsonl carries estimated_db_qps_delta_pct /
estimated_db_qps_error_bound_pct that the parameter_optimizer precomputed
importer now reads into surrogate_prediction.

The unsafe no-baseline KNN-5 rescore omitted the real default baseline label and
predicted the default point as a large improvement; this test guards against
that regression by asserting the default candidate rescored with the anchored
forest has QPS delta ~0.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


FOREST100 = (
    ROOT
    / "config/generated/bonect_phase2_vm_memory_blind_v112/memory_v112_top18_plus_baseline_replay_delta_forest100.json"
)
LABELS = (
    ROOT
    / "history/bonect_parallel_vm_replay/memory_v112_top18_plus_baseline_labels_20260712.jsonl"
)
CONFIG = (
    ROOT
    / "config/generated/bonect_phase2_vm_memory_blind_v112/memory_blind_v112_vm.json"
)

PG_DEFAULT = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


def load_jsonl(path):
    rows = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def find_row(rows, candidate_id):
    for row in rows:
        if str(row.get("candidate_id") or "") == candidate_id:
            return row
    raise KeyError(candidate_id)


def main() -> int:
    assert FOREST100.is_file(), FOREST100
    assert CONFIG.is_file(), CONFIG
    rows = load_jsonl(LABELS)
    baseline_row = find_row(rows, "__baseline_postgresql_defaults__")
    baseline_pf = baseline_row.get("plan_delta_features") or {}
    c4_row = find_row(rows, "candidate_0004")
    c4_pf = c4_row.get("plan_delta_features") or {}

    with tempfile.TemporaryDirectory(prefix="bonect_offline_rescore_") as tmp:
        tmpdir = Path(tmp)
        candidates = tmpdir / "candidates.jsonl"
        # Minimal candidate set: the mandatory default baseline anchor + a
        # known strong point +# a fallback small change so rescore has >=3 rows.
        strong = dict(PG_DEFAULT)
        strong.update(
            {
                "seq_page_cost": 0.0001,
                "cpu_tuple_cost": 0.000001,
                "cpu_index_tuple_cost": 0.000001,
                "parallel_setup_cost": 10000.0,
                "parallel_tuple_cost": 0.000001,
            }
        )
        rows_out = [
            {
                "candidate_index": 19531,
                "params": PG_DEFAULT,
                "estimated_resource": {"cpu": 6.18, "memory": 1.62, "io": 587.0},
                "plan_delta_features": baseline_pf,
                "plan_hash": "baseline_hash",
                "query_count": 23,
                "failed_query_count": 0,
            },
            {
                "candidate_index": 4,
                "params": strong,
                "estimated_resource": {"cpu": 9.96, "memory": 1.28, "io": 250.0},
                "plan_delta_features": c4_pf,
                "plan_hash": "strong_hash",
                "query_count": 23,
                "failed_query_count": 0,
            },
            {
                "candidate_index": 5,
                "params": {**PG_DEFAULT, "random_page_cost": 5.0},
                "estimated_resource": {"cpu": 6.50, "memory": 1.60, "io": 580.0},
                "plan_delta_features": baseline_pf,
                "plan_hash": "small_change_hash",
                "query_count": 23,
                "failed_query_count": 0,
            },
        ]
        candidates.write_text(
            "".join(json.dumps(r) + "\n" for r in rows_out), encoding="utf-8"
        )
        # baseline_calibration referencing forest100 via model_path resolved by
        # load_baseline_calibration at (path.parent, ROOT); forest100 is under
        # ROOT/config/generated/... so resolve via ROOT works.
        calibration = {
            "source": "test_offline_rescore",
            "actual_resource": {"cpu": 6.18, "memory": 1.62, "io": 587.0},
            "actual_utilization_pct": {"cpu": 56.2, "memory": 81.1, "io": 36.7},
            "scales": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
            "exempt_resources": ["memory"],
            "replay_delta_tree": {
                "enabled": True,
                "required": True,
                "source": "test",
                "model_path": FOREST100.name,
                "teacher_coordinates_loaded_by_scanner": False,
            },
            "expected_initial_params": PG_DEFAULT,
        }
        calibration_path = tmpdir / "baseline_calibration.json"
        # load_baseline_calibration resolves model_path relative to path.parent
        # first, then ROOT. We must put the model file next to the calibration
        # OR rely on ROOT resolve. Put a copy of the forest next to the cal so
        # the path.parent lookup succeeds.
        calibration_path.write_text(json.dumps(calibration, indent=2, sort_keys=True), encoding="utf-8")
        # Copy the forest to the temp dir so model_path resolves via path.parent.
        (tmpdir / FOREST100.name).write_text(FOREST100.read_text(), encoding="utf-8")
        # Append a baseline label jsonl with the mandatory baseline row only
        # (replay-labels are optional; we pass an empty path to keep it simple).
        out_dir = tmpdir / "out"
        cmd = [
            "/home/anonymous/miniconda3/envs/resource_adaptive/bin/python",
            "-u",
            str(ROOT / "shell/bonect_offline_rescore_scan.py"),
            "--candidates-jsonl",
            str(candidates),
            "--config",
            str(CONFIG),
            "--baseline-calibration-json",
            str(calibration_path),
            "--output-dir",
            str(out_dir),
            "--scenario",
            "memory",
            "--top-k",
            "3",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        # scan_summary.json must exist with completed status + 3 candidates.
        scan_summary = out_dir / "scan_summary.json"
        assert scan_summary.is_file(), f"scan_summary.json missing: {scan_summary}"
        scan = json.loads(scan_summary.read_text())
        assert scan["status"] == "completed", scan
        assert scan["total_candidates"] == 3, scan
        assert scan["completed_candidates"] == 3, scan
        assert scan["successful_count"] == 3, scan
        assert scan["failure_count"] == 0, scan
        # rescored_candidates.jsonl rows must carry estimated_db_qps_delta_pct
        # and the default candidate must predict ~0 QPS delta.
        rc = out_dir / "rescored_candidates.jsonl"
        assert rc.is_file(), rc
        rc_rows = [json.loads(line) for line in rc.read_text().splitlines() if line.strip()]
        assert len(rc_rows) == 3, rc_rows
        for r in rc_rows:
            assert "estimated_db_qps_delta_pct" in r, r
            assert "estimated_db_qps_error_bound_pct" in r, r
            assert "plan_delta_features" in r, r
        default_rc = next(
            r for r in rc_rows if r["candidate_index"] == 19531
        )
        qps = float(default_rc["estimated_db_qps_delta_pct"])
        print(f"rescore default candidate estimated QPS delta = {qps:+.4f}", flush=True)
        assert abs(qps) < 5.0, default_rc
        strong_rc = next(
            r for r in rc_rows if r["candidate_index"] == 4
        )
        sqps = float(strong_rc["estimated_db_qps_delta_pct"])
        print(f"rescore strong candidate estimated QPS delta = {sqps:+.4f}", flush=True)
        assert sqps > 50.0, strong_rc
    print("bonect offline rescore scan_summary test passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())