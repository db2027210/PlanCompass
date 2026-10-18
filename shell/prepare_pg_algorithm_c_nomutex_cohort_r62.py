#!/usr/bin/env python3
"""Create a fresh r62 campaign from r61's immutable configs only."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict


SOURCE_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260813_algorithm_c_cached_tier_redecision_clean_r61"
)
TARGET_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260813_algorithm_c_same_plan_numeric_clean_r62"
)
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    if TARGET_ROOT.exists() and any(TARGET_ROOT.iterdir()):
        raise RuntimeError(f"r62 target already exists and is non-empty: {TARGET_ROOT}")
    TARGET_ROOT.mkdir(parents=True, exist_ok=True)
    manifest: Dict[str, Any] = {
        "contract": "algorithm_c_fresh_config_only_campaign_v1",
        "source_root": str(SOURCE_ROOT),
        "campaign_root": str(TARGET_ROOT),
        "copied_config_only": True,
        "copied_state": False,
        "copied_history": False,
        "copied_replays": False,
        "fresh_state": True,
        "predictor_artifacts_unchanged": True,
        "unadmitted_refit_artifacts_used": False,
        "same_plan_numeric_refinement_release": True,
        "scenes": {},
    }
    for scene in SCENES:
        source = SOURCE_ROOT / scene / "optimizer_config.json"
        if not source.is_file():
            raise FileNotFoundError(source)
        payload = json.loads(source.read_text(encoding="utf-8"))
        target_dir = TARGET_ROOT / scene
        target_dir.mkdir(parents=True, exist_ok=False)
        target = target_dir / "optimizer_config.json"
        atomic_json(target, payload)
        algorithm = payload.get("algorithm_c") or {}
        manifest["scenes"][scene] = {
            "source_config": str(source),
            "source_config_sha256": sha256(source),
            "config": str(target),
            "config_sha256": sha256(target),
            "config_semantically_identical": (
                json.loads(source.read_text(encoding="utf-8"))
                == json.loads(target.read_text(encoding="utf-8"))
            ),
            "accepted_target": int(algorithm.get("accepted_target") or 0),
            "normalized_width_threshold": float(
                algorithm.get("normalized_width_threshold") or 0.0
            ),
            "experience_table_path": str(
                algorithm.get("experience_table_path") or ""
            ),
            "state_exists_before_launch": (
                target_dir / "algorithm_c_state.json"
            ).exists(),
            "history_exists_before_launch": (
                target_dir / "algorithm_c.jsonl"
            ).exists(),
        }
    atomic_json(TARGET_ROOT / "fresh_campaign_manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
