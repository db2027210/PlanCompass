#!/usr/bin/env python3
"""Prepare JOB formal continuation 6 with hard cross-batch fingerprint exclusion."""

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
PREVIOUS = BASE / "formal10_after_round10_gate_cont5_20260729T1542"
OUTPUT = BASE / "formal10_after_round10_gate_cont6_fpblacklist_v2_20260729T1610"
HISTORY = PREVIOUS / "optimizer_history_2026-07-29_154222.jsonl"
ACCEPTED = BASE / "formal10_accepted_counter.txt"
REPLAY = BASE / "formal_search_replay_counter.txt"
GLOBAL = BASE / "replay_counter.txt"

EXPECTED_CONFIG_SHA = "bf7dc91e93b112341209141a9a91c99132719d35fee5b39f6d94dc57a8b9ee2f"
EXPECTED_HISTORY_SHA = "a5c04902b4b0e1263980d81368dbdee5deda364a110af104c9cc292deff4d38b"
EXPECTED_READBACK_SHA = "a62d0e5450185391d221362a79eee35223e8700f9cb7438b13ba2b51c738e532"
EXPECTED_PATCHED_OPTIMIZER_SHA = (
    "535e60704d7ca6ab18a13e6b0138e98dc9382a8ed884eaffcf33cbae7936bd30"
)
PRIOR_FINGERPRINTS = [
    "three-level-sha256:cf341a2b1ead0cc6fee278017ac4e479e6db8dd334fae42eb379faafe45a9ea2",
    "three-level-sha256:cacc58228b090d8ea5eeab0f8c693f01a6088084ecdd88ec4d3c15c829bcf622",
    "three-level-sha256:db17f405d4967a1a9f9b0182e94f1f9bb5a10b0d27c3da35031980a8828467a8",
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
        WORKSPACE / "parameter_optimizer.py": EXPECTED_PATCHED_OPTIMIZER_SHA,
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
        "formal_replay": 142,
        "global_ordinal": 201,
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
    if not float(final["actual_final_score"]) < float(final["actual_initial_score"]):
        raise RuntimeError("previous replay is no longer a rejected regression")

    config = json.loads((PREVIOUS / "config.json").read_text(encoding="utf-8"))
    protocol = config["formal_outer_protocol"]
    protocol.update(
        {
            "formal_search_accepted_count_start": 0,
            "formal_search_accepted_counter_path": str(ACCEPTED),
            "formal_search_replay_count_start": 142,
            "initial_real_replay_count": 142,
            "formal_search_replay_counter_path": str(REPLAY),
            "global_replay_ordinal_at_binding": 201,
            "global_replay_counter_path": str(GLOBAL),
            "formal_startup_baseline_count_start": 0,
            "formal_startup_baseline_counter_path": str(
                OUTPUT / "state/formal_startup_baseline_counter.txt"
            ),
            "excluded_prior_three_level_fingerprints": PRIOR_FINGERPRINTS,
        }
    )
    gradient = config["optimizer"]["gradient"]
    if (
        gradient.get("relative_step_probe_pct") != [5, 10]
        or float(gradient.get("max_param_delta_rel_pct")) != 10.0
    ):
        raise RuntimeError("previous gradient schedule mismatch")
    gradient["subset_log_dir"] = str(OUTPUT / "subset_logs")
    config["output"]["directory"] = str(OUTPUT)
    config["logging"]["history_path"] = str(OUTPUT / "optimizer_history.jsonl")
    config["_job_formal_continuation"] = {
        "schema_version": "job_formal10_cont6_fpblacklist_v2",
        "previous_formal_dir": str(PREVIOUS),
        "previous_config_sha256": EXPECTED_CONFIG_SHA,
        "previous_history_sha256": EXPECTED_HISTORY_SHA,
        "accepted_count_start": 0,
        "formal_replay_count_start": 142,
        "global_replay_ordinal_at_binding": 201,
        "excluded_prior_three_level_fingerprints": PRIOR_FINGERPRINTS,
        "exclusion_mechanism": (
            "hard_filter_before_real_replay_and_deduplicate_by_three_level_id"
        ),
        "filter_contract": (
            "formal_cross_batch_three_level_fingerprint_exclusion_v1"
        ),
        "parameter_optimizer_sha256": EXPECTED_PATCHED_OPTIMIZER_SHA,
        "qps_used_for_inner_scoring": False,
        "rejected_requires_baseline_restore": True,
        "counters_must_never_regress": True,
    }

    OUTPUT.mkdir(parents=True)
    atomic_text(OUTPUT / "state/formal_startup_baseline_counter.txt", "0\n")
    atomic_json(OUTPUT / "config.json", config)
    manifest = {
        "schema_version": "job_formal10_cont6_fpblacklist_manifest_v2",
        "output_config": str(OUTPUT / "config.json"),
        "output_config_sha256": sha256(OUTPUT / "config.json"),
        "previous_config_sha256": EXPECTED_CONFIG_SHA,
        "previous_history_sha256": EXPECTED_HISTORY_SHA,
        "parameter_optimizer_sha256": EXPECTED_PATCHED_OPTIMIZER_SHA,
        "excluded_fingerprints": PRIOR_FINGERPRINTS,
        "accepted_count_start": 0,
        "formal_replay_count_start": 142,
        "global_replay_ordinal_at_binding": 201,
        "maximum_physical_workload_calls": 5,
    }
    atomic_json(OUTPUT / "continuation_manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
