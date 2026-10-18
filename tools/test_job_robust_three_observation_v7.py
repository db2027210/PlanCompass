#!/usr/bin/env python3
"""Offline regression test for JOB three-observation formal acceptance."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
FIRST_SELECTION = Path("/tmp/job_diag003_top5.json")
FIRST_SUMMARY = Path("/tmp/job_diag003_summary.json")
SECOND_SELECTION = Path("/tmp/job_diag004_exact_top3_repeat_top5.json")
BASE_RUNNER = Path(
    "/tmp/job_round8_corrected_top5_v6_20260731/"
    "run_job_round8_strict_serial.py"
)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def same_params(left: dict, right: dict) -> bool:
    return all(float(left[name]) == float(right[name]) for name in left)


def import_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    for path in (
        FIRST_SELECTION,
        FIRST_SUMMARY,
        SECOND_SELECTION,
        BASE_RUNNER,
    ):
        if not path.is_file():
            raise RuntimeError(f"missing test fixture: {path}")

    first_summary = load(FIRST_SUMMARY)
    second_selection = load(SECOND_SELECTION)
    with tempfile.TemporaryDirectory(prefix="job_robust_v7_test_") as raw:
        temporary = Path(raw)
        second_summary_path = temporary / "second_summary.json"
        gate_path = temporary / "gate.json"
        formal_selection_path = temporary / "formal_top5.json"

        second_rows = []
        for rank, candidate in enumerate(second_selection["top5"], 1):
            matches = [
                row
                for row in first_summary["successful"]
                if same_params(
                    candidate["candidate_params"],
                    row["candidate_binding"]["candidate_params"],
                )
            ]
            if len(matches) != 1:
                raise RuntimeError("fixture candidate matching failed")
            row = copy.deepcopy(matches[0])
            row["rank"] = rank
            row["actual_score"] = float(row["actual_score"]) + rank * 0.001
            row["actual_resource"] = {
                name: float(value) * (1.0 + rank * 0.001)
                for name, value in row["actual_resource"].items()
            }
            row["actual_utilization"] = {
                "cpu": row["actual_resource"]["cpu"] / 3.0,
                "io": row["actual_resource"]["io"] / 256.0,
                "memory": row["actual_resource"]["memory"] / 2.91015625,
            }
            second_rows.append(row)
        second_summary = copy.deepcopy(first_summary)
        second_summary["selection_sha256"] = sha256(SECOND_SELECTION)
        second_summary["successful"] = second_rows
        second_summary_path.write_text(
            json.dumps(second_summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        gate_path.write_text(
            json.dumps(
                {
                    "passed": True,
                    "checks": {"offline_fixture_gate": True},
                    "gradient_rank_update_allowed": False,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/build_job_three_observation_formal_selection_v1.py"),
                "--first-selection",
                str(FIRST_SELECTION),
                "--first-summary",
                str(FIRST_SUMMARY),
                "--second-selection",
                str(SECOND_SELECTION),
                "--second-summary",
                str(second_summary_path),
                "--gate",
                str(gate_path),
                "--output",
                str(formal_selection_path),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        os.environ["JOB_ROUND8_BASE_RUNNER"] = str(BASE_RUNNER)
        robust = import_path(
            "job_robust_runner_v7_test",
            ROOT / "tools/run_job_round8_strict_serial_robust_v7.py",
        )
        robust._validate_formal_selection(formal_selection_path)
        formal = load(formal_selection_path)
        candidate = formal["top5"][0]
        prior_scores = robust.validate_policy(candidate)
        if len(prior_scores) != 2:
            raise RuntimeError("policy did not return two observations")

        third_utilization = copy.deepcopy(second_rows[0]["actual_utilization"])
        state = {
            "accepted_count": 2,
            "incumbent_score": min(prior_scores) - 1.0,
            "incumbent_params": copy.deepcopy(candidate["initial_params"]),
            "range_by_parameter": {
                name: {"min": float(value), "max": float(value)}
                for name, value in candidate["initial_params"].items()
            },
            "gradient_observation_count": 0,
        }
        decision = robust.decide_state_update(
            candidate,
            third_utilization,
            state,
            accepted_target=10,
            diagnostic_only=False,
        )
        if decision["accepted"] is not True or state["accepted_count"] != 3:
            raise RuntimeError("robust acceptance transition failed")

        rejected_state = copy.deepcopy(state)
        rejected_state["incumbent_score"] = max(prior_scores) + 10.0
        before = copy.deepcopy(rejected_state)
        rejected = robust.decide_state_update(
            candidate,
            third_utilization,
            rejected_state,
            accepted_target=10,
            diagnostic_only=False,
        )
        if rejected["accepted"] is not False or rejected_state != before:
            raise RuntimeError("robust rejection mutated formal state")

    print("JOB robust three-observation v7 offline regression passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
