#!/usr/bin/env python3
"""Retry only the invalid XP/IO Algorithm-A baseline replica w5.

The predecessor completed all eight baseline measurements.  Seven replicas
are technically valid; w5 alone reached the 900 second bound without passing
the frozen target-I/O stability rule.  This successor retains the seven valid
physical measurements, retries w5 with the same default parameter vector and
resource envelope, and starts round 1 only after the combined eight-row
baseline passes the unchanged hard gate.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v12 as runtime
from scripts import mysql_async_xp_post_reset_settle_v2 as disk2_settle
from scripts import mysql_single_scene_measurement_v1 as single_measurement


CONFIG = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_xp_io_baseline_w5_retry_20260827_v1"
)
CONTRACT = CONFIG / "retry_contract.json"
SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v840_to_a_io_control2_fresh_20260827_xp_io"
)
SOURCE_STATE = SOURCE / "algorithm_a_state.json"
SOURCE_BATCH = SOURCE / "formal_baseline/batch_result.json"
AUTHORITY = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v849_to_a_io_baseline_w5_retry_current_rebind_20260827"
    / "authority/live_authority.json"
)
FAILED_WORKER = "xp_io_w5"
VALID_WORKERS = [
    "xp_io_w1", "xp_io_w2", "xp_io_w3", "xp_io_w4",
    "xp_io_w6", "xp_io_w7", "xp_io_w8",
]


def load(path: Path) -> dict[str, Any]:
    return runtime.base.alg.load(path)


def validate() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    contract = load(CONTRACT)
    state = load(SOURCE_STATE)
    batch = load(SOURCE_BATCH)
    authority = load(AUTHORITY)
    rows = dict(batch.get("measurements") or {})
    expected = {f"xp_io_w{i}" for i in range(1, 9)}
    failed = rows.get(FAILED_WORKER) or {}
    failed_checks = failed.get("hard_gate_checks") or {}
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_xp_io_baseline_w5_retry_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("failed_worker") != FAILED_WORKER
        or contract.get("retained_valid_workers") != VALID_WORKERS
        or int(contract.get("maximum_physical_retry_attempts") or 0) != 3
        or contract.get("accepted_or_rejected_round_credit") != 0
        or contract.get("candidate_set_changed") is not False
        or contract.get("workload_or_resource_envelope_changed") is not False
        or contract.get("stability_threshold_changed") is not False
        or contract.get("combined_baseline_requires_all_eight_valid") is not True
        or state.get("status") != "FAILED_CLOSED"
        or state.get("error")
        != "RuntimeError: Algorithm A baseline physical hard gate failed"
        or int(state.get("accepted") or 0) != 0
        or int(state.get("rejected_rounds") or 0) != 0
        or int(state.get("round_index") or 0) != 0
        or batch.get("status") != "FAILED_CLOSED"
        or set(rows) != expected
        or any(
            not runtime.base.pg_outer.physical_valid(rows[worker_id])
            for worker_id in VALID_WORKERS
        )
        or runtime.base.pg_outer.physical_valid(failed)
        or failed.get("coverage_complete") is not True
        or failed_checks.get("target_resource_stability_passed") is not False
        or any(
            value is not True
            for key, value in failed_checks.items()
            if key != "target_resource_stability_passed"
        )
        or authority.get("status") != "PASS"
        or authority.get("scene") != "hybench_xp_io"
        or int(authority.get("physical_replay_parallelism") or 0) != 8
    ):
        raise RuntimeError("XP/IO baseline-w5 retry predecessor differs")
    for label, path in {
        "controller": Path(__file__).resolve(),
        "runtime_v12": Path(runtime.__file__).resolve(),
        "single_device_settle": Path(disk2_settle.__file__).resolve(),
        "single_scene_measurement": Path(single_measurement.__file__).resolve(),
        "source_state": SOURCE_STATE,
        "source_batch": SOURCE_BATCH,
        "launch_authority": AUTHORITY,
    }.items():
        binding = (contract.get("source_bindings") or {}).get(label) or {}
        if (
            binding.get("path") != str(path.relative_to(ROOT))
            or binding.get("sha256") != runtime.base.alg.sha(path)
        ):
            raise RuntimeError(f"XP/IO baseline-w5 retry binding differs: {label}")
    return contract, rows


def main() -> int:
    contract, source_rows = validate()
    base = runtime.base
    physical_measure: dict[str, Any] = {}
    retry_invoked = False

    def measure_with_single_retry(**kwargs: Any):
        nonlocal retry_invoked
        output = Path(kwargs["output"])
        if output.name != "formal_baseline" or retry_invoked:
            return physical_measure["callable"](**kwargs)
        retry_invoked = True
        assignments = dict(kwargs["assignments"])
        workers = dict(kwargs["workers"])
        if (
            set(assignments) != {f"xp_io_w{i}" for i in range(1, 9)}
            or set(workers) != set(assignments)
            or assignments[FAILED_WORKER].get("params") != source_rows[FAILED_WORKER].get("params")
        ):
            raise RuntimeError("XP/IO baseline retry assignment differs")

        attempts: list[dict[str, Any]] = []
        replacement: dict[str, Any] | None = None
        state = kwargs["state"]
        for attempt in range(1, int(contract["maximum_physical_retry_attempts"]) + 1):
            retry_output = output.parent / f"formal_baseline_w5_retry_attempt_{attempt:02d}"
            original_settle = single_measurement.async_xp_settle
            single_measurement.async_xp_settle = disk2_settle
            try:
                measured, state = physical_measure["callable"](
                    **{
                        **kwargs,
                        "workers": {FAILED_WORKER: workers[FAILED_WORKER]},
                        "assignments": {FAILED_WORKER: assignments[FAILED_WORKER]},
                        "output": retry_output,
                        "reset_token": f"{kwargs['run_id']}_formal_baseline_w5_retry_{attempt:02d}",
                        "state": state,
                        "stage_prefix": f"FORMAL_BASELINE_W5_RETRY_{attempt:02d}",
                    }
                )
            finally:
                single_measurement.async_xp_settle = original_settle
            row = copy.deepcopy(dict(measured.get(FAILED_WORKER) or {}))
            valid = base.pg_outer.physical_valid(row)
            attempts.append({
                "attempt": attempt,
                "output": str(retry_output),
                "batch_result": str(retry_output / "batch_result.json"),
                "batch_result_sha256": base.alg.sha(retry_output / "batch_result.json"),
                "hard_gate_passed": row.get("hard_gate_passed"),
                "coverage_complete": row.get("coverage_complete"),
                "target_resource_stability_passed": (
                    (row.get("hard_gate_checks") or {}).get(
                        "target_resource_stability_passed"
                    )
                ),
                "physical_valid": valid,
            })
            if valid:
                replacement = row
                break
        if replacement is None:
            raise RuntimeError("XP/IO baseline w5 remained invalid after 3 retries")

        combined = {
            worker_id: copy.deepcopy(source_rows[worker_id])
            for worker_id in VALID_WORKERS
        }
        combined[FAILED_WORKER] = replacement
        combined = {worker_id: combined[worker_id] for worker_id in sorted(combined)}
        # Enforce the original eight-replica hard gate before making the
        # retained/retried cohort visible to the outer algorithm.
        base.aggregate_baseline(combined)

        output.mkdir(parents=True, exist_ok=False, mode=0o700)
        source_document = load(SOURCE_BATCH)
        combined_document = copy.deepcopy(source_document)
        combined_document.update({
            "status": "PASS",
            "measurements": combined,
            "technical_baseline_retry": True,
            "retained_valid_workers": VALID_WORKERS,
            "retried_worker": FAILED_WORKER,
            "retry_attempts": attempts,
            "source_batch_path": str(SOURCE_BATCH),
            "source_batch_sha256": base.alg.sha(SOURCE_BATCH),
            "accepted_or_rejected_round_credit": 0,
            "physical_workload_invocations": len(attempts),
        })
        base.alg.atomic(output / "batch_result.json", combined_document)
        base.alg.atomic(output / "technical_baseline_retry_manifest.json", {
            "schema_version": "mysql_algorithm_a_xp_io_baseline_w5_retry_manifest_v1",
            "status": "PASS",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "retained_valid_workers": VALID_WORKERS,
            "retried_worker": FAILED_WORKER,
            "retry_attempts": attempts,
            "combined_worker_count": len(combined),
            "combined_baseline_all_physical_valid": True,
            "source_batch_path": str(SOURCE_BATCH),
            "source_batch_sha256": base.alg.sha(SOURCE_BATCH),
            "contract_path": str(CONTRACT),
            "contract_sha256": base.alg.sha(CONTRACT),
            "accepted_or_rejected_round_credit": 0,
            "candidate_set_changed": False,
            "workload_or_resource_envelope_changed": False,
            "stability_threshold_changed": False,
        })
        return combined, state

    # The Top4 runtime installs its no-lease measurement adapter immediately
    # before invoking base.run.  Intercept base.run so the single-worker hook
    # is installed *after* that runtime binding, not overwritten by it.
    original_run = base.run

    def run_with_single_retry(args: Any) -> int:
        original_measure = base.measure
        physical_measure["callable"] = original_measure
        base.measure = measure_with_single_retry
        try:
            return int(original_run(args))
        finally:
            base.measure = original_measure

    base.run = run_with_single_retry
    try:
        result = int(runtime.main())
    finally:
        base.run = original_run
    if not retry_invoked:
        raise RuntimeError("XP/IO baseline w5 retry hook was not invoked")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
