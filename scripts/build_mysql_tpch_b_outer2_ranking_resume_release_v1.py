#!/usr/bin/env python3
"""Freeze partial Outer-2 ranking reuse for the TPC-H B continuation."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "config/formal/frozen_mysql_tpch_b_outer2_ranking_resume_20260830_v4"
SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v1125_to_b_tpch_client_qps_disk2_normalized_20260830_tpch"
)
RANKINGS = SOURCE / "outer_002/predicted_rankings.json"
SOURCES = {
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_tpch_algorithm_b_timeout_resume_frozen_outer2_v3.py",
    "quality_reuse_controller": ROOT / "scripts/run_mysql_tpch_algorithm_b_timeout_resume_quality_reuse_v2.py",
    "timeout_resume_controller": ROOT / "scripts/run_mysql_tpch_algorithm_b_timeout_resume_v1.py",
    "outer2_rankings": RANKINGS,
    "outer2_valid_rank1": (
        ROOT / "history/mysql_four_scene_20260809"
        / "v1128_tpch_b_outer2_timeout_recovery_evidence_20260830"
        / "rank_1/batch_result.json"
    ),
    "outer2_invalid_rank2": (
        ROOT / "history/mysql_four_scene_20260809"
        / "v1128_tpch_b_outer2_timeout_recovery_evidence_20260830"
        / "rank_2/batch_result.json"
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
        raise RuntimeError(f"fresh Outer-2 ranking release required: {OUTPUT}")
    for path in SOURCES.values():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"Outer-2 ranking source absent: {path}")
    rankings = json.loads(RANKINGS.read_text(encoding="utf-8"))
    selected = (rankings.get("selected_top_k") or {}).get("tpch_sf4_memory") or []
    if rankings.get("outer_attempt") != 2 or len(selected) != 5:
        raise RuntimeError("frozen Outer-2 ranking identity differs")
    contract = {
        "schema_version": "mysql_tpch_algorithm_b_outer2_ranking_resume_v4",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "frozen_partial_outer": 2,
        "frozen_ranking_recomputed": False,
        "frozen_ranking_path": str(RANKINGS.relative_to(ROOT)),
        "frozen_ranking_sha256": sha(RANKINGS),
        "completed_outer_state_reused_through": 1,
        "valid_rank_reused": 1,
        "invalid_timeout_rank_preserved_without_measurement_credit": 2,
        "next_physical_candidate_wave": [3, 4],
        "source_bindings": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
            for name, path in sorted(SOURCES.items())
        },
    }
    atomic(OUTPUT / "algorithm_b_outer2_ranking_resume_contract.json", contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "contract": str(OUTPUT / "algorithm_b_outer2_ranking_resume_contract.json"),
        "contract_sha256": sha(OUTPUT / "algorithm_b_outer2_ranking_resume_contract.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
