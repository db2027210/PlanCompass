#!/usr/bin/env python3
"""Advance an unchanged empty v6 replay quarantine by one completed outer."""

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
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output.parent.name) is None:
        parser.error("unsafe output parent")

    prior_path = args.prior.resolve()
    checkpoint_path = args.checkpoint.resolve()
    prior = load(prior_path)
    checkpoint = load(checkpoint_path)
    prior_outer = int(prior.get("resume_checkpoint_outer_attempt") or 0)
    completed_outer = int(checkpoint.get("completed_outer_attempts") or 0)
    mappings = [
        prior.get("quarantined_param_keys_by_scene") or {},
        prior.get("soft_replayed_param_keys_by_scene") or {},
        prior.get("permanent_invalid_param_keys_by_scene") or {},
    ]
    if (
        prior.get("schema_version") != "mysql_algorithm_c_replay_quarantine_v6"
        or prior.get("status") != "PASS"
        or prior_outer < 1
        or completed_outer != prior_outer + 1
        or prior.get("formal_state_mutation") is not False
        or any(set(mapping) != SCENES for mapping in mappings)
        or any(mapping[scene] for mapping in mappings for scene in SCENES)
        or checkpoint.get("status") != "PREPARED"
        or int((checkpoint.get("reconstructed_state") or {}).get("outer_attempt") or 0)
        != completed_outer
        or checkpoint.get("freeze_sha256") != prior.get("freeze_sha256")
    ):
        raise RuntimeError("empty quarantine successor authority differs")

    document = dict(prior)
    document.update({
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "resume_checkpoint_outer_attempt": completed_outer,
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": sha(Path(__file__).resolve()),
        "formal_state_mutation": False,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        f"outer{completed_outer}_completed_outcomes_owned_by_checkpoint": True,
        "predecessor": {"path": str(prior_path), "sha256": sha(prior_path)},
        "resume_checkpoint": {
            "path": str(checkpoint_path), "sha256": sha(checkpoint_path),
        },
    })
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
        "sha256": sha(args.output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
