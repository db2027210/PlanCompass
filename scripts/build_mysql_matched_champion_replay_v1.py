#!/usr/bin/env python3
"""Freeze the second matched champion replay after matched baselines pass."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_contaminated_champion_replay_20260829_v2/champion_replay_contract.json"
AUTHORITY_ROOT = ROOT / "history/mysql_four_scene_20260809/v1079_matched_baseline_live_authority_20260829"
BASELINE_RESULTS = {
    "job_memory": ROOT / "history/mysql_four_scene_20260809/v1096_matched_baseline_replay_v8_job_20260829/baseline_replay_result.json",
    "tpch_sf4_memory": ROOT / "history/mysql_four_scene_20260809/v1081_matched_baseline_replay_tpch_20260829/baseline_replay_result.json",
    "hybench_xp_cpu": ROOT / "history/mysql_four_scene_20260809/v1091_matched_baseline_replay_v5_xp_cpu_20260829/baseline_replay_result.json",
    "hybench_xp_io": ROOT / "history/mysql_four_scene_20260809/v1095_matched_baseline_replay_v7_xp_io_20260829/baseline_replay_result.json",
}


def sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bind(path: Path) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenes", required=True, help="comma-separated formal scene names")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    selected_scenes = tuple(dict.fromkeys(part.strip() for part in args.scenes.split(",") if part.strip()))
    unknown = sorted(set(selected_scenes) - set(BASELINE_RESULTS))
    if not selected_scenes or unknown:
        raise RuntimeError(f"invalid selected scenes: {unknown or selected_scenes}")
    out = args.output_dir.resolve()
    contract_path = out / "champion_replay_contract.json"
    if out.exists() or out.is_symlink():
        raise RuntimeError(f"fresh replay contract required: {out}")
    live_path = AUTHORITY_ROOT / "live_authority.json"
    live = json.loads(live_path.read_text(encoding="utf-8"))
    if live.get("status") != "PASS" or live.get("formal_launch_authorized") is not True:
        raise RuntimeError("restored live authority is not launchable")
    for scene in selected_scenes:
        path = BASELINE_RESULTS[scene]
        baseline = json.loads(path.read_text(encoding="utf-8"))
        if baseline.get("status") != "PASS" or baseline.get("baseline_replayed") is not True:
            raise RuntimeError(f"matched baseline is not complete: {scene}")
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    document.update({
        "schema_version": "mysql_matched_champion_replay_contract_v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "predecessor_contract": bind(SOURCE),
        "purpose": "repeat selected champions on the exact workers/devices used by corrected matched baselines",
        "selected_scenes": list(selected_scenes),
        "baseline_replayed": False,
        "champion_replayed": True,
        "algorithm_search_reexecuted": False,
        "accepted_state_credit_increment": 0,
        "historical_artifacts_mutated": False,
        "xp_reset_cross_scene_lock_used": False,
        "matched_baseline_result_evidence": {
            scene: bind(BASELINE_RESULTS[scene]) for scene in selected_scenes
        },
        "authority": {
            "manifest_path": str(live_path), "manifest_sha256": sha(live_path),
            "prepared_path": live["artifacts"]["prepared"],
            "prepared_sha256": live["artifacts_sha256"]["prepared"],
            "preflight_path": live["artifacts"]["preflight"],
            "preflight_sha256": live["artifacts_sha256"]["preflight"],
            "accounts_path": live["artifacts"]["accounts"],
            "accounts_sha256": live["artifacts_sha256"]["accounts"],
        },
        "resource_freeze_by_scene": live["resource_freeze_by_scene"],
    })
    document.pop("resource_freeze", None)
    document["assignments_by_scene"]["hybench_xp_io"] = {
        "xp_io_w1": "b_io", "xp_io_w2": "c_io",
    }
    document["assignments_by_scene"] = {
        scene: document["assignments_by_scene"][scene] for scene in selected_scenes
    }
    document["resource_freeze_by_scene"] = {
        scene: document["resource_freeze_by_scene"][scene] for scene in selected_scenes
    }
    document["source_bindings"].update({
        "builder": bind(Path(__file__).resolve()),
        "runner": bind(ROOT / "scripts/run_mysql_matched_champion_replay_v1.py"),
        "single_scene_measurement": bind(ROOT / "scripts/mysql_single_scene_measurement_v2.py"),
        "parallel_xp_reset": bind(ROOT / "scripts/mysql_async_xp_post_reset_settle_v2.py"),
        "dynamic_gate": bind(ROOT / "scripts/mysql_cross_project_wave_coordination_v24.py"),
        "coordination_contract": bind(
            ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260829_v76/coordination_contract.json"
        ),
        "authority_sealer": bind(ROOT / "scripts/seal_mysql_baseline_replay_authority_v6.py"),
    })
    out.mkdir(parents=True, exist_ok=False)
    temporary = contract_path.with_name(f".{contract_path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, contract_path)
    contract_path.with_name(contract_path.name + ".sha256").write_text(
        f"{sha(contract_path)}  {contract_path.name}\n", encoding="ascii",
    )
    print(json.dumps({"status": "PASS", "contract": str(contract_path), "sha256": sha(contract_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
