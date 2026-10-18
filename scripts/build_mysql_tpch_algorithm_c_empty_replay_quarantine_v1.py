#!/usr/bin/env python3
"""Bind an empty replay quarantine to a TPC-H C boundary checkpoint."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping


SCENES = {
    "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular evidence required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--empty-template", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output.parent.name) is None:
        parser.error("unsafe output parent")
    template_path = args.empty_template.resolve()
    checkpoint_path = args.checkpoint.resolve()
    template = load(template_path)
    checkpoint = load(checkpoint_path)
    key_fields = (
        "quarantined_param_keys_by_scene",
        "soft_replayed_param_keys_by_scene",
        "permanent_invalid_param_keys_by_scene",
    )
    row_fields = ("rows_by_scene",)
    count_fields = (
        "quarantined_counts_by_scene",
        "soft_replayed_counts_by_scene",
        "permanent_invalid_counts_by_scene",
    )
    if (
        template.get("schema_version") != "mysql_algorithm_c_replay_quarantine_v6"
        or template.get("status") != "PASS"
        or template.get("formal_state_mutation") is not False
        or any(
            set(template.get(field) or {}) != SCENES
            or any((template.get(field) or {})[scene] for scene in SCENES)
            for field in key_fields + row_fields
        )
        or any(
            set(template.get(field) or {}) != SCENES
            or any(int((template.get(field) or {})[scene]) != 0 for scene in SCENES)
            for field in count_fields
        )
        or checkpoint.get("status") != "PREPARED"
        or checkpoint.get("formal_scope") != "mysql_only"
        or checkpoint.get("cross_engine_comparison_prohibited") is not True
        or int(checkpoint.get("completed_outer_attempts") or 0) < 1
        or checkpoint.get("freeze_sha256") != template.get("freeze_sha256")
    ):
        raise RuntimeError("TPC-H empty replay quarantine authority differs")
    completed = int(checkpoint["completed_outer_attempts"])
    document = dict(template)
    document.update({
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "resume_checkpoint_outer_attempt": completed,
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": sha(Path(__file__).resolve()),
        "formal_state_mutation": False,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "checkpoint_owns_completed_replay_history": True,
        "empty_quarantine_does_not_clear_checkpoint_replay_dedup": True,
        "source_empty_template": {
            "path": str(template_path), "sha256": sha(template_path),
        },
        "resume_checkpoint": {
            "path": str(checkpoint_path), "sha256": sha(checkpoint_path),
        },
    })
    evidence = dict(document.get("evidence") or {})
    evidence["checkpoint"] = {
        "path": str(checkpoint_path), "sha256": sha(checkpoint_path),
    }
    evidence["source_empty_template"] = {
        "path": str(template_path), "sha256": sha(template_path),
    }
    document["evidence"] = evidence
    args.output.parent.mkdir(parents=True, mode=0o700, exist_ok=False)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.output.with_name(args.output.name + ".sha256").write_text(
        f"{sha(args.output)}  {args.output.name}\n", encoding="ascii",
    )
    print(json.dumps({
        "status": "PASS", "output": str(args.output),
        "sha256": sha(args.output), "completed_outer_attempts": completed,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
