#!/usr/bin/env python3
"""Prepare JOB continuation 7 with the formal feedback state machine enabled."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


BASE = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "job_node02/history/job/outer/attempt_7_cap5_contract_20260726"
)
WORKSPACE = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "job_node02/workspace_attempt7"
)
PREVIOUS = BASE / "formal10_after_round10_gate_cont6_fpblacklist_v2_20260729T1610"
OUTPUT = BASE / "formal10_after_round10_gate_cont7_feedback_v1_20260729T1645"
HISTORY = PREVIOUS / "optimizer_history_2026-07-29_161236.jsonl"
ACCEPTED = BASE / "formal10_accepted_counter.txt"
REPLAY = BASE / "formal_search_replay_counter.txt"
GLOBAL = BASE / "replay_counter.txt"

EXPECTED_CONFIG_SHA = "d8ce7afde6c92830b450e569bedb458dccc2f421d3c001fd3b65d66fdd3c1dbd"
EXPECTED_HISTORY_SHA = "99141e8c475da02471a7deb2fa5e12e09784c5447717994bc6625d65145c66d0"
EXPECTED_READBACK_SHA = "a62d0e5450185391d221362a79eee35223e8700f9cb7438b13ba2b51c738e532"
EXPECTED_OPTIMIZER_SHA = (
    "535e60704d7ca6ab18a13e6b0138e98dc9382a8ed884eaffcf33cbae7936bd30"
)
PRIOR_FINGERPRINTS = [
    "three-level-sha256:cf341a2b1ead0cc6fee278017ac4e479e6db8dd334fae42eb379faafe45a9ea2",
    "three-level-sha256:cacc58228b090d8ea5eeab0f8c693f01a6088084ecdd88ec4d3c15c829bcf622",
    "three-level-sha256:db17f405d4967a1a9f9b0182e94f1f9bb5a10b0d27c3da35031980a8828467a8",
    "three-level-sha256:81f220c14d73a694fd734d56e88db77003d1ead13a76c4cf41e8fa5fb722222d",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_int(path: Path) -> int:
    return int(path.read_text(encoding="utf-8").strip())


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"continuation output already exists: {OUTPUT}")
    bindings = {
        PREVIOUS / "config.json": EXPECTED_CONFIG_SHA,
        HISTORY: EXPECTED_HISTORY_SHA,
        PREVIOUS / "rejected_baseline_restore_readback.txt": EXPECTED_READBACK_SHA,
        WORKSPACE / "parameter_optimizer.py": EXPECTED_OPTIMIZER_SHA,
    }
    for path, expected in bindings.items():
        observed = sha256(path)
        if observed != expected:
            raise RuntimeError(f"SHA mismatch for {path}: {observed} != {expected}")
    if (PREVIOUS / "driver_exit_code.txt").read_text(encoding="utf-8").strip() != "0":
        raise RuntimeError("previous driver did not exit cleanly")

    observed_counters = {
        "accepted": read_int(ACCEPTED),
        "formal_replay": read_int(REPLAY),
        "global_ordinal": read_int(GLOBAL),
    }
    expected_counters = {
        "accepted": 0,
        "formal_replay": 144,
        "global_ordinal": 203,
    }
    if observed_counters != expected_counters:
        raise RuntimeError(
            f"counter mismatch: {observed_counters} != {expected_counters}"
        )

    ranking_events = []
    final_events = []
    with HISTORY.open("r", encoding="utf-8") as handle:
        for line in handle:
            event = json.loads(line)
            if event.get("event") == "experience_table_all_subset_ranking":
                ranking_events.append(event)
            elif event.get("event") == "final_summary":
                final_events.append(event)
    if len(ranking_events) != 1 or len(final_events) != 1:
        raise RuntimeError("previous sealed ranking/final event cardinality mismatch")
    rank1 = ranking_events[0]["candidate_rankings"][0]
    final = final_events[0]
    if rank1["three_level_fingerprint"]["id"] != PRIOR_FINGERPRINTS[-1]:
        raise RuntimeError("previous replayed rank1 fingerprint mismatch")
    if rank1["optimized_params"] != final["optimized_params"]:
        raise RuntimeError("previous rank1/final parameter evidence mismatch")
    if not float(final["actual_final_score"]) > float(final["actual_initial_score"]):
        raise RuntimeError("previous final replay is no longer a score improvement")
    if final["final_effect_evaluation"]["target_achieved"] is not False:
        raise RuntimeError("previous run unexpectedly reached the target range")

    config = json.loads((PREVIOUS / "config.json").read_text(encoding="utf-8"))
    feedback = config["final_evaluation"]["feedback_loop"]
    if feedback.get("enabled") is not False:
        raise RuntimeError("previous feedback state machine was not disabled")
    if config["final_evaluation"].get("run_validation") is not False:
        raise RuntimeError("previous run_validation flag was not disabled")

    protocol = config["formal_outer_protocol"]
    protocol.update(
        {
            "formal_search_accepted_count_start": 0,
            "formal_search_accepted_counter_path": str(ACCEPTED),
            "formal_search_replay_count_start": 144,
            "initial_real_replay_count": 144,
            "formal_search_replay_counter_path": str(REPLAY),
            "global_replay_ordinal_at_binding": 203,
            "global_replay_counter_path": str(GLOBAL),
            "formal_startup_baseline_count_start": 0,
            "formal_startup_baseline_counter_path": str(
                OUTPUT / "state/formal_startup_baseline_counter.txt"
            ),
            "excluded_prior_three_level_fingerprints": PRIOR_FINGERPRINTS,
        }
    )
    feedback["enabled"] = True
    feedback["max_rounds"] = 1
    feedback["stop_after_primary_acceptance"] = True
    feedback["continue_after_primary_acceptance_rounds"] = 0
    feedback["select_best_primary_by_db_qps"] = False
    resource_goal = feedback["resource_iteration_goal"]
    resource_goal["stop_after_primary_acceptance"] = True
    resource_goal["continue_after_primary_acceptance_rounds"] = 0
    resource_goal["select_best_primary_by_db_qps"] = False
    strong_constraints = resource_goal["strong_constraints"]
    strong_constraints["enabled"] = False
    strong_constraints["disabled_reason"] = (
        "use_common_outer_qps_and_strict_target_down_gate; "
        "legacy_phase2_15pct_qps_5pp_memory_gate_is_not_the_formal_contract"
    )
    config["final_evaluation"]["run_validation"] = True

    gradient = config["optimizer"]["gradient"]
    gradient["subset_log_dir"] = str(OUTPUT / "subset_logs")
    config["output"]["directory"] = str(OUTPUT)
    config["logging"]["history_path"] = str(OUTPUT / "optimizer_history.jsonl")
    config["_job_formal_continuation"] = {
        "schema_version": "job_formal10_cont7_feedback_enabled_v1",
        "previous_formal_dir": str(PREVIOUS),
        "previous_config_sha256": EXPECTED_CONFIG_SHA,
        "previous_history_sha256": EXPECTED_HISTORY_SHA,
        "accepted_count_start": 0,
        "formal_replay_count_start": 144,
        "global_replay_ordinal_at_binding": 203,
        "excluded_prior_three_level_fingerprints": PRIOR_FINGERPRINTS,
        "formal_feedback_state_machine_enabled": True,
        "formal_acceptance_contract": (
            "common_outer_qps_strictly_higher_target_bottleneck_strictly_lower_"
            "non_targets_safe_v1"
        ),
        "legacy_phase2_strong_gate_enabled": False,
        "qps_used_for_inner_scoring": False,
        "rejected_requires_baseline_restore": True,
        "counters_must_never_regress": True,
    }

    OUTPUT.mkdir(parents=True)
    atomic_text(OUTPUT / "state/formal_startup_baseline_counter.txt", "0\n")
    atomic_json(OUTPUT / "config.json", config)
    manifest = {
        "schema_version": "job_formal10_cont7_feedback_manifest_v1",
        "output_config": str(OUTPUT / "config.json"),
        "output_config_sha256": sha256(OUTPUT / "config.json"),
        "previous_config_sha256": EXPECTED_CONFIG_SHA,
        "previous_history_sha256": EXPECTED_HISTORY_SHA,
        "parameter_optimizer_sha256": EXPECTED_OPTIMIZER_SHA,
        "excluded_fingerprints": PRIOR_FINGERPRINTS,
        "accepted_count_start": 0,
        "formal_replay_count_start": 144,
        "global_replay_ordinal_at_binding": 203,
        "maximum_candidate_replays_per_batch": 5,
        "formal_feedback_state_machine_enabled": True,
        "qps_used_for_inner_scoring": False,
    }
    atomic_json(OUTPUT / "continuation_manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
