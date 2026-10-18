#!/usr/bin/env python3
"""Rebind a zero-mutation replay quarantine to a chained checkpoint."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
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

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v15 as checkpoint_v15


SCENE = "tpch_sf4_memory"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


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
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def build(checkpoint_path: Path, inherited_path: Path) -> dict[str, Any]:
    checkpoint_path = checkpoint_path.resolve()
    inherited_path = inherited_path.resolve()
    checkpoint = checkpoint_v15.verify_checkpoint(checkpoint_path)
    inherited = load(inherited_path)
    keys = (
        inherited.get("permanent_invalid_param_keys_by_scene") or {}
    ).get(SCENE) or []
    rows = (inherited.get("rows_by_scene") or {}).get(SCENE) or []
    if (
        checkpoint.get("scene") != SCENE
        or int(checkpoint.get("completed_outer_attempts") or 0) != 10
        or inherited.get("status") != "PASS"
        or inherited.get("schema_version") != "mysql_algorithm_c_replay_quarantine_v6"
        or int(inherited.get("resume_checkpoint_outer_attempt") or 0) != 10
        or inherited.get("formal_state_mutation") is not False
        or inherited.get("accepted_rounds_mutation") is not False
        or inherited.get("rejected_rounds_mutation") is not False
        or inherited.get("interval_convergence_mutation") is not False
        or len(keys) != 5
        or len(set(map(str, keys))) != 5
        or len(rows) != 5
        or any(row.get("formal_state_mutation") is not False for row in rows)
    ):
        raise RuntimeError("inherited TPC-H timeout quarantine differs")
    for name, raw in dict(inherited.get("evidence") or {}).items():
        path = Path(str((raw or {}).get("path") or "")).resolve()
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"inherited quarantine evidence differs: {name}: {path}")
    document = copy.deepcopy(inherited)
    document["created_at"] = datetime.now().astimezone().isoformat()
    document["initialization"] = "preserved_tpch_timeout_exclusions_rebound_to_chained_cleanup_checkpoint"
    evidence = dict(document.get("evidence") or {})
    evidence["checkpoint"] = bound(checkpoint_path)
    evidence["inherited_quarantine"] = bound(inherited_path)
    evidence["immediate_failed_source_state"] = copy.deepcopy(
        checkpoint["evidence"]["immediate_failed_source_state"]
    )
    document["evidence"] = evidence
    document["builder_source_path"] = str(Path(__file__).resolve())
    document["builder_source_sha256"] = sha(Path(__file__).resolve())
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--inherited-quarantine", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise RuntimeError("fresh quarantine output required")
    document = build(args.checkpoint, args.inherited_quarantine)
    args.output_dir.mkdir(parents=True, mode=0o700)
    output = args.output_dir / "replay_quarantine.json"
    atomic(output, document)
    print(json.dumps({
        "status": "PASS", "output": str(output),
        "permanent_invalid_candidates": 5, "sha256": sha(output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
