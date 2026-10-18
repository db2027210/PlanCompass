#!/usr/bin/env python3
"""Build a checkpoint that binds a paired JOB/TPC-H technical timeout."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v2 as base
from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as predecessor


SCENES = {"job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"}


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def verify_paired_suite_timeout_batch(
    path: Path, expected_rank: int,
) -> dict[str, Any]:
    """Accept only the exact joined JOB/TPC-H 900-second timeout shape."""

    value = base.alg.load(path)
    measurements = _mapping(value.get("measurements"), "timeout measurements")
    failures = [
        _mapping(row, "timeout failure") for row in (value.get("failures") or [])
    ]
    timeout_rows = [
        row for row in failures
        if str(row.get("error") or "").startswith("TimeoutExpired:")
    ]
    skipped = [
        row for row in failures
        if str(row.get("error") or "") == "skipped_after_invalid_workload"
    ]
    if (
        value.get("status") != "FAILED_CLOSED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or set(measurements)
        != {"job_w1", "tpch_w1", "xp_cpu_w1", "xp_io_w1"}
        or {str(row.get("scene")) for row in measurements.values()} != SCENES
        or any(
            int(row.get("rank") or 0) != expected_rank
            for row in measurements.values()
        )
        or len(timeout_rows) != 1
        or str(timeout_rows[0].get("task")) != "pair_1"
        or "timed out after 900 seconds" not in str(timeout_rows[0].get("error") or "")
        or {str(row.get("task")) for row in skipped}
        != {"job_w1_plan_capture", "tpch_w1_plan_capture"}
        or len(failures) != 3
        or measurements["job_w1"].get("coverage_complete") is not False
        or measurements["tpch_w1"].get("coverage_complete") is not False
        or measurements["xp_cpu_w1"].get("coverage_complete") is not True
        or measurements["xp_io_w1"].get("coverage_complete") is not True
        or measurements["job_w1"].get("hard_gate_passed") is not False
        or measurements["tpch_w1"].get("hard_gate_passed") is not False
        or measurements["xp_cpu_w1"].get("hard_gate_passed") is not True
        or measurements["xp_io_w1"].get("hard_gate_passed") is not True
        or not (900 <= float(value.get("elapsed_seconds") or 0) <= 910)
    ):
        raise RuntimeError(f"paired-suite technical timeout batch differs: {path}")
    return value


def build_checkpoint(run_root: Path) -> dict[str, Any]:
    original = base._verify_technical_timeout_batch

    def verify(path: Path, expected_rank: int) -> dict[str, Any]:
        try:
            return original(path, expected_rank)
        except RuntimeError:
            return verify_paired_suite_timeout_batch(path, expected_rank)

    base._verify_technical_timeout_batch = verify
    try:
        document = predecessor.build_checkpoint(run_root)
    finally:
        base._verify_technical_timeout_batch = original

    builder = Path(__file__).resolve()
    document["builder_source_path"] = str(builder)
    document["builder_source_sha256"] = base.alg.sha(builder)
    document["paired_suite_timeout_recovery"] = {
        "technical_timeout_excluded": True,
        "candidate_state_mutation": False,
        "accepted_state_credit_increment": 0,
        "rejected_outer_attempt_increment": 0,
        "job_tpch_full_suite_success_claimed": False,
    }
    payload = copy.deepcopy(document)
    payload.pop("checkpoint_sha256", None)
    document["checkpoint_sha256"] = base._canonical_sha256(payload)
    return document


def verify_checkpoint(path: Path) -> dict[str, Any]:
    return predecessor.verify_checkpoint(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory name")
    if args.output_dir.exists():
        raise RuntimeError(f"fresh checkpoint output exists: {args.output_dir}")
    document = build_checkpoint(args.run_root.resolve())
    args.output_dir.mkdir(parents=True, mode=0o700)
    path = args.output_dir / "prepared_binding.json"
    base.alg.atomic(path, document)
    print(json.dumps({
        "status": "PREPARED",
        "checkpoint": str(path),
        "completed_outer_attempts": document["completed_outer_attempts"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
