#!/usr/bin/env python3
"""Create JOB v50 fresh0 state while preserving historical accepted=3 read-only."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping


B = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "job_node02/history/job/outer/attempt_7_cap5_contract_20260726"
)
T = B / "job_final_algorithm_fresh10_v36_20260731T2020"
HISTORICAL_STATE = T / "package/formal_state.json"
HISTORICAL_ACCEPTED_COUNTER = B / "formal_search_accepted_counter.txt"
GLOBAL_COUNTER = B / "replay_counter.txt"
FORMAL_COUNTER = B / "formal_search_replay_counter.txt"
OLD_CYCLE = T / "autonomous_target10_v49/cycle_005"
FRESH_ROOT = T / "autonomous_target10_v50_fresh0"
FRESH_STATE = FRESH_ROOT / "package/formal_state.json"
FRESH_ACCEPTED_COUNTER = FRESH_ROOT / "formal_search_accepted_counter.txt"
BOOTSTRAP_AUDIT = FRESH_ROOT / "fresh0_bootstrap_audit.json"
BASELINE_SCORE = 4.416709205648302
BASELINE_PARAMS = {
    "cpu_index_tuple_cost": 0.0005,
    "cpu_operator_cost": 0.0025,
    "cpu_tuple_cost": 0.01,
    "parallel_setup_cost": 7000.0,
    "parallel_tuple_cost": 0.001,
    "random_page_cost": 4.0,
    "seq_page_cost": 1.0,
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root is not an object: {path}")
    return value


def counter(path: Path) -> int:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw.isdigit():
        raise RuntimeError(f"invalid counter: {path}")
    return int(raw)


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_counter(path: Path, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(f"{value}\n", encoding="utf-8")
    os.replace(temporary, path)


def validate_diagnostic(path: Path, expected: tuple[int, int, int]) -> Dict[str, Any]:
    summary = load_json(path)
    physical = int(summary.get("physical_invocation_count", -1))
    valid = int(summary.get("successful_real_replay_count", -1))
    technical = int(summary.get("technical_failure_count", -1))
    if (
        (physical, valid, technical) != expected
        or summary.get("diagnostic_only") is not True
        or summary.get("baseline_params_restored") is not True
        or (summary.get("baseline_restore") or {}).get("success") is not True
        or summary.get("initial_state") != summary.get("final_state")
        or summary.get("formal_update_flags")
        != {"accepted": False, "gradient": False, "incumbent": False, "range": False}
    ):
        raise RuntimeError(f"historical diagnostic handoff mismatch: {path}")
    return summary


def main() -> int:
    historical = load_json(HISTORICAL_STATE)
    historical_sha = sha256_file(HISTORICAL_STATE)
    if (
        int(historical.get("accepted_count", -1)) != 3
        or counter(HISTORICAL_ACCEPTED_COUNTER) != 3
    ):
        raise RuntimeError("historical accepted=3 authority mismatch")
    replay01_path = OLD_CYCLE / "discovery_replay_01/replay/summary.json"
    replay02_path = OLD_CYCLE / "discovery_replay_02/replay/summary.json"
    replay01 = validate_diagnostic(replay01_path, (5, 4, 1))
    replay02 = validate_diagnostic(replay02_path, (1, 0, 1))
    global_counter = counter(GLOBAL_COUNTER)
    formal_counter = counter(FORMAL_COUNTER)
    historical_global = int(historical.get("global_ordinal", -1))
    historical_formal = int(historical.get("formal_replay_count", -1))
    if (
        global_counter - historical_global != 6
        or formal_counter != historical_formal
    ):
        raise RuntimeError("historical diagnostic physical counter chain mismatch")

    fresh = dict(historical)
    fresh.update(
        {
            "campaign_contract": "job_v50_fresh0_historical3_excluded_v1",
            "accepted_count": 0,
            "accepted_target": 10,
            "incumbent_score": BASELINE_SCORE,
            "incumbent_params": dict(BASELINE_PARAMS),
            "range_by_parameter": {
                name: {"min": value, "max": value}
                for name, value in BASELINE_PARAMS.items()
            },
            "global_ordinal": global_counter,
            "formal_replay_count": formal_counter,
            "formal_valid_replay_count": 0,
            "gradient_observation_count": 0,
            "historical_accepted_excluded": 3,
            "historical_state_sha256": historical_sha,
            "historical_state_used_for_training_only": True,
            "historical_incumbent_or_range_inherited": False,
            "countable_formal_accepted_origin": 0,
            "snapshot_only": False,
        }
    )
    fresh.pop("last_formal_batch", None)
    bootstrap = {
        "schema_version": "job_v50_fresh0_bootstrap_audit_v1",
        "status": "complete",
        "campaign_contract": fresh["campaign_contract"],
        "historical_state_path": str(HISTORICAL_STATE),
        "historical_state_sha256": historical_sha,
        "historical_accepted_excluded": 3,
        "fresh_accepted_count": 0,
        "fresh_incumbent_score": BASELINE_SCORE,
        "fresh_incumbent_params": dict(BASELINE_PARAMS),
        "fresh_range_is_baseline_point": True,
        "historical_incumbent_or_range_inherited": False,
        "fresh_accepted_counter_path": str(FRESH_ACCEPTED_COUNTER),
        "global_ordinal": global_counter,
        "formal_replay_count": formal_counter,
        "replay01_summary_sha256": sha256_file(replay01_path),
        "replay02_summary_sha256": sha256_file(replay02_path),
        "sealed_historical_diagnostic_physical_calls": (
            int(replay01["physical_invocation_count"])
            + int(replay02["physical_invocation_count"])
        ),
        "old_state_or_accepted_counter_modified": False,
        "workload_calls": 0,
        "explain_calls": 0,
    }
    if FRESH_STATE.exists():
        if load_json(FRESH_STATE) != fresh:
            raise RuntimeError("existing fresh0 state differs from deterministic bootstrap")
    else:
        atomic_json(FRESH_STATE, fresh)
    if FRESH_ACCEPTED_COUNTER.exists():
        if counter(FRESH_ACCEPTED_COUNTER) != 0:
            raise RuntimeError("existing fresh0 accepted counter is not zero")
    else:
        atomic_counter(FRESH_ACCEPTED_COUNTER, 0)
    bootstrap["fresh_state_sha256"] = sha256_file(FRESH_STATE)
    if BOOTSTRAP_AUDIT.exists():
        if load_json(BOOTSTRAP_AUDIT) != bootstrap:
            raise RuntimeError("existing fresh0 bootstrap audit mismatch")
    else:
        atomic_json(BOOTSTRAP_AUDIT, bootstrap)
    if (
        sha256_file(HISTORICAL_STATE) != historical_sha
        or counter(HISTORICAL_ACCEPTED_COUNTER) != 3
    ):
        raise RuntimeError("historical authority changed during fresh0 bootstrap")
    print(json.dumps(bootstrap, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
