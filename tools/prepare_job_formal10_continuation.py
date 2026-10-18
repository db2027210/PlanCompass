#!/usr/bin/env python3
"""Prepare a fail-closed JOB formal continuation without regressing counters."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


BASE = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/"
    "attempt_7_cap5_contract_20260726"
)
SOURCE = BASE / "formal10_after_round10_gate_20260729T1055"
OUTPUT = BASE / "formal10_after_round10_gate_cont1_20260729T1405"
EXPECTED_SOURCE_CONFIG_SHA = (
    "f325799043ae2fcd7ea1d5396f2736d5571048040afc3310285b38ad127bb6a3"
)
EXPECTED_FORMAL_REPLAY = 132
EXPECTED_GLOBAL_ORDINAL = 191


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def read_int(path: Path) -> int:
    return int(path.read_text(encoding="utf-8").strip())


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent), text=True
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
    source_config_path = SOURCE / "config.json"
    if sha256(source_config_path) != EXPECTED_SOURCE_CONFIG_SHA:
        raise RuntimeError("source JOB formal config SHA mismatch")
    source_accepted = SOURCE / "state" / "formal_search_accepted_counter.txt"
    shared_formal_replay = BASE / "formal_search_replay_counter.txt"
    shared_global = BASE / "replay_counter.txt"
    observed = {
        "accepted": read_int(source_accepted),
        "formal_replay": read_int(shared_formal_replay),
        "global_ordinal": read_int(shared_global),
    }
    expected = {
        "accepted": 0,
        "formal_replay": EXPECTED_FORMAL_REPLAY,
        "global_ordinal": EXPECTED_GLOBAL_ORDINAL,
    }
    if observed != expected:
        raise RuntimeError(f"JOB continuation counter mismatch: {observed} != {expected}")

    config = load_json(source_config_path)
    protocol = config.get("formal_outer_protocol")
    if not isinstance(protocol, dict):
        raise RuntimeError("formal_outer_protocol is missing")
    if (
        int(protocol.get("formal_search_accepted_target", -1)) != 10
        or int(protocol.get("formal_search_max_replay_top_k", -1)) != 5
        or protocol.get("qps_used_for_inner_scoring") is not False
    ):
        raise RuntimeError("source formal protocol contract mismatch")

    state_dir = OUTPUT / "state"
    accepted_counter = state_dir / "formal_search_accepted_counter.txt"
    startup_counter = state_dir / "formal_startup_baseline_counter.txt"
    protocol.update(
        {
            "formal_search_accepted_count_start": 0,
            "formal_search_accepted_counter_path": str(accepted_counter),
            "formal_search_replay_count_start": EXPECTED_FORMAL_REPLAY,
            "initial_real_replay_count": EXPECTED_FORMAL_REPLAY,
            "formal_search_replay_counter_path": str(shared_formal_replay),
            "global_replay_ordinal_at_binding": EXPECTED_GLOBAL_ORDINAL,
            "global_replay_counter_path": str(shared_global),
            "formal_startup_baseline_count_start": 0,
            "formal_startup_baseline_counter_path": str(startup_counter),
            "accepted_shrink_target": 10,
            "formal_search_accepted_target": 10,
            "formal_search_initial_replay_top_k": 5,
            "formal_search_second_replay_top_k": 5,
            "formal_search_max_replay_top_k": 5,
            "maximum_resource_qualified_real_replays_per_batch": 5,
            "global_real_replay_top_k_hard_cap": 5,
            "second_replay_batch_enabled": False,
        }
    )
    config["formal_outer_protocol"] = protocol
    config.setdefault("output", {})["directory"] = str(OUTPUT)
    config.setdefault("logging", {})["history_path"] = str(
        OUTPUT / "optimizer_history.jsonl"
    )
    config.setdefault("optimizer", {}).setdefault("gradient", {})[
        "subset_log_dir"
    ] = str(OUTPUT / "subset_logs")
    config["_job_formal_continuation"] = {
        "schema_version": "job_formal10_counter_preserving_continuation_v1",
        "source_formal_dir": str(SOURCE),
        "source_config_sha256": EXPECTED_SOURCE_CONFIG_SHA,
        "accepted_count_start": 0,
        "formal_replay_count_start": EXPECTED_FORMAL_REPLAY,
        "global_replay_ordinal_at_binding": EXPECTED_GLOBAL_ORDINAL,
        "counters_must_never_regress": True,
        "qps_used_for_inner_scoring": False,
        "maximum_real_replays_per_batch": 5,
    }

    OUTPUT.mkdir(parents=True)
    atomic_text(accepted_counter, "0\n")
    atomic_text(startup_counter, "0\n")
    output_config = OUTPUT / "config.json"
    atomic_json(output_config, config)
    manifest = {
        "schema_version": "job_formal10_continuation_manifest_v1",
        "source_formal_dir": str(SOURCE),
        "source_config_sha256": EXPECTED_SOURCE_CONFIG_SHA,
        "output_config": str(output_config),
        "output_config_sha256": sha256(output_config),
        "accepted_shrink_target": 10,
        "inherited_accepted_count": 0,
        "formal_replay_count_start": EXPECTED_FORMAL_REPLAY,
        "global_replay_ordinal_at_binding": EXPECTED_GLOBAL_ORDINAL,
        "shared_formal_replay_counter": str(shared_formal_replay),
        "shared_global_replay_counter": str(shared_global),
    }
    atomic_json(OUTPUT / "continuation_manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
