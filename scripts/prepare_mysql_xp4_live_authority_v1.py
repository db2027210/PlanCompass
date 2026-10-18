#!/usr/bin/env python3
"""Provision four-worker XP cohorts from completed memory-scene capacity."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_c_xp4_scene_handoff_v1 as handoff


HISTORY = ROOT / "history/mysql_four_scene_20260809"
MANIFEST = HISTORY / "v164_to_xp4_capacity_handoff_20260826/transition_manifest.json"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_xp4_handoff_20260826_v2"
    / "xp_four_worker_resource_successor.json"
)
OUTPUT = HISTORY / "v164_to_xp4_live_authority_20260826"
SOURCES = {
    "hybench_xp_cpu": HISTORY / "v59_to_c_xp_top4_20260825_attempt033_cpu_zero_safe_xp_cpu",
    "hybench_xp_io": HISTORY / "v59_to_c_xp_top4_20260825_attempt034_io_zero_safe_xp_io",
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def prepare(scene: str, freeze: Mapping[str, Any]) -> dict[str, Any]:
    output = OUTPUT / scene
    result_path = output / "live_authority.json"
    if result_path.is_file():
        result = load(result_path)
        if result.get("status") == "PASS":
            return result
    if output.exists() and not result_path.is_file():
        raise RuntimeError(f"incomplete fresh XP4 authority root: {output}")
    output.mkdir(parents=True, mode=0o700)
    original = handoff.original_live_workers(
        scene=scene, source_root=SOURCES[scene], freeze=freeze,
    )
    additions = handoff.provision_new_workers(
        scene=scene, freeze=freeze, output=output / "provisioned_workers",
    )
    workers = {**original, **additions}
    artifacts = handoff.authority_artifacts(
        scene=scene, freeze_path=FREEZE, freeze=freeze,
        live_rows=workers, output=output / "authority",
    )
    result = {
        "schema_version": "mysql_xp_four_worker_live_authority_v1",
        "status": "PASS", "created_at": now(), "scene": scene,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "resource_freeze_path": str(FREEZE),
        "resource_freeze_sha256": sha(FREEZE),
        "worker_ids": sorted(workers),
        "physical_replay_parallelism": 4,
        "candidate_batches": [[1, 2, 3, 4], [5]],
        "native_xp_workload_unchanged": True,
        "recalibration_performed": False,
        "accepted_state_credit_increment": 0,
        "artifacts": {name: str(path) for name, path in artifacts.items()},
    }
    atomic(result_path, result)
    return result


def main() -> int:
    manifest = load(MANIFEST)
    freeze = load(FREEZE)
    if (
        manifest.get("status") != "CAPACITY_READY"
        or manifest.get("memory_scenes_complete") is not True
        or manifest.get("memory_worker_containers_stopped") is not True
        or manifest.get("successor_freeze_sha256") != sha(FREEZE)
        or freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("XP4 capacity authority differs")
    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xp4-authority") as pool:
        futures = {pool.submit(prepare, scene, freeze): scene for scene in SOURCES}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    summary = {
        "schema_version": "mysql_xp_four_worker_live_authority_summary_v1",
        "status": "PASS", "created_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "scenes": results, "four_scene_barrier": False,
        "sibling_scene_wait": False,
    }
    atomic(OUTPUT / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
