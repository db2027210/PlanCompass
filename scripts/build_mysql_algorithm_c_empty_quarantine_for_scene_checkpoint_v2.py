#!/usr/bin/env python3
"""Build an empty replay quarantine bound to a v18 scene checkpoint."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v18 as verifier


SCENES = ("job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io")
PRUNING = (
    ROOT / "config/formal/mysql_algorithm_c_plan_replay_pruning_20260813_v2"
    / "contract.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound(path: Path) -> dict[str, str]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular evidence required: {path}")
    return {"path": str(path), "sha256": sha(path)}


def atomic(path: Path, value: Mapping[str, Any]) -> None:
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise RuntimeError("fresh quarantine output required")
    checkpoint_path = args.checkpoint.resolve()
    checkpoint = verifier.verify_checkpoint(checkpoint_path)
    freeze = Path(str(checkpoint["freeze_path"])).resolve()
    completed = int(checkpoint["completed_outer_attempts"])
    if completed < 1 or checkpoint.get("formal_scope") != "mysql_only":
        raise RuntimeError("independent checkpoint boundary differs")
    empty = {scene: [] for scene in SCENES}
    zeros = {scene: 0 for scene in SCENES}
    document = {
        "schema_version": "mysql_algorithm_c_replay_quarantine_v6",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_sha256": str(checkpoint["freeze_sha256"]),
        "resume_checkpoint_outer_attempt": completed,
        "formal_state_mutation": False,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "quarantined_counts_by_scene": zeros,
        "soft_replayed_counts_by_scene": dict(zeros),
        "permanent_invalid_counts_by_scene": dict(zeros),
        "quarantined_param_keys_by_scene": empty,
        "soft_replayed_param_keys_by_scene": {scene: [] for scene in SCENES},
        "permanent_invalid_param_keys_by_scene": {scene: [] for scene in SCENES},
        "rows_by_scene": {scene: [] for scene in SCENES},
        "cycle_reset_may_clear_soft_replayed": True,
        "cycle_reset_may_clear_permanent_invalid": False,
        "initialization": "empty_independent_checkpoint_resume",
        "pre_v18_timeout_and_replay_evidence_imported": False,
        "evidence": {
            "checkpoint": bound(checkpoint_path),
            "resource_freeze": bound(freeze),
            "pruning_contract": bound(PRUNING),
        },
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": sha(Path(__file__).resolve()),
    }
    args.output_dir.mkdir(parents=True, mode=0o700)
    output = args.output_dir / "replay_quarantine.json"
    atomic(output, document)
    print(json.dumps({
        "status": "PASS", "output": str(output),
        "completed_outer_attempts": completed, "sha256": sha(output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
