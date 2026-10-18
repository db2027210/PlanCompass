#!/usr/bin/env python3
"""Build an empty v6 quarantine owned by a reconstructed C checkpoint."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


SCENES = ("job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular evidence required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def build(checkpoint: Mapping[str, Any], checkpoint_path: Path) -> dict[str, Any]:
    completed = int(checkpoint.get("completed_outer_attempts") or 0)
    reconstructed = checkpoint.get("reconstructed_state") or {}
    replayed = reconstructed.get("replayed_param_keys_by_scene") or {}
    if (
        checkpoint.get("status") != "PREPARED"
        or completed < 1
        or int(reconstructed.get("outer_attempt") or 0) != completed
        or set(replayed) != set(SCENES)
        or not checkpoint.get("freeze_sha256")
    ):
        raise RuntimeError("transition checkpoint quarantine authority differs")
    empty = {scene: [] for scene in SCENES}
    counts = {scene: 0 for scene in SCENES}
    return {
        "schema_version": "mysql_algorithm_c_replay_quarantine_v6",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_sha256": checkpoint["freeze_sha256"],
        "resume_checkpoint_outer_attempt": completed,
        "quarantined_param_keys_by_scene": empty,
        "quarantined_counts_by_scene": counts,
        "soft_replayed_param_keys_by_scene": {scene: [] for scene in SCENES},
        "soft_replayed_counts_by_scene": dict(counts),
        "permanent_invalid_param_keys_by_scene": {scene: [] for scene in SCENES},
        "permanent_invalid_counts_by_scene": dict(counts),
        "rows_by_scene": {scene: [] for scene in SCENES},
        "cycle_reset_may_clear_soft_replayed": True,
        "cycle_reset_may_clear_permanent_invalid": False,
        "formal_state_mutation": False,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "checkpoint_replayed_param_keys_owned_by_reconstructed_state": {
            scene: len(replayed[scene]) for scene in SCENES
        },
        "evidence": {
            "checkpoint": {
                "path": str(checkpoint_path),
                "sha256": sha(checkpoint_path),
            }
        },
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": sha(Path(__file__).resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    checkpoint_path = args.checkpoint.resolve()
    output = args.output.resolve()
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        parser.error("fresh output beneath an existing transition root required")
    document = build(load(checkpoint_path), checkpoint_path)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_name(output.name + ".sha256").write_text(
        f"{sha(output)}  {output.name}\n", encoding="ascii",
    )
    print(json.dumps({"status": "PASS", "output": str(output), "sha256": sha(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
