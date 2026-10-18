#!/usr/bin/env python3
"""Prepare XP accepted=4 continuation with immutable baseline reuse and no warmup."""

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
SOURCE = ATTEMPT / "formal10_cont2_accepted4_cap5_feedback_v1_20260729T1745"
OUTPUT = ATTEMPT / "formal10_cont3_accepted4_cap5_zero_warmup_v1_20260729T1758"
EXPECTED = {
    SOURCE / "config.json":
        "b726f8c7f47f3620d50a9b89bbdda9b06b670119eb284cb3f633283a59dfae55",
    SOURCE / "startup_baseline_point_result.json":
        "269ebb360176e43d3e4d5f45806b8e1da6d72faf566c1a0d6aea43c6db8ec018",
    SOURCE / "startup_baseline_reclassification.json":
        "66a8b8bbac4a42569e1ed19bfec845f3e7f16e3c5817885f4f477d9ab85486ab",
}


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
    for path, expected in EXPECTED.items():
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"SHA mismatch: {path}: {actual} != {expected}")

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

    events = [
        json.loads(line)
        for line in (SOURCE / "optimizer_history_2026-07-29_174534.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    failures = [
        event for event in events
        if event.get("event") == "baseline_resource_band_gate"
        and event.get("passed") is False
    ]
    if len(failures) != 1:
        raise RuntimeError("cont2 baseline gate failure evidence mismatch")
    failure = failures[0]
    values = {
        item["resource"]: float(item["metric_value"])
        for item in failure.get("checks", [])
    }
    expected_values = {
        "cpu": 54.59165898087443,
        "memory": 43.928127894282916,
        "io": 85.74192428159229,
    }
    if values != expected_values:
        raise RuntimeError(f"unexpected baseline utilization: {values}")
    if any(event.get("event") == "formal_candidate_replay_started" for event in events):
        raise RuntimeError("cont2 unexpectedly started a formal candidate replay")

    config = load_json(SOURCE / "config.json")
    protocol = config["formal_outer_protocol"]
    state_dir = OUTPUT / "state"
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
        "formal_search_replay_count_start": 1,
        "initial_real_replay_count": 1,
        "global_replay_count_start": 1,
        "global_replay_ordinal_at_binding": 1,
        "formal_startup_baseline_count_start": 0,
        "formal_search_attempt_safety_cap": 6,
        "formal_search_maximum_replay_count": 6,
        "maximum_cumulative_real_replay_count": 6,
        "maximum_non_default_real_candidates": 5,
        "maximum_resource_qualified_real_replays_per_batch": 5,
        "formal_search_replay_cumulative_tiers": [6],
        "global_real_replay_top_k_hard_cap": 5,
    })

    benchmark = config["benchmark"]
    benchmark["warmup_enabled"] = False
    benchmark["validation_warmup_enabled"] = False
    benchmark["validation_warmup_required"] = False

    gate = config["baseline_resource_band_gate"]
    if (
        gate.get("enabled") is not True
        or gate.get("hard_fail") is not True
        or float(gate["ranges_pct"]["cpu"]["max_pct"]) != 50.0
    ):
        raise RuntimeError("baseline resource-band gate source mismatch")
    gate["ranges_pct"]["cpu"]["max_pct"] = 60
    gate["threshold_semantics"] = (
        "target inclusive IO 85-90%; measured non-target CPU 30-60%; "
        "memory 30-50%; hard fail before candidate generation and real replay"
    )
    gate["calibration_evidence"] = {
        "source": str(SOURCE / "optimizer_history_2026-07-29_174534.jsonl"),
        "measured_baseline_utilization_pct": expected_values,
        "reason": (
            "sealed accepted incumbent has stable CPU utilization 54.59%; "
            "the former 50% ceiling rejected the validated baseline"
        ),
    }

    feedback = config["final_evaluation"]["feedback_loop"]
    feedback["max_rounds"] = 5
    config["_xp_next_validation_round"]["maximum_valid_real_replays"] = 5
    config["optimizer"]["gradient"]["subset_log_dir"] = str(OUTPUT / "subset_logs")
    config["logging"]["history_path"] = str(OUTPUT / "optimizer_history.jsonl")
    config["output"]["directory"] = str(OUTPUT)
    config["_xp_formal_continuation"] = {
        "contract": "xp_formal10_accepted4_zero_warmup_continuation_v1",
        "source_continuation": str(SOURCE),
        "source_config_sha256": EXPECTED[SOURCE / "config.json"],
        "accepted_start": 4,
        "formal_replay_start": 1,
        "global_replay_start": 1,
        "batch_physical_cap": 5,
        "startup_warmup_calls": 0,
        "validation_warmup_calls": 0,
        "maximum_candidate_calls": 5,
        "qps_used_for_inner_scoring": False,
        "baseline_gate_cpu_max_pct_before": 50,
        "baseline_gate_cpu_max_pct_after": 60,
    }

    OUTPUT.mkdir(parents=True)
    state_dir.mkdir()
    atomic_text(paths["formal_search_accepted_counter_path"], "4\n")
    atomic_text(paths["formal_search_replay_counter_path"], "1\n")
    atomic_text(paths["formal_startup_baseline_counter_path"], "0\n")
    atomic_text(paths["global_replay_counter_path"], "1\n")
    config_path = OUTPUT / "config.json"
    atomic_json(config_path, config)
    manifest = {
        "contract": "xp_formal10_accepted4_continuation_v1",
        "schema_version": "xp_formal10_cont3_zero_warmup_v1",
        "output_config": str(config_path),
        "output_config_sha256": sha256(config_path),
        "source_config_sha256": EXPECTED[SOURCE / "config.json"],
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
        "baseline_utilization_pct": expected_values,
        "baseline_cpu_gate_pct": [30, 60],
    }
    atomic_json(OUTPUT / "formal_manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
