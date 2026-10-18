#!/usr/bin/env python3
"""Prepare XP continuation 7 with plan-basin fingerprint propagation fixed."""

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
SOURCE = ATTEMPT / "formal10_cont6_accepted4_cap5_accounted_v2_20260729T1842"
OUTPUT = ATTEMPT / "formal10_cont7_accepted4_basin_fpfix_v1_20260729T1908"
EXPECTED = {
    SOURCE / "config.json":
        "66f8a0fecb4770fb04e44221ec50328737d53101bb4dc20890483f04f6fc8159",
    SOURCE / "optimizer_history_2026-07-29_184159.jsonl":
        "c9877101e378b1651e02a823d29830d662c7fa198147ee89d7f4e2f89708663c",
    SOURCE / "state/plan_basin_escape_state.json":
        "863872abde812c2654f823ad37614bad3addeb938d76e540be4a5c097891987a",
    ATTEMPT / "repair/parameter_optimizer.py":
        "a098a9e8981446e390df809e3529420cbfbdf49b2798bd52f9fc8ee19995e55c",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    for path, expected in EXPECTED.items():
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"SHA mismatch: {path}: {actual} != {expected}")
    expected_counters = {
        "formal_search_accepted_counter.txt": 4,
        "formal_search_replay_counter.txt": 2,
        "formal_startup_baseline_counter.txt": 0,
        "global_replay_counter.txt": 2,
    }
    for name, expected in expected_counters.items():
        actual = int((SOURCE / "state" / name).read_text(encoding="utf-8"))
        if actual != expected:
            raise RuntimeError(f"counter mismatch: {name}: {actual} != {expected}")
    config = json.loads((SOURCE / "config.json").read_text(encoding="utf-8"))
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
    protocol.update({
        "formal_search_accepted_count_start": 4,
        "formal_search_replay_count_start": 2,
        "initial_real_replay_count": 2,
        "global_replay_ordinal_at_binding": 2,
        "formal_startup_baseline_count_start": 0,
        "formal_search_attempt_safety_cap": 7,
        "formal_search_maximum_replay_count": 7,
        "maximum_cumulative_real_replay_count": 7,
        "maximum_non_default_real_candidates": 5,
        "maximum_resource_qualified_real_replays_per_batch": 5,
        "global_real_replay_top_k_hard_cap": 5,
    })
    basin = config["optimizer"]["gradient"]["plan_basin_escape"]
    basin["enabled"] = True
    basin["state_path"] = str(state_dir / "plan_basin_escape_state.json")
    config["optimizer"]["gradient"]["plateau_exploration_enabled"] = True
    config["optimizer"]["gradient"]["subset_log_dir"] = str(
        OUTPUT / "subset_logs"
    )
    config["logging"]["history_path"] = str(OUTPUT / "optimizer_history.jsonl")
    config["output"]["directory"] = str(OUTPUT)
    config["_xp_formal_continuation"] = {
        "contract": "xp_formal10_accepted4_basin_fingerprint_fix_v1",
        "source_continuation": str(SOURCE),
        "source_config_sha256": EXPECTED[SOURCE / "config.json"],
        "accepted_start": 4,
        "formal_replay_start": 2,
        "global_replay_start": 2,
        "batch_physical_cap": 5,
        "warmup_calls": 0,
        "maximum_candidate_calls": 5,
        "plan_basin_fingerprint_fix_optimizer_sha256": EXPECTED[
            ATTEMPT / "repair/parameter_optimizer.py"
        ],
        "plan_basin_fingerprint_helper_sha256": (
            "26271377543b788c44fba0567688ed82d196db9c192b1f7e6ef1b677073bfb07"
        ),
        "qps_used_for_inner_scoring": False,
        "rejected_candidates_update_incumbent_range_or_accepted": False,
    }

    OUTPUT.mkdir(parents=True)
    state_dir.mkdir()
    atomic_text(paths["formal_search_accepted_counter_path"], "4\n")
    atomic_text(paths["formal_search_replay_counter_path"], "2\n")
    atomic_text(paths["formal_startup_baseline_counter_path"], "0\n")
    atomic_text(paths["global_replay_counter_path"], "2\n")
    atomic_text(
        state_dir / "plan_basin_escape_state.json",
        (SOURCE / "state/plan_basin_escape_state.json").read_text(
            encoding="utf-8"
        ),
    )
    config_path = OUTPUT / "config.json"
    atomic_json(config_path, config)
    manifest = {
        "contract": "xp_formal10_accepted4_continuation_v1",
        "schema_version": "xp_formal10_cont7_basin_fpfix_v1",
        "output_config": str(config_path),
        "output_config_sha256": sha256(config_path),
        "parameter_optimizer_sha256": EXPECTED[
            ATTEMPT / "repair/parameter_optimizer.py"
        ],
        "accepted_count_start": 4,
        "inherited_accepted": 3,
        "new_accepted_before_batch": 1,
        "formal_replay_count_start": 2,
        "global_replay_ordinal_at_binding": 2,
        "per_batch_physical_replay_cap": 5,
        "warmup_calls": 0,
        "accepted_target": 10,
        "qps_role": "outer_audit_only",
        "cpu_affinity": "32-51,288-307",
        "mysql_exclusion": "do_not_touch_cpu_184-199,440-455_or_mysql_state",
        "plan_basin_escape_state_sha256": sha256(
            state_dir / "plan_basin_escape_state.json"
        ),
        "candidate_contract_fix": (
            "propagate_plan_basin_rankings_and_attach_three_level_fingerprint"
        ),
    }
    atomic_json(OUTPUT / "formal_manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
