#!/usr/bin/env python3
"""Prepare JOB formal continuation 5 after three rejected gradient steps."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


BASE = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "job_node02/history/job/outer/attempt_7_cap5_contract_20260726"
)
PREVIOUS = BASE / "formal10_after_round10_gate_cont4_20260729T1525"
OUTPUT = BASE / "formal10_after_round10_gate_cont5_20260729T1542"
HISTORY = PREVIOUS / "optimizer_history_2026-07-29_152507.jsonl"
ACCEPTED = BASE / "formal10_accepted_counter.txt"
REPLAY = BASE / "formal_search_replay_counter.txt"
GLOBAL = BASE / "replay_counter.txt"
EXPECTED_CONFIG_SHA = "b00c696ba3635fa711bbef92de9c555acae5cf99f761319a018e16098ec01652"
EXPECTED_HISTORY_SHA = "4988ab482742ac57f4d9d6145d735654b36cf43337fee520ea9d732394e538f8"
EXPECTED_READBACK_SHA = "a62d0e5450185391d221362a79eee35223e8700f9cb7438b13ba2b51c738e532"
PRIOR_FINGERPRINTS = [
    "three-level-sha256:cf341a2b1ead0cc6fee278017ac4e479e6db8dd334fae42eb379faafe45a9ea2",
    "three-level-sha256:cacc58228b090d8ea5eeab0f8c693f01a6088084ecdd88ec4d3c15c829bcf622",
    "three-level-sha256:db17f405d4967a1a9f9b0182e94f1f9bb5a10b0d27c3da35031980a8828467a8",
]
PRIOR_RANK1_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.010437504520084297,
    "cpu_index_tuple_cost": 0.0005,
    "cpu_operator_cost": 0.002625,
    "parallel_setup_cost": 7252.312575220897,
    "parallel_tuple_cost": 0.001,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_int(path: Path) -> int:
    return int(path.read_text(encoding="utf-8").strip())


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    if OUTPUT.exists():
        raise RuntimeError(f"output already exists: {OUTPUT}")
    bindings = {
        PREVIOUS / "config.json": EXPECTED_CONFIG_SHA,
        HISTORY: EXPECTED_HISTORY_SHA,
        PREVIOUS / "rejected_baseline_restore_readback.txt": EXPECTED_READBACK_SHA,
    }
    for path, expected in bindings.items():
        if sha256(path) != expected:
            raise RuntimeError(f"SHA mismatch: {path}")
    if (PREVIOUS / "driver_exit_code.txt").read_text(encoding="utf-8").strip() != "0":
        raise RuntimeError("previous driver exit code mismatch")
    observed = {
        "accepted": read_int(ACCEPTED),
        "formal_replay": read_int(REPLAY),
        "global_ordinal": read_int(GLOBAL),
    }
    if observed != {"accepted": 0, "formal_replay": 140, "global_ordinal": 199}:
        raise RuntimeError(f"counter mismatch: {observed}")

    rankings = []
    finals = []
    exits = []
    with HISTORY.open("r", encoding="utf-8") as handle:
        for line in handle:
            event = json.loads(line)
            if event.get("event") == "experience_table_all_subset_ranking":
                rankings.append(event)
            elif event.get("event") == "final_summary":
                finals.append(event)
            elif event.get("event") == "optimizer_process_exit":
                exits.append(event)
    if len(rankings) != 1 or len(finals) != 1 or len(exits) != 1:
        raise RuntimeError("previous sealed event cardinality mismatch")
    rank1 = rankings[0]["candidate_rankings"][0]
    if (
        rank1["three_level_fingerprint"]["id"] != PRIOR_FINGERPRINTS[2]
        or rank1["optimized_params"] != PRIOR_RANK1_PARAMS
        or finals[0]["optimized_params"] != PRIOR_RANK1_PARAMS
        or not float(finals[0]["actual_final_score"])
        < float(finals[0]["actual_initial_score"])
    ):
        raise RuntimeError("previous rejected rank1 evidence mismatch")

    config = json.loads((PREVIOUS / "config.json").read_text(encoding="utf-8"))
    protocol = config["formal_outer_protocol"]
    protocol.update(
        {
            "formal_search_accepted_count_start": 0,
            "formal_search_accepted_counter_path": str(ACCEPTED),
            "formal_search_replay_count_start": 140,
            "initial_real_replay_count": 140,
            "formal_search_replay_counter_path": str(REPLAY),
            "global_replay_ordinal_at_binding": 199,
            "global_replay_counter_path": str(GLOBAL),
            "formal_startup_baseline_count_start": 0,
            "formal_startup_baseline_counter_path": str(
                OUTPUT / "state/formal_startup_baseline_counter.txt"
            ),
        }
    )
    gradient = config["optimizer"]["gradient"]
    if (
        gradient.get("relative_step_probe_pct") != [5, 10, 20]
        or float(gradient.get("max_param_delta_rel_pct")) != 20.0
    ):
        raise RuntimeError("previous gradient schedule mismatch")
    gradient["relative_step_probe_pct"] = [5, 10]
    gradient["max_param_delta_rel_pct"] = 10
    gradient["subset_log_dir"] = str(OUTPUT / "subset_logs")
    config["output"]["directory"] = str(OUTPUT)
    config["logging"]["history_path"] = str(OUTPUT / "optimizer_history.jsonl")
    config["_job_formal_continuation"] = {
        "schema_version": "job_formal10_rejected_step_exclusion_cont5_v1",
        "previous_formal_dir": str(PREVIOUS),
        "previous_config_sha256": EXPECTED_CONFIG_SHA,
        "previous_history_sha256": EXPECTED_HISTORY_SHA,
        "accepted_count_start": 0,
        "formal_replay_count_start": 140,
        "global_replay_ordinal_at_binding": 199,
        "excluded_prior_three_level_fingerprints": PRIOR_FINGERPRINTS,
        "excluded_prior_params": [PRIOR_RANK1_PARAMS],
        "exclusion_mechanism": (
            "remove_the_20pct_gradient_probe_and_cap_relative_step_at_10pct"
        ),
        "expected_next_cpu_operator_cost_upper_inclusive": 0.00275,
        "qps_used_for_inner_scoring": False,
        "rejected_requires_baseline_restore": True,
        "counters_must_never_regress": True,
    }
    OUTPUT.mkdir(parents=True)
    atomic_text(OUTPUT / "state/formal_startup_baseline_counter.txt", "0\n")
    atomic_json(OUTPUT / "config.json", config)
    manifest = {
        "schema_version": "job_formal10_cont5_manifest_v1",
        "output_config": str(OUTPUT / "config.json"),
        "output_config_sha256": sha256(OUTPUT / "config.json"),
        "previous_config_sha256": EXPECTED_CONFIG_SHA,
        "previous_history_sha256": EXPECTED_HISTORY_SHA,
        "excluded_fingerprints": PRIOR_FINGERPRINTS,
        "accepted_count_start": 0,
        "formal_replay_count_start": 140,
        "global_replay_ordinal_at_binding": 199,
    }
    atomic_json(OUTPUT / "continuation_manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
