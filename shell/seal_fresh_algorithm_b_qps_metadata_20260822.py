#!/usr/bin/env python3
"""Seal the not-yet-started B r3 metadata to the last-accepted QPS contract."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping


ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
    "20260822_algorithm_b_last_accepted_qps_clean_r3"
)
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
CONTRACT = "candidate_qps_strictly_above_previous_accepted_round_v1"


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    manifest_path = ROOT / "fresh_campaign_manifest.json"
    manifest = read_json(manifest_path)
    for scene in SCENES:
        scene_dir = ROOT / scene
        for forbidden in ("algorithm_b_state.json", "algorithm_b.jsonl"):
            if (scene_dir / forbidden).exists():
                raise RuntimeError(
                    f"refusing metadata mutation after B start: {scene_dir / forbidden}"
                )
        config_path = scene_dir / "optimizer_config.json"
        config = read_json(config_path)
        shared = config.get("algorithm_c")
        if not isinstance(shared, dict):
            raise RuntimeError(f"shared framework missing: {scene}")
        workload = shared.get("workload_contract")
        if not isinstance(workload, dict):
            raise RuntimeError(f"workload contract missing: {scene}")
        workload["outer_qps_acceptance"] = {
            "contract": CONTRACT,
            "first_round_reference": "canonical_baseline",
            "subsequent_round_reference": "previous_accepted_round",
            "threshold_source": "outer_acceptance.min_db_qps_gain_pct",
            "resource_reference": "last_accepted_incumbent",
            "qps_noise_model_role": "required_audit_not_compounded_threshold",
            "rejected_round_updates_reference": False,
            "technical_failure_updates_reference": False,
        }
        atomic_json(config_path, config)
        manifest["scenes"][scene]["new_config_sha256"] = sha256(config_path)
        manifest["scenes"][scene]["preformal_metadata_sealed"] = True
    manifest["preformal_metadata_seal"] = {
        "contract": CONTRACT,
        "scope": "workload_contract.outer_qps_acceptance_metadata_only",
        "state_or_history_existed": False,
        "semantic_config_already_last_accepted": True,
    }
    atomic_json(manifest_path, manifest)
    print(str(manifest_path), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
