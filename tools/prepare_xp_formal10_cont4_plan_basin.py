#!/usr/bin/env python3
"""Prepare XP accepted=4 continuation with deterministic plan-basin escape."""

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
SOURCE = ATTEMPT / "formal10_cont3_accepted4_cap5_zero_warmup_v1_20260729T1758"
OUTPUT = ATTEMPT / "formal10_cont4_accepted4_cap5_plan_basin_v1_20260729T1810"
EXPECTED_CONFIG_SHA = (
    "d7c8dfdab170cefd30e40b028e1ea4623cf20f2e510e952f131c00678bce41d7"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    config_path = SOURCE / "config.json"
    actual_sha = sha256(config_path)
    if actual_sha != EXPECTED_CONFIG_SHA:
        raise RuntimeError(
            f"source config SHA mismatch: {actual_sha} != {EXPECTED_CONFIG_SHA}"
        )
    expected_counters = {
        "formal_search_accepted_counter.txt": 4,
        "formal_search_replay_counter.txt": 1,
        "formal_startup_baseline_counter.txt": 0,
        "global_replay_counter.txt": 1,
    }
    for name, expected in expected_counters.items():
        actual = int((SOURCE / "state" / name).read_text(encoding="utf-8"))
        if actual != expected:
            raise RuntimeError(f"counter mismatch: {name}: {actual} != {expected}")
    histories = sorted(SOURCE.glob("optimizer_history_*.jsonl"))
    if len(histories) != 1:
        raise RuntimeError("cont3 history cardinality mismatch")
    events = [
        json.loads(line)
        for line in histories[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if any(
        event.get("event") in {
            "formal_candidate_replay_started",
            "formal_candidate_replay_complete",
        }
        for event in events
    ):
        raise RuntimeError("cont3 unexpectedly consumed a candidate replay")
    completion = [
        event for event in events
        if event.get("event") == "experience_table_all_subset_ranking"
    ]
    if not completion:
        raise RuntimeError("cont3 lacks candidate-generation evidence")
    final = events[-2] if events[-1].get("event") == "optimizer_process_exit" else events[-1]
    gradient = final.get("gradient_common_outer") or {}
    if gradient.get("stop_reason") != "candidate_space_exhausted_before_accepted_shrink_target":
        raise RuntimeError("cont3 did not stop on the expected plan plateau")

    config = load_json(config_path)
    state_dir = OUTPUT / "state"
    protocol = config["formal_outer_protocol"]
    paths = {
        "formal_search_accepted_counter_path":
            state_dir / "formal_search_accepted_counter.txt",
        "formal_search_replay_counter_path":
            state_dir / "formal_search_replay_counter.txt",
        "formal_startup_baseline_counter_path":
            state_dir / "formal_startup_baseline_counter.txt",
        "global_replay_counter_path":
            state_dir / "global_replay_counter.txt",
    }
    for key, path in paths.items():
        protocol[key] = str(path)

    basin = config["optimizer"]["gradient"]["plan_basin_escape"]
    basin.update({
        "enabled": True,
        "min_distinct_plans": 5,
        "radius_multiplier": 2,
        "stratified_probe_count": 32,
        "boundary_fraction": 0.2,
        "seed": 4508334,
        "state_path": str(state_dir / "plan_basin_escape_state.json"),
    })
    config["optimizer"]["gradient"]["plateau_exploration_enabled"] = True
    config["_original_gradient_debug"]["plan_basin_escape_enabled"] = True
    config["optimizer"]["gradient"]["subset_log_dir"] = str(OUTPUT / "subset_logs")
    config["logging"]["history_path"] = str(OUTPUT / "optimizer_history.jsonl")
    config["output"]["directory"] = str(OUTPUT)
    config["_xp_formal_continuation"] = {
        "contract": "xp_formal10_accepted4_plan_basin_continuation_v1",
        "source_continuation": str(SOURCE),
        "source_config_sha256": EXPECTED_CONFIG_SHA,
        "accepted_start": 4,
        "formal_replay_start": 1,
        "global_replay_start": 1,
        "batch_physical_cap": 5,
        "warmup_calls": 0,
        "maximum_candidate_calls": 5,
        "plan_basin_escape": basin,
        "qps_used_for_inner_scoring": False,
        "rejected_candidates_update_incumbent_range_or_accepted": False,
    }

    OUTPUT.mkdir(parents=True)
    state_dir.mkdir()
    atomic_text(paths["formal_search_accepted_counter_path"], "4\n")
    atomic_text(paths["formal_search_replay_counter_path"], "1\n")
    atomic_text(paths["formal_startup_baseline_counter_path"], "0\n")
    atomic_text(paths["global_replay_counter_path"], "1\n")
    output_config = OUTPUT / "config.json"
    atomic_json(output_config, config)
    manifest = {
        "contract": "xp_formal10_accepted4_continuation_v1",
        "schema_version": "xp_formal10_cont4_plan_basin_v1",
        "output_config": str(output_config),
        "output_config_sha256": sha256(output_config),
        "source_config_sha256": EXPECTED_CONFIG_SHA,
        "accepted_count_start": 4,
        "inherited_accepted": 3,
        "new_accepted_before_batch": 1,
        "formal_replay_count_start": 1,
        "global_replay_ordinal_at_binding": 1,
        "per_batch_physical_replay_cap": 5,
        "warmup_calls": 0,
        "maximum_candidate_calls": 5,
        "accepted_target": 10,
        "qps_role": "outer_audit_only",
        "cpu_affinity": "32-51,288-307",
        "mysql_exclusion": "do_not_touch_cpu_184-199,440-455_or_mysql_state",
        "plan_basin_escape": basin,
    }
    atomic_json(OUTPUT / "formal_manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
