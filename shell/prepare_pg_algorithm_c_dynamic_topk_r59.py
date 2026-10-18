#!/usr/bin/env python3
"""Prepare a fresh four-lane Algorithm C dynamic-Top-K campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict


SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
REQUIRED_CONFIG_KEYS = {
    "accepted_target": 7,
    "normalized_width_threshold": 0.01,
    "stability_confirmation_rounds": 2,
}
OUTER_PRUNING_CONTRACT = "algorithm_c_outer_accept_reject_plan_context_v2"


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    args = parser.parse_args()
    source_root = args.source_root.resolve()
    campaign_root = args.campaign_root.resolve()
    if campaign_root.exists():
        raise RuntimeError(f"fresh campaign root already exists: {campaign_root}")

    campaign_root.mkdir(parents=True, mode=0o700)
    manifest: Dict[str, Any] = {
        "contract": "algorithm_c_dynamic_topk_fresh_campaign_v2",
        "source_root": str(source_root),
        "campaign_root": str(campaign_root),
        "fresh_state": True,
        "copied_state": False,
        "copied_replays": False,
        "copied_history": False,
        "scenes": {},
    }
    for scene in SCENES:
        source_config = source_root / scene / "optimizer_config.json"
        if not source_config.is_file():
            raise FileNotFoundError(source_config)
        cfg = read_json(source_config)
        algorithm_cfg = cfg.get("algorithm_c") or {}
        for key, expected in REQUIRED_CONFIG_KEYS.items():
            if algorithm_cfg.get(key) != expected:
                raise RuntimeError(
                    f"{scene} {key} mismatch: "
                    f"{algorithm_cfg.get(key)!r} != {expected!r}"
                )
        if algorithm_cfg.get("operator_resource_pruning_enabled") is not False:
            raise RuntimeError(f"{scene} operator-resource pruning is not disabled")
        pruning_cfg = algorithm_cfg.get("outer_accept_reject_pruning")
        workload_contract = algorithm_cfg.get("workload_contract")
        workload_pruning = (
            workload_contract.get("outer_accept_reject_pruning")
            if isinstance(workload_contract, dict)
            else None
        )
        if not isinstance(pruning_cfg, dict) or not isinstance(workload_pruning, dict):
            raise RuntimeError(f"{scene} missing outer pruning contract objects")
        source_contracts = {
            "algorithm_c.outer_accept_reject_pruning.contract": pruning_cfg.get(
                "contract"
            ),
            "algorithm_c.workload_contract.outer_accept_reject_pruning.contract": (
                workload_pruning.get("contract")
            ),
        }
        pruning_cfg["contract"] = OUTER_PRUNING_CONTRACT
        workload_pruning["contract"] = OUTER_PRUNING_CONTRACT
        scene_root = campaign_root / scene
        scene_root.mkdir(mode=0o700)
        target = scene_root / "optimizer_config.json"
        target.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(target, 0o600)
        manifest["scenes"][scene] = {
            "config_path": str(target),
            "config_sha256": sha256(target),
            "source_config_path": str(source_config),
            "source_config_sha256": sha256(source_config),
            "source_outer_pruning_contracts": source_contracts,
            "target_outer_pruning_contract": OUTER_PRUNING_CONTRACT,
            "config_mutations": [
                "algorithm_c.outer_accept_reject_pruning.contract",
                "algorithm_c.workload_contract.outer_accept_reject_pruning.contract",
            ],
            "state_path_exists": (scene_root / "algorithm_c_state.json").exists(),
            "history_path_exists": (scene_root / "algorithm_c.jsonl").exists(),
            "accepted_target": algorithm_cfg.get("accepted_target"),
            "normalized_width_threshold": algorithm_cfg.get(
                "normalized_width_threshold"
            ),
            "stability_confirmation_rounds": algorithm_cfg.get(
                "stability_confirmation_rounds"
            ),
        }
    (campaign_root / "cohort_support").mkdir(mode=0o700)
    manifest_path = campaign_root / "fresh_campaign_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(manifest_path, 0o600)
    print(str(manifest_path), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
