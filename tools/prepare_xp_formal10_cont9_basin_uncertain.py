#!/usr/bin/env python3
"""Prepare XP continuation 9 after the zero-call cont8 helper exception."""

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
SOURCE = ATTEMPT / "formal10_cont8_accepted4_basin_uncertain_v1_20260729T1916"
OUTPUT = ATTEMPT / "formal10_cont9_accepted4_basin_uncertain_v2_20260729T1920"
PROJECT = Path("/home/anonymous/program/resource_adaptive")
EXPECTED = {
    SOURCE / "config.json":
        "1c3164c1ea49f019829c497cee88bb2f83f2ae6558f91664cee03a2976d0cbdb",
    SOURCE / "optimizer_history_2026-07-29_191645.jsonl":
        "61a4d5f1f5bbd56d13d6ac1693c4a49b65dd5006c2a48d7decccbf316658e2b0",
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
    EXPECTED[PROJECT / "experience_table_subset_optimizer.py"] = sha256(
        PROJECT / "experience_table_subset_optimizer.py"
    )
    for path, expected in EXPECTED.items():
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"SHA mismatch: {path}: {actual} != {expected}")
    source_pid = int(
        (SOURCE / "formal_launcher.pid").read_text(encoding="utf-8").strip()
    )
    if Path(f"/proc/{source_pid}").exists():
        raise RuntimeError(f"XP cont8 process is still active: {source_pid}")
    history_tail = (
        SOURCE / "optimizer_history_2026-07-29_191645.jsonl"
    ).read_text(encoding="utf-8")
    if (
        '"event": "optimizer_process_exception"' not in history_tail
        or '"exception_type": "NameError"' not in history_tail
    ):
        raise RuntimeError("XP cont8 lacks the expected zero-call helper exception")
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
    ratio_scale = config["metrics"]["plan_estimator"]["ratio_scale"]
    ratio_scale.setdefault("boundary_scan", {})["enabled"] = False
    config["optimizer"]["gradient"]["subset_log_dir"] = str(
        OUTPUT / "subset_logs"
    )
    config["logging"]["history_path"] = str(OUTPUT / "optimizer_history.jsonl")
    config["output"]["directory"] = str(OUTPUT)
    config["_xp_formal_continuation"] = {
        "contract": "xp_formal10_accepted4_basin_uncertain_probe_v2",
        "source_continuation": str(SOURCE),
        "accepted_start": 4,
        "formal_replay_start": 2,
        "global_replay_start": 2,
        "batch_physical_cap": 5,
        "warmup_calls": 0,
        "maximum_candidate_calls": 5,
        "boundary_scan_disabled_reason": (
            "five exact plan-basin fingerprints exist but no validated "
            "same-lane ratio-scale error bound is available; replay them as "
            "uncertain probes and retain the actual resource acceptance gate"
        ),
        "uncertain_probe_updates_gradient_incumbent_range_or_accepted": False,
        "qps_used_for_inner_scoring": False,
        "rejected_candidates_update_incumbent_range_or_accepted": False,
    }

    OUTPUT.mkdir(parents=True)
    state_dir.mkdir()
    for name, expected in expected_counters.items():
        atomic_text(state_dir / name, f"{expected}\n")
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
        "schema_version": "xp_formal10_cont9_basin_uncertain_v2",
        "output_config": str(config_path),
        "output_config_sha256": sha256(config_path),
        "parameter_optimizer_sha256": EXPECTED[
            ATTEMPT / "repair/parameter_optimizer.py"
        ],
        "candidate_helper_sha256": EXPECTED[
            PROJECT / "experience_table_subset_optimizer.py"
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
            "all required audit fields plus three-level fingerprint; "
            "uncertain probes cannot mutate state unless actual outer gate accepts"
        ),
        "cont8_zero_call_failure": "NameError_in_candidate_audit_helper",
    }
    atomic_json(OUTPUT / "formal_manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
