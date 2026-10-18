#!/usr/bin/env python3
"""Account the XP cont4 fallback workload that bypassed replay reservation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


ATTEMPT = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726"
)
ROOT = ATTEMPT / "formal10_cont4_accepted4_cap5_plan_basin_v1_20260729T1810"
HISTORY = ROOT / "optimizer_history_2026-07-29_181229.jsonl"
SUMMARY = (
    ATTEMPT
    / "candidate_measurements/20260729T181437+0800_3186132/summary.json"
)
EXPECTED_SHA = {
    ROOT / "config.json":
        "4942ea23bbc00b67e032afc114365d2b8506fb51c55ed0ea10f290721c6def87",
    HISTORY:
        "800b84f0e4c74c20e796f87a28d62a9261bb631d22834a46c0ae8cca88a680b1",
    SUMMARY:
        "a8b93f238f4c4980aa3b5cdfcbb3a308621d887186f82aed55197c7934830453",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    audit_path = ROOT / "unreserved_fallback_call_accounting_audit.json"
    if audit_path.exists():
        raise FileExistsError(f"audit already exists: {audit_path}")
    for path, expected in EXPECTED_SHA.items():
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"SHA mismatch: {path}: {actual} != {expected}")
    counter_paths = {
        "accepted": ROOT / "state/formal_search_accepted_counter.txt",
        "formal_replay": ROOT / "state/formal_search_replay_counter.txt",
        "global": ROOT / "state/global_replay_counter.txt",
    }
    before = {
        name: int(path.read_text(encoding="utf-8").strip())
        for name, path in counter_paths.items()
    }
    if before != {"accepted": 4, "formal_replay": 1, "global": 1}:
        raise RuntimeError(f"counter mismatch: {before}")
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    if (
        summary.get("correctness_passed") is not True
        or summary.get("stable") is not True
        or summary.get("technical_failure") is not False
    ):
        raise RuntimeError("unreserved workload is not valid stable evidence")
    events = [
        json.loads(line)
        for line in HISTORY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    stable_events = [
        item
        for item in events
        if item.get("event") == "final_top_sql"
        and (
            (
                (
                    (
                        item.get("final_validation") or {}
                    ).get("resource_stats") or {}
                ).get("candidate_measurement_protocol") or {}
            ).get("summary")
            == str(SUMMARY)
        )
    ]
    rejected = [
        item
        for item in events
        if item.get("event") == "final_feedback_initial_validation_rejected"
    ]
    if len(stable_events) != 1 or len(rejected) != 1:
        raise RuntimeError("history does not prove one rejected fallback workload")
    if any(
        item.get("event") in {
            "formal_search_candidate_accepted",
            "experience_table_gradient_candidate_accepted",
        }
        for item in events
    ):
        raise RuntimeError("cont4 unexpectedly contains an acceptance event")
    audit: dict[str, Any] = {
        "schema_version": "xp_unreserved_fallback_accounting_v1",
        "event": "xp_cont4_unreserved_fallback_call_accounted",
        "classification": "rejected_valid_stable_unreserved_fallback",
        "root": str(ROOT),
        "history_path": str(HISTORY),
        "history_sha256": EXPECTED_SHA[HISTORY],
        "summary_path": str(SUMMARY),
        "summary_sha256": EXPECTED_SHA[SUMMARY],
        "physical_calls_accounted": 1,
        "valid_replays_accounted": 1,
        "accepted": False,
        "accepted_update_applied": False,
        "incumbent_update_applied": False,
        "range_update_applied": False,
        "gradient_update_applied": False,
        "counters_before": before,
        "counters_after": {
            "accepted": 4,
            "formal_replay": 2,
            "global": 2,
        },
        "baseline_readback": {
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.0025,
            "cpu_tuple_cost": 0.01,
            "parallel_setup_cost": 1000.0,
            "parallel_tuple_cost": 0.1,
            "random_page_cost": 4.0,
            "seq_page_cost": 0.2,
        },
        "fix_optimizer_sha256":
            "04bd0be7bdc1cd8d91b86b110a0e39994e90d5e4dd7f21a87542fc121b90b293",
    }
    atomic_text(
        audit_path,
        json.dumps(audit, indent=2, sort_keys=True) + "\n",
    )
    atomic_text(counter_paths["formal_replay"], "2\n")
    atomic_text(counter_paths["global"], "2\n")
    after = {
        name: int(path.read_text(encoding="utf-8").strip())
        for name, path in counter_paths.items()
    }
    if after != audit["counters_after"]:
        raise RuntimeError(f"counter write verification failed: {after}")
    print(json.dumps(audit, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
