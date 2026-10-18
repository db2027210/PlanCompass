#!/usr/bin/env python3
"""Offline runtime self-test for the TPC-H round29 diagnostic runner."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, Mapping

import run_tpch_round29_diagnostic_exact_plan_regime_batch as runner


PACKAGE = Path("/tmp/tpch_round29_diagnostic_replay_package")


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_self_test() -> Dict[str, Any]:
    required = ("top5.json", "resume_state.json", "config.json", "gate.json")
    if any(not (PACKAGE / name).is_file() for name in required):
        raise RuntimeError("diagnostic replay package fixtures are missing")

    with tempfile.TemporaryDirectory(prefix="tpch_round29_runner_test_") as name:
        root = Path(name)
        selection = root / "top5.json"
        resume = root / "resume.json"
        config = root / "config.json"
        gate = root / "gate.json"
        baseline = root / "baseline.json"
        workload = root / "workload.sh"
        counter = root / "accepted.counter"
        output = root / "output"
        for source_name, destination in (
            ("top5.json", selection),
            ("resume_state.json", resume),
            ("config.json", config),
            ("gate.json", gate),
        ):
            destination.write_bytes((PACKAGE / source_name).read_bytes())
        write_json(
            baseline,
            {
                "status": "complete",
                "failed_queries": 0,
                "r0_decision_window": {
                    "available": True,
                    "stable": True,
                    "utilization_pct": {
                        "cpu": 40.0,
                        "io": 40.0,
                        "memory": 87.0,
                    },
                },
                "r0_profile_construction_gate": {"passed": True},
                "r0_resource_gate": {"passed": True},
            },
        )
        workload.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
        counter.write_text("1\n", encoding="utf-8")

        apply_order = []
        workload_order = []

        def fake_apply(
            _container: str,
            _database: str,
            params: Mapping[str, Any],
        ) -> str:
            apply_order.append(copy.deepcopy(dict(params)))
            return "offline-test-verification"

        payload = {
            "status": "complete",
            "failed_queries": 0,
            "successful_queries": 22,
            "worker_cleanup": {
                "controller_thread_stopped": True,
                "database_backends_fully_released": True,
                "remaining_benchmark_backend_count": 0,
            },
            "candidate_measurement_protocol": {"stable": True},
            "formal_pg_resource": {
                "semantics": "pg5_io_total_throughput_utilization_v1",
                "caps": {
                    "cpu_quota_cores": 22.0,
                    "memory_bytes": 1_730_000_000,
                    "io_total_cap_bps": 36.0 * 1024.0 ** 2,
                },
                "aggregate": {
                    "cpu_utilization": 0.4,
                    "io_utilization": 0.4,
                    "memory_utilization": 0.87,
                },
            },
        }

        def fake_workload(
            _workload: Path,
            point_root: Path,
            _timeout_seconds: int,
        ) -> tuple[int, str]:
            workload_order.append(point_root.parent.name)
            return 0, runner.MARKER + json.dumps(payload)

        runner.apply_params = fake_apply
        runner.run_workload = fake_workload
        original_argv = sys.argv
        successful_argv = [
            "runner",
            "--diagnostic-only",
            "--selection",
            str(selection),
            "--selection-sha256",
            runner.file_sha256(selection),
            "--gate-summary",
            str(gate),
            "--gate-summary-sha256",
            runner.file_sha256(gate),
            "--baseline-summary",
            str(baseline),
            "--baseline-summary-sha256",
            runner.file_sha256(baseline),
            "--config",
            str(config),
            "--config-sha256",
            runner.file_sha256(config),
            "--workload",
            str(workload),
            "--workload-sha256",
            runner.file_sha256(workload),
            "--accepted-counter",
            str(counter),
            "--resume-state",
            str(resume),
            "--resume-state-sha256",
            runner.file_sha256(resume),
            "--selection-start-rank",
            "1",
            "--maximum-real-replay-invocations",
            "5",
            "--output",
            str(output),
            "--container",
            "offline-test",
            "--database",
            "offline-test",
        ]
        sys.argv = successful_argv
        try:
            runner.main()
        finally:
            sys.argv = original_argv

        state = json.loads(
            (output / "diagnostic_state.json").read_text(encoding="utf-8")
        )
        summary = json.loads(
            (output / "diagnostic_summary.json").read_text(encoding="utf-8")
        )
        resume_state = json.loads(resume.read_text(encoding="utf-8"))
        for field in (
            "accepted_count",
            "incumbent_params",
            "incumbent_resource",
            "incumbent_score",
            "range_state",
        ):
            if state[field] != resume_state[field]:
                raise RuntimeError(f"diagnostic runner mutated {field}")
        if counter.read_text(encoding="utf-8") != "1\n":
            raise RuntimeError("diagnostic runner mutated accepted counter")
        if (
            state["physical_call_count"] != 5
            or state["valid_replay_count"] != 5
            or state["technical_failure_count"] != 0
            or state["batch_added_accepted_count"] != 0
            or summary["accepted_update_applied"] is not False
            or summary["incumbent_update_applied"] is not False
            or summary["range_update_applied"] is not False
            or summary["gradient_update_applied"] is not False
        ):
            raise RuntimeError("diagnostic counters or update flags are invalid")
        if workload_order != [
            "rank_01",
            "rank_02",
            "rank_03",
            "rank_04",
            "rank_05",
        ]:
            raise RuntimeError(f"replay order is not strictly serial: {workload_order}")
        if len(apply_order) != 6:
            raise RuntimeError("expected five candidates plus baseline restore")

        failure_output = root / "failure_output"
        failure_order = []

        def one_failure_workload(
            _workload: Path,
            point_root: Path,
            _timeout_seconds: int,
        ) -> tuple[int, str]:
            failure_order.append(point_root.parent.name)
            if point_root.parent.name == "rank_03":
                return 71, "injected technical failure"
            return 0, runner.MARKER + json.dumps(payload)

        runner.run_workload = one_failure_workload
        failure_argv = [
            str(failure_output) if value == str(output) else value
            for value in successful_argv
        ]
        sys.argv = failure_argv
        try:
            runner.main()
        finally:
            sys.argv = original_argv
        failure_state = json.loads(
            (failure_output / "diagnostic_state.json").read_text(
                encoding="utf-8"
            )
        )
        if (
            failure_state["physical_call_count"] != 3
            or failure_state["valid_replay_count"] != 2
            or failure_state["technical_failure_count"] != 1
            or failure_state["status"] != "technical_failure_abort"
            or failure_state["accepted_count"] != resume_state["accepted_count"]
            or counter.read_text(encoding="utf-8") != "1\n"
            or failure_order
            != ["rank_01", "rank_02", "rank_03"]
        ):
            raise RuntimeError("technical failure accounting contract is invalid")
        return {
            "status": "passed",
            "physical_call_count": state["physical_call_count"],
            "valid_replay_count": state["valid_replay_count"],
            "technical_failure_count": state["technical_failure_count"],
            "accepted_counter_after": 1,
            "formal_state_fields_mutated": 0,
            "replay_order": workload_order,
            "baseline_restore_calls": 1,
            "injected_failure_physical_call_count": failure_state[
                "physical_call_count"
            ],
            "injected_failure_valid_replay_count": failure_state[
                "valid_replay_count"
            ],
            "injected_failure_technical_failure_count": failure_state[
                "technical_failure_count"
            ],
        }


def main() -> int:
    print(json.dumps(run_self_test(), indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
