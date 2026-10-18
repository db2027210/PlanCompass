#!/usr/bin/env python3
"""Create fresh, non-overlapping configs for the three generic optimizer lanes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (
    ROOT
    / "config/formal/pg5_common_outer_bgallowed_20260722_v1/"
    "common_scoring_contract.json"
)
SCORING = {
    "overload_threshold": 0.8,
    "score_mode": "band_target",
    "resource_weights": {"cpu": 1, "memory": 1, "io": 3},
    "target_relative_resource_target_ranges": {
        "target_bottleneck": {
            "min_pct": 85,
            "max_pct": 90,
            "hard_overload_pct": 95,
        },
        "non_bottleneck": {
            "min_pct": 30,
            "max_pct": 50,
            "hard_overload_pct": 90,
        },
    },
}


def enforce_baseline_gate(data: Dict[str, Any], target_resource: str) -> None:
    ranges = {
        resource: {
            "min_pct": 85 if resource == target_resource else 30,
            "max_pct": 90 if resource == target_resource else 50,
        }
        for resource in ("cpu", "memory", "io")
    }
    data["baseline_resource_band_gate"] = {
        "enabled": True,
        "hard_fail": True,
        "stage": "after_stable_baseline_before_retrain_and_candidate_search",
        "target_resource": target_resource,
        "ranges_pct": ranges,
        "threshold_semantics": (
            "target inclusive 85-90%; non-target inclusive 30-50%; "
            "hard fail before candidate generation and real replay"
        ),
        "does_not_affect_outer_acceptance": True,
    }
    calibration = data.setdefault("bottleneck_calibration", {})
    calibration["resources"] = target_resource
    calibration["min_util_pct"] = 85
    calibration["target_util_pct"] = 90


def remove_non_score_inner_gates(data: Dict[str, Any]) -> None:
    """Inner T-SGD is guided only by the role-aware unified score."""
    gradient = data.setdefault("optimizer", {}).setdefault("gradient", {})
    for key in (
        "target_bottleneck_flat_band_decrease_pressure",
        "target_score_non_decrease_acceptance_enabled",
        "min_predicted_target_util_decrease",
        "min_predicted_non_target_util_increase",
    ):
        gradient.pop(key, None)


def replace(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: replace(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [replace(item, old, new) for item in value]
    if isinstance(value, str):
        return value.replace(old, new)
    return value


def prepare(source_config: Path, old_root: Path, new_root: Path) -> Path:
    if new_root.exists():
        raise RuntimeError(f"refusing to overwrite live attempt: {new_root}")
    new_root.mkdir(parents=True)
    for name in ("wrapper", "configs"):
        source = old_root / name
        if source.is_dir():
            shutil.copytree(source, new_root / name, dirs_exist_ok=True)
    data: Dict[str, Any] = json.loads(source_config.read_text(encoding="utf-8"))
    data = replace(data, str(old_root), str(new_root))
    data["scoring"] = SCORING
    target_resource = str(
        (data.get("baseline_resource_band_gate") or {}).get("target_resource")
        or (data.get("bottleneck_calibration") or {}).get("resources")
    )
    enforce_baseline_gate(data, target_resource)
    remove_non_score_inner_gates(data)
    digest = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    data["scoring_contract"] = {"path": str(CONTRACT), "sha256": digest}
    data["_common_scoring_contract"] = {
        "path": str(CONTRACT),
        "sha256": digest,
        "config_scoring_equals_contract": True,
    }
    data.pop("startup_baseline_reuse", None)
    outer = data.setdefault("formal_outer_protocol", {})
    for key in list(outer):
        if "resume_checkpoint" in key or key.startswith("resume_"):
            outer.pop(key)
    outer.update(
        {
            "formal_search_accepted_count_start": 0,
            "formal_search_replay_count_start": 1,
            "formal_search_accepted_target": 10,
            "accepted_shrink_target": 10,
            "formal_search_initial_replay_top_k": 5,
            "formal_search_second_replay_top_k": 10,
        }
    )
    for key in list(data):
        if key.startswith("_attempt") or "partial_initial" in key:
            data.pop(key)
    output = new_root / "config/live.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def prepare_remote_config(
    source_config: Path,
    old_root: str,
    new_root: str,
    output: Path,
) -> Path:
    data: Dict[str, Any] = json.loads(source_config.read_text(encoding="utf-8"))
    data = replace(data, old_root, new_root)
    data["scoring"] = SCORING
    target_resource = str(
        (data.get("baseline_resource_band_gate") or {}).get("target_resource")
        or (data.get("bottleneck_calibration") or {}).get("resources")
    )
    enforce_baseline_gate(data, target_resource)
    remove_non_score_inner_gates(data)
    digest = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    remote_contract = f"{new_root}/code/config/formal/common_scoring_contract.json"
    data["scoring_contract"] = {"path": remote_contract, "sha256": digest}
    data["_common_scoring_contract"] = {
        "path": remote_contract,
        "sha256": digest,
        "config_scoring_equals_contract": True,
    }
    outer = data.setdefault("formal_outer_protocol", {})
    outer.update(
        {
            "formal_search_accepted_count_start": 0,
            "formal_search_accepted_target": 10,
            "accepted_shrink_target": 10,
        }
    )
    output.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def apply_tpch_resource_contract(config_path: Path) -> None:
    """Keep TPC-H score denominators aligned with its Docker cgroup limits."""
    data: Dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
    data["_physical_resource_contract"].update(
        {
            "cpu_quota_cores": 6.32,
            "memory_bytes": 2281701376,
            "io_read_bps": 15834,
            "io_write_bps": 24000,
            "io_total_bps": 39834,
        }
    )
    data["capability"].update(
        {
            "cpu": 6.32,
            "memory": 2.125,
            "io": 0.03798866271972656,
        }
    )
    data["vm_resource"].update(
        {
            "cpu": 6.32,
            "memory_gb": 2.125,
            "read_mb": 0.015100479125976562,
            "write_mb": 0.02288818359375,
        }
    )
    config_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    xp_old = Path(
        "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
        "hybench_xp_compute00_59630/experience_sgd/"
        "attempt36_tsgd_v5_resume_rank2_target5_fullsql31_callback_v1"
    )
    tpcc_old = Path(
        "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
        "tpcc_experience_sgd/attempt9_partial_initial_target5_v1"
    )
    paths = {
        "xp": prepare(
            xp_old / "configs/experience_table_all_subset_sgd.resume.json",
            xp_old,
            xp_old.parent / "attempt37_live_target_relative_20260723",
        ),
        "tpcc": prepare(
            tpcc_old / "config/tpcc_experience_table_all_subset_sgd.json",
            tpcc_old,
            tpcc_old.parent / "attempt10_live_target_relative_20260723",
        ),
        "tpch": prepare_remote_config(
            ROOT / "preflight/tpch_attempt45_tsgd_config.json",
            (
                "/mnt/nvme2n1/anonymous/resource_adaptive/"
                "pg5_common_outer_bgallowed_20260722_v1/tpch_node04/"
                "experience_sgd/attempt45_tsgd_eta010_target5_v1"
            ),
            (
                "/mnt/nvme2n1/anonymous/resource_adaptive/"
                "pg5_common_outer_bgallowed_20260722_v1/tpch_node04/"
                "experience_sgd/attempt46_live_target_relative_20260723"
            ),
            ROOT / "preflight/tpch_attempt46_live_config.json",
        ),
    }
    apply_tpch_resource_contract(paths["tpch"])
    print(json.dumps({key: str(value) for key, value in paths.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
