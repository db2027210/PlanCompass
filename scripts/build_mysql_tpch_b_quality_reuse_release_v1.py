#!/usr/bin/env python3
"""Freeze deterministic quality-gate reuse for the TPC-H B continuation."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "config/formal/frozen_mysql_tpch_b_quality_reuse_20260830_v3"
QUALITY = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v1125_to_b_tpch_client_qps_disk2_normalized_20260830_tpch"
    / "fresh_calibration/tpch_sf4_memory/fresh_model_quality_gate.json"
)
SOURCES = {
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_tpch_algorithm_b_timeout_resume_quality_reuse_v2.py",
    "timeout_resume_controller": ROOT / "scripts/run_mysql_tpch_algorithm_b_timeout_resume_v1.py",
    "quality_policy": ROOT / "mysql_algorithm_b_policy_v2.py",
    "quality_model": Path("/home/anonymous/program/resource_adaptive/double_loop_scenario.py"),
    "quality_gate": QUALITY,
    "calibration_design": (
        ROOT / "history/mysql_four_scene_20260809"
        / "v1125_to_b_tpch_client_qps_disk2_normalized_20260830_tpch"
        / "calibration_design.json"
    ),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh quality-reuse release required: {OUTPUT}")
    for path in SOURCES.values():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"quality-reuse source absent: {path}")
    quality = json.loads(QUALITY.read_text(encoding="utf-8"))
    if not (
        quality.get("passed") is False
        and quality.get("preflight_soft_start") is True
        and quality.get("frozen_design_override_consumed") is True
        and int(quality.get("calibration_replay_attempt_count") or 0) == 15
        and len(quality.get("calibration_design") or []) == 15
    ):
        raise RuntimeError("quality-reuse source semantics differ")
    contract = {
        "schema_version": "mysql_tpch_algorithm_b_quality_reuse_v3",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "quality_gate_replay_required": False,
        "calibration_measurement_replay_required": False,
        "quality_gate_source_path": str(QUALITY.relative_to(ROOT)),
        "quality_gate_source_sha256": sha(QUALITY),
        "quality_gate_passed": False,
        "preflight_soft_start_preserved": True,
        "model_reconstruction": (
            "baseline_plus_bit_identical_calibration_payloads_with_default_idw_mode"
        ),
        "source_bindings": {
            name: {
                "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                "sha256": sha(path),
            }
            for name, path in sorted(SOURCES.items())
        },
    }
    atomic(OUTPUT / "algorithm_b_quality_reuse_contract.json", contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "contract": str(OUTPUT / "algorithm_b_quality_reuse_contract.json"),
        "contract_sha256": sha(OUTPUT / "algorithm_b_quality_reuse_contract.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
