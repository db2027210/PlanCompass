#!/usr/bin/env python3
"""Fail-closed repair for a TPC-C incumbent/range invariant violation.

The historical formal runner could accept a candidate outside the active
range.  It then shrank toward a clipped boundary while retaining the original
candidate as incumbent.  This migration preserves the accepted count,
incumbent, counters, and per-parameter interval width; it only relocates each
interval so that the measured incumbent is inside it.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Dict, Mapping, Tuple


LOG_TRANSFORM_PARAMS = {
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_tuple_cost",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def shifted_interval(
    name: str,
    incumbent: float,
    current: Mapping[str, Any],
    global_bounds: Mapping[str, Any],
) -> Tuple[float, float]:
    current_low = float(current["min_value"])
    current_high = float(current["max_value"])
    global_low = float(global_bounds["min_value"])
    global_high = float(global_bounds["max_value"])
    if not (
        global_low < global_high
        and current_low < current_high
        and global_low <= incumbent <= global_high
    ):
        raise RuntimeError(f"invalid range migration input: {name}")

    if name in LOG_TRANSFORM_PARAMS:
        if min(global_low, current_low, incumbent) <= 0.0:
            raise RuntimeError(f"log-space value is not positive: {name}")
        width = math.log(current_high) - math.log(current_low)
        global_minimum = math.log(global_low)
        global_maximum = math.log(global_high)
        if width > global_maximum - global_minimum + 1e-12:
            raise RuntimeError(f"current log interval exceeds global range: {name}")
        low = math.log(incumbent) - width / 2.0
        high = low + width
        if low < global_minimum:
            high += global_minimum - low
            low = global_minimum
        if high > global_maximum:
            low -= high - global_maximum
            high = global_maximum
        return math.exp(low), math.exp(high)

    width = current_high - current_low
    if width > global_high - global_low + 1e-12:
        raise RuntimeError(f"current interval exceeds global range: {name}")
    low = incumbent - width / 2.0
    high = low + width
    if low < global_low:
        high += global_low - low
        low = global_low
    if high > global_high:
        low -= high - global_high
        high = global_high
    return low, high


def build_migration(
    state: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    state_sha256: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if (
        state.get("schema_version") != "tpcc_formal_target10_state_v1"
        or int(state.get("accepted_target", -1)) != 10
        or state.get("qps_used_for_inner_scoring") is not False
    ):
        raise RuntimeError("formal state contract mismatch")
    ranges = state.get("range_state")
    incumbent = state.get("incumbent_params")
    global_ranges = config.get("param_ranges")
    if (
        not isinstance(ranges, dict)
        or not isinstance(incumbent, dict)
        or not isinstance(global_ranges, dict)
        or set(ranges) != set(incumbent)
        or set(ranges) != set(global_ranges)
    ):
        raise RuntimeError("formal/config parameter set mismatch")

    violations = {}
    proposed_ranges = copy.deepcopy(ranges)
    width_audit = {}
    for name, current in ranges.items():
        value = float(incumbent[name])
        current_low = float(current["min_value"])
        current_high = float(current["max_value"])
        if not current_low <= value <= current_high:
            violations[name] = {
                "incumbent": value,
                "min_value": current_low,
                "max_value": current_high,
            }
        new_low, new_high = shifted_interval(
            name, value, current, global_ranges[name]
        )
        proposed_ranges[name]["min_value"] = new_low
        proposed_ranges[name]["max_value"] = new_high
        if not new_low <= value <= new_high:
            raise RuntimeError(f"proposed interval misses incumbent: {name}")
        if name in LOG_TRANSFORM_PARAMS:
            old_width = math.log(current_high / current_low)
            new_width = math.log(new_high / new_low)
            metric = "log_ratio"
        else:
            old_width = current_high - current_low
            new_width = new_high - new_low
            metric = "absolute"
        if not math.isclose(old_width, new_width, rel_tol=1e-10, abs_tol=1e-12):
            raise RuntimeError(f"interval width changed during migration: {name}")
        width_audit[name] = {
            "metric": metric,
            "before": old_width,
            "after": new_width,
        }
    if not violations:
        raise RuntimeError("formal state has no incumbent/range violation")

    migrated = copy.deepcopy(state)
    migrated["range_state"] = proposed_ranges
    migration = {
        "contract": "tpcc_formal_range_invariant_migration_v1",
        "reason": "historical_runner_accepted_candidate_outside_active_range",
        "source_state_sha256": state_sha256,
        "accepted_count_preserved": int(state["accepted_count"]),
        "incumbent_score_preserved": float(state["incumbent_score"]),
        "incumbent_params_preserved": copy.deepcopy(incumbent),
        "formal_physical_call_count_preserved": int(
            state["formal_physical_call_count"]
        ),
        "formal_valid_replay_count_preserved": int(
            state["formal_valid_replay_count"]
        ),
        "formal_technical_failure_count_preserved": int(
            state["formal_technical_failure_count"]
        ),
        "violations_before": violations,
        "range_before": copy.deepcopy(ranges),
        "range_after": copy.deepcopy(proposed_ranges),
        "width_audit": width_audit,
        "workload_invocation_count": 0,
        "accepted_delta": 0,
        "incumbent_delta": 0,
    }
    history = list(migrated.get("range_invariant_migrations") or [])
    history.append(migration)
    migrated["range_invariant_migrations"] = history
    migrated["updated_at_epoch"] = time.time()
    return migrated, migration


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("formal_state", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--audit-output", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    state_path = args.formal_state.resolve()
    config_path = args.config.resolve()
    observed_sha = sha256(state_path)
    if observed_sha != args.expected_sha256:
        raise RuntimeError(
            "formal state SHA mismatch: "
            f"expected={args.expected_sha256}, observed={observed_sha}"
        )
    original = load_json(state_path)
    migrated, migration = build_migration(
        original,
        load_json(config_path),
        state_sha256=observed_sha,
    )
    audit = {
        **migration,
        "formal_state_path": str(state_path),
        "config_path": str(config_path),
        "config_sha256": sha256(config_path),
        "apply_requested": args.apply,
    }
    if not args.apply:
        print(json.dumps(audit, indent=2, sort_keys=True), flush=True)
        return 0

    timestamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
    backup = state_path.with_name(f"{state_path.name}.backup_{timestamp}")
    if backup.exists():
        raise FileExistsError(f"backup already exists: {backup}")
    shutil.copy2(state_path, backup)
    if sha256(backup) != observed_sha or sha256(state_path) != observed_sha:
        raise RuntimeError("backup/source SHA verification failed")
    atomic_json(state_path, migrated)
    final_sha = sha256(state_path)
    if final_sha == observed_sha:
        raise RuntimeError("migration did not change formal state")
    audit.update(
        {
            "backup_path": str(backup),
            "backup_sha256": sha256(backup),
            "final_state_sha256": final_sha,
            "applied_at_epoch": time.time(),
        }
    )
    if args.audit_output is None:
        audit_output = state_path.with_name(
            f"{state_path.name}.range_invariant_migration_{timestamp}.json"
        )
    else:
        audit_output = args.audit_output.resolve()
    if audit_output.exists():
        raise FileExistsError(f"audit output already exists: {audit_output}")
    atomic_json(audit_output, audit)
    print(json.dumps(audit, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
