#!/usr/bin/env python3
"""Prepare JOB formal continuation 3 with a fail-closed rejected-step exclusion."""

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
PREVIOUS = BASE / "formal10_after_round10_gate_cont2_20260729T1420"
OUTPUT = BASE / "formal10_after_round10_gate_cont3_20260729T1508"
SHARED_ACCEPTED = BASE / "formal10_accepted_counter.txt"
SHARED_REPLAY = BASE / "formal_search_replay_counter.txt"
SHARED_GLOBAL = BASE / "replay_counter.txt"
PREVIOUS_HISTORY = PREVIOUS / "optimizer_history_2026-07-29_141329.jsonl"

EXPECTED_CONFIG_SHA = "5234046f1dcb04dd9f4cc55e8fa42d8aaafd438966d33d7c5e9fa4e9e60eff65"
EXPECTED_HISTORY_SHA = "7be97dbc9187980ab1d2a826b7edbbb1c7e36abe72702a823de9bfea9fb78a6e"
EXCLUDED_FINGERPRINT = (
    "three-level-sha256:cf341a2b1ead0cc6fee278017ac4e479e6db8dd334fae42eb379faafe45a9ea2"
)
EXCLUDED_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.0005,
    "cpu_operator_cost": 0.0045000000000000005,
    "parallel_setup_cost": 7000.0,
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
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main() -> None:
    if OUTPUT.exists():
        raise RuntimeError(f"output already exists: {OUTPUT}")
    if sha256(PREVIOUS / "config.json") != EXPECTED_CONFIG_SHA:
        raise RuntimeError("previous config SHA mismatch")
    if sha256(PREVIOUS_HISTORY) != EXPECTED_HISTORY_SHA:
        raise RuntimeError("previous history SHA mismatch")
    if (PREVIOUS / "driver_exit_code.txt").read_text(encoding="utf-8").strip() != "0":
        raise RuntimeError("previous driver did not exit cleanly")
    observed = {
        "accepted": read_int(SHARED_ACCEPTED),
        "formal_replay": read_int(SHARED_REPLAY),
        "global_ordinal": read_int(SHARED_GLOBAL),
    }
    if observed != {"accepted": 0, "formal_replay": 136, "global_ordinal": 195}:
        raise RuntimeError(f"counter mismatch: {observed}")

    ranking_count = 0
    final_count = 0
    with PREVIOUS_HISTORY.open("r", encoding="utf-8") as handle:
        for line in handle:
            event = json.loads(line)
            if event.get("event") == "experience_table_all_subset_ranking":
                ranking_count += 1
                rank1 = event["candidate_rankings"][0]
                if (
                    rank1["three_level_fingerprint"]["id"] != EXCLUDED_FINGERPRINT
                    or rank1["optimized_params"] != EXCLUDED_PARAMS
                ):
                    raise RuntimeError("previous rank1 evidence mismatch")
            elif event.get("event") == "final_summary":
                final_count += 1
                if event.get("optimized_params") != EXCLUDED_PARAMS:
                    raise RuntimeError("previous final params mismatch")
                if not float(event["final_score"]) > float(event["initial_score"]):
                    raise RuntimeError("previous result no longer matches observed improving replay")
    if ranking_count != 1 or final_count != 1:
        raise RuntimeError(
            f"previous history event count mismatch: ranking={ranking_count}, final={final_count}"
        )

    expected_readback = "\n".join(
        [
            "cpu_index_tuple_cost=0.0005",
            "cpu_operator_cost=0.0025",
            "cpu_tuple_cost=0.01",
            "parallel_setup_cost=7000",
            "parallel_tuple_cost=0.001",
            "random_page_cost=4",
            "seq_page_cost=1",
            "",
        ]
    )
    if (PREVIOUS / "rejected_baseline_restore_readback.txt").read_text(
        encoding="utf-8"
    ) != expected_readback:
        raise RuntimeError("previous baseline restore readback mismatch")

    config = json.loads((PREVIOUS / "config.json").read_text(encoding="utf-8"))
    protocol = config["formal_outer_protocol"]
    protocol.update(
        {
            "formal_search_accepted_count_start": 0,
            "formal_search_accepted_counter_path": str(SHARED_ACCEPTED),
            "formal_search_replay_count_start": 136,
            "initial_real_replay_count": 136,
            "formal_search_replay_counter_path": str(SHARED_REPLAY),
            "global_replay_ordinal_at_binding": 195,
            "global_replay_counter_path": str(SHARED_GLOBAL),
            "formal_startup_baseline_count_start": 0,
            "formal_startup_baseline_counter_path": str(
                OUTPUT / "state/formal_startup_baseline_counter.txt"
            ),
        }
    )
    gradient = config["optimizer"]["gradient"]
    if gradient.get("relative_step_probe_pct") != [5, 10, 20, 40, 80, 160, 320, 600]:
        raise RuntimeError("previous gradient probe schedule mismatch")
    gradient["relative_step_probe_pct"] = [5, 10, 20, 40]
    gradient["max_param_delta_rel_pct"] = 40
    gradient["subset_log_dir"] = str(OUTPUT / "subset_logs")
    config["output"]["directory"] = str(OUTPUT)
    config["logging"]["history_path"] = str(OUTPUT / "optimizer_history.jsonl")
    config["_job_formal_continuation"] = {
        "schema_version": "job_formal10_rejected_step_exclusion_cont3_v1",
        "previous_formal_dir": str(PREVIOUS),
        "previous_config_sha256": EXPECTED_CONFIG_SHA,
        "previous_history_sha256": EXPECTED_HISTORY_SHA,
        "accepted_count_start": 0,
        "formal_replay_count_start": 136,
        "global_replay_ordinal_at_binding": 195,
        "excluded_prior_three_level_fingerprints": [EXCLUDED_FINGERPRINT],
        "excluded_prior_params": [EXCLUDED_PARAMS],
        "exclusion_mechanism": (
            "remove_the_80pct_gradient_probe_and_cap_relative_step_at_40pct"
        ),
        "expected_next_cpu_operator_cost_upper_inclusive": 0.0035,
        "qps_used_for_inner_scoring": False,
        "rejected_requires_baseline_restore": True,
        "counters_must_never_regress": True,
    }

    OUTPUT.mkdir(parents=True)
    atomic_text(OUTPUT / "state/formal_startup_baseline_counter.txt", "0\n")
    atomic_json(OUTPUT / "config.json", config)
    manifest = {
        "schema_version": "job_formal10_cont3_manifest_v1",
        "output_config": str(OUTPUT / "config.json"),
        "output_config_sha256": sha256(OUTPUT / "config.json"),
        "previous_config_sha256": EXPECTED_CONFIG_SHA,
        "previous_history_sha256": EXPECTED_HISTORY_SHA,
        "excluded_fingerprint": EXCLUDED_FINGERPRINT,
        "accepted_count_start": 0,
        "formal_replay_count_start": 136,
        "global_replay_ordinal_at_binding": 195,
    }
    atomic_json(OUTPUT / "continuation_manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
