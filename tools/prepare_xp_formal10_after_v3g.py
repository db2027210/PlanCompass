#!/usr/bin/env python3
"""Prepare an isolated XP formal-10 configuration after the v3g entry gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-config", required=True, type=Path)
    parser.add_argument("--gate-audit", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    if args.output_dir.exists():
        raise FileExistsError(f"formal output already exists: {args.output_dir}")

    gate = load_json(args.gate_audit)
    checks = gate.get("checks")
    if gate.get("gate_passed") is not True or not isinstance(checks, dict):
        raise RuntimeError("XP v3g entry gate did not pass")
    failed_checks = sorted(name for name, passed in checks.items() if passed is not True)
    if failed_checks:
        raise RuntimeError(f"XP v3g entry checks failed: {failed_checks}")

    config = load_json(args.source_config)
    protocol = config.get("formal_outer_protocol")
    if not isinstance(protocol, dict):
        raise RuntimeError("source formal_outer_protocol is missing")
    if int(protocol.get("formal_search_accepted_target", -1)) != 10:
        raise RuntimeError("source accepted target is not 10")
    if int(protocol.get("global_real_replay_top_k_hard_cap", -1)) != 5:
        raise RuntimeError("source per-batch physical replay cap is not 5")

    state_dir = args.output_dir / "state"
    paths = {
        "formal_search_accepted_counter_path": state_dir / "formal_search_accepted_counter.txt",
        "formal_search_replay_counter_path": state_dir / "formal_search_replay_counter.txt",
        "formal_startup_baseline_counter_path": state_dir / "formal_startup_baseline_counter.txt",
        "global_replay_counter_path": state_dir / "global_replay_counter.txt",
    }
    for name, path in paths.items():
        protocol[name] = str(path)

    protocol.update({
        "accepted_shrink_target": 10,
        "candidate_failure_consumes_budget": True,
        "candidate_replay_top_k": 5,
        "formal_search_accepted_count_start": 0,
        "formal_search_accepted_target": 10,
        "formal_search_attempt_safety_cap": 130,
        "formal_search_initial_replay_top_k": 5,
        "formal_search_max_replay_top_k": 5,
        "formal_search_maximum_replay_count": 130,
        "formal_search_replay_count_start": 0,
        "formal_search_replay_cumulative_tiers": [5],
        "formal_search_second_replay_top_k": 5,
        "formal_startup_baseline_count_start": 0,
        "global_real_replay_top_k_hard_cap": 5,
        "global_replay_count_start": 0,
        "global_replay_ordinal_at_binding": 0,
        "initial_real_replay_count": 0,
        "maximum_cumulative_real_replay_count": 130,
        "maximum_non_default_real_candidates": 130,
        "maximum_resource_qualified_real_replays_per_batch": 5,
        "second_replay_batch_enabled": False,
        "valid_initial_batch_count_start": 0,
    })
    config["formal_outer_protocol"] = protocol

    config.setdefault("_gradient_contract_round", {}).update({
        "diagnostic_only": False,
        "generate_only": False,
        "real_replay_owner": "formal_optimizer_loop",
    })
    config.setdefault("_xp_next_validation_round", {}).update({
        "accepted_shrink_target": 10,
        "maximum_valid_real_replays": 5,
        "qps_used_for_inner_scoring": False,
        "rejected_candidates_update_incumbent_range_or_accepted": False,
        "technical_failures_count_as_replay": True,
    })
    config.setdefault("logging", {})["history_path"] = str(
        args.output_dir / "optimizer_history.jsonl"
    )
    config.setdefault("optimizer", {}).setdefault("gradient", {})[
        "subset_log_dir"
    ] = str(args.output_dir / "subset_logs")
    config.setdefault("output", {})["directory"] = str(args.output_dir)

    args.output_dir.mkdir(parents=True)
    for path in paths.values():
        atomic_text(path, "0\n")
    output_config = args.output_dir / "config.json"
    atomic_json(output_config, config)

    manifest = {
        "contract": "xp_formal10_after_v3g_entry_gate_v1",
        "accepted_shrink_target": 10,
        "accepted_count_start": 0,
        "inherited_accepted_audit_only": 3,
        "per_batch_physical_replay_cap": 5,
        "formal_replay_safety_cap": 130,
        "qps_role": "outer_audit_only",
        "source_config": str(args.source_config),
        "source_config_sha256": sha256(args.source_config),
        "entry_gate_audit": str(args.gate_audit),
        "entry_gate_audit_sha256": sha256(args.gate_audit),
        "output_config": str(output_config),
        "output_config_sha256": sha256(output_config),
        "state_counters": {name: str(path) for name, path in paths.items()},
    }
    atomic_json(args.output_dir / "formal_manifest.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
