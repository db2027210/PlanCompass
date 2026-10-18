#!/usr/bin/env python3
"""Bind scene-equivalent PASS baselines to the v36 four-scene R0 set."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260923_v36/"
    "four_scene_effective_resource_successor.json"
)
OUTPUT = HISTORY / "c_v152_formal_r0_set_v36_20260923/formal_r0_set.json"
REPLICATED_XP_IO_GATE = HISTORY / (
    "c_v152_xp_io_replicated_r0_20260923/replicated_resource_gate.json"
)
SOURCES = {
    "job_memory": ("c_v150_paper_baseline_calibration_20260923_job", "single_run"),
    "tpch_sf4_memory": ("c_v148_paper_baseline_calibration_20260923_tpch", "single_run"),
    "hybench_xp_cpu": ("c_v149_paper_baseline_calibration_20260923_xp_cpu", "single_run"),
    "hybench_xp_io": ("c_v150_paper_baseline_calibration_20260923_xp_io", "replicated_mean"),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def canonical_sha(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def scene_projection(freeze: dict[str, Any], scene: str) -> dict[str, Any]:
    value = {
        "formal_scope": freeze.get("formal_scope"),
        "cross_engine_comparison_prohibited": freeze.get(
            "cross_engine_comparison_prohibited"
        ),
        "scene": scene,
        "storage": freeze.get("storage"),
        "template": (freeze.get("templates") or {}).get(scene),
        "metric_semantics": (freeze.get("metric_semantics") or {}).get(scene),
        "workers": sorted(
            [row for row in freeze.get("workers") or [] if row.get("scene") == scene],
            key=lambda row: str(row.get("worker_id")),
        ),
    }
    if scene.startswith("hybench_xp_"):
        value["native_xp_contract"] = freeze.get("native_xp_contract")
        value["xp_innodb_io_profile"] = freeze.get("xp_innodb_io_profile")
    if scene == "tpch_sf4_memory":
        value["tpch_sf4_storage_manifest"] = freeze.get("tpch_sf4_storage_manifest")
    if len(value["workers"]) != 2:
        raise RuntimeError(f"two frozen workers required: {scene}")
    return value


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    if OUTPUT.parent.exists() or OUTPUT.parent.is_symlink():
        raise RuntimeError(f"fresh formal R0 set output required: {OUTPUT.parent}")
    target_freeze = load(FREEZE)
    target_freeze_sha = sha(FREEZE)
    replicated = load(REPLICATED_XP_IO_GATE)
    if (
        replicated.get("status") != "PASS"
        or replicated.get("candidate_search_authorized") is not True
        or replicated.get("scene") != "hybench_xp_io"
        or replicated.get("freeze_sha256") != target_freeze_sha
    ):
        raise RuntimeError("XP/I/O replicated resource gate differs")
    rows = {}
    for scene, (run_name, mode) in SOURCES.items():
        run = HISTORY / run_name
        state_path = run / "run_state.json"
        batch_path = run / "formal_incumbent_baseline/batch_result.json"
        single_gate_path = run / "paper_baseline_resource_gate.json"
        state, batch, single_gate = map(load, (state_path, batch_path, single_gate_path))
        source_freeze_path = Path(str(state.get("freeze_path") or ""))
        if (
            not source_freeze_path.is_file()
            or source_freeze_path.is_symlink()
            or sha(source_freeze_path) != state.get("freeze_sha256")
            or batch.get("status") != "PASS"
            or batch.get("scene") != scene
            or batch.get("formal_scope") != "mysql_only"
            or batch.get("cross_engine_comparison_prohibited") is not True
            or int((state.get("accepted_rounds") or {}).get(scene, 0)) != 0
        ):
            raise RuntimeError(f"formal R0 source differs: {scene}")
        source_freeze = load(source_freeze_path)
        source_projection = scene_projection(source_freeze, scene)
        target_projection = scene_projection(target_freeze, scene)
        source_projection_sha = canonical_sha(source_projection)
        target_projection_sha = canonical_sha(target_projection)
        if source_projection_sha != target_projection_sha:
            raise RuntimeError(f"scene-local frozen resources differ: {scene}")
        if mode == "single_run":
            if (
                state.get("status") != "COMPLETE"
                or single_gate.get("status") != "PASS"
                or single_gate.get("candidate_search_authorized") is not True
            ):
                raise RuntimeError(f"single-run formal gate differs: {scene}")
            gate_path = single_gate_path
            gate_status = "PASS"
        else:
            if (
                state.get("status") != "FAILED_CLOSED"
                or single_gate.get("status") != "FAILED_CLOSED"
                or not str(state.get("error") or "").startswith(
                    "RuntimeError: Algorithm C paper baseline resource gate failed:"
                )
            ):
                raise RuntimeError("XP/I/O superseded single-run failure differs")
            gate_path = REPLICATED_XP_IO_GATE
            gate_status = "PASS"
        rows[scene] = {
            "baseline_acceptance_mode": mode,
            "batch_result_path": str(batch_path.resolve()),
            "batch_result_sha256": sha(batch_path),
            "paper_gate_path": str(gate_path.resolve()),
            "paper_gate_sha256": sha(gate_path),
            "paper_gate_status": gate_status,
            "source_single_run_gate_path": str(single_gate_path.resolve()),
            "source_single_run_gate_sha256": sha(single_gate_path),
            "source_single_run_gate_status": single_gate.get("status"),
            "run_state_path": str(state_path.resolve()),
            "run_state_sha256": sha(state_path),
            "source_run_state_status": state.get("status"),
            "scene_resource_equivalence": {
                "source_freeze_path": str(source_freeze_path.resolve()),
                "source_freeze_sha256": sha(source_freeze_path),
                "target_freeze_path": str(FREEZE.resolve()),
                "target_freeze_sha256": target_freeze_sha,
                "source_scene_projection_sha256": source_projection_sha,
                "target_scene_projection_sha256": target_projection_sha,
                "equal": True,
            },
        }
    atomic(OUTPUT, {
        "schema_version": "mysql_algorithm_c_scene_equivalent_formal_r0_set_v1",
        "status": "PASS",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE.resolve()),
        "freeze_sha256": target_freeze_sha,
        "scene_local_resource_equivalence_required": True,
        "cross_scene_baseline_credit_prohibited": True,
        "replicated_mean_resource_gate_allowed": True,
        "scenes": rows,
    })
    print(json.dumps({"status": "PASS", "path": str(OUTPUT), "sha256": sha(OUTPUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
