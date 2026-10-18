#!/usr/bin/env python3
"""Fail-closed quarantine for an XP EXPLAIN-only partial batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent), text=True
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--expected-state-sha256", required=True)
    parser.add_argument("--quarantine-name", required=True)
    args = parser.parse_args()

    campaign = args.campaign_root.resolve()
    state_path = campaign / "state.json"
    batch_root = campaign / "batches" / args.batch_id
    quarantine = campaign / args.quarantine_name
    if quarantine.exists() or not batch_root.is_dir():
        raise RuntimeError("quarantine exists or source batch is missing")
    if sha256(state_path) != args.expected_state_sha256:
        raise RuntimeError("XP formal state SHA mismatch")
    state = load(state_path)
    if not (
        state.get("status") == "blocked_contract"
        and int(state.get("next_batch_index", -1))
        == int(args.batch_id.removeprefix("cont"))
        and int(state.get("accepted_count", -1)) == 2
        and str((state.get("last_batch") or {}).get("batch_id")) == "cont025"
        and int((state.get("last_batch") or {}).get("physical_calls", -1)) == 5
    ):
        raise RuntimeError("XP state is not at the expected cont026 boundary")

    files = sorted(path for path in batch_root.rglob("*") if path.is_file())
    if not files or any(path.is_symlink() for path in batch_root.rglob("*")):
        raise RuntimeError("XP partial is empty or contains symlinks")
    relative = [str(path.relative_to(batch_root)) for path in files]
    if "accepted_incumbent.json" not in relative:
        raise RuntimeError("XP partial lacks accepted_incumbent.json")
    unexpected = [
        name
        for name in relative
        if name != "accepted_incumbent.json"
        and not (
            name.startswith("explain_only/subset_logs/")
            and name.endswith(".jsonl")
        )
    ]
    if unexpected:
        raise RuntimeError(f"XP partial contains unexpected files: {unexpected}")
    forbidden_names = {
        "selection.json",
        "replay_summary.json",
        "formal_summary.json",
        "runner.log",
        "workload.log",
    }
    if any(path.name in forbidden_names for path in files):
        raise RuntimeError("XP partial contains replay/workload output")

    file_manifest = {
        str(path.relative_to(batch_root)): sha256(path) for path in files
    }
    state_backup = campaign / (
        f"state.pre_{args.batch_id}_v23_offline_handoff.json"
    )
    if state_backup.exists():
        raise RuntimeError("XP state backup already exists")
    shutil.copy2(state_path, state_backup)
    if sha256(state_backup) != args.expected_state_sha256:
        raise RuntimeError("XP state backup SHA mismatch")

    os.replace(batch_root, quarantine)
    if sha256(state_path) != args.expected_state_sha256:
        raise RuntimeError("XP state changed during quarantine")
    audit = {
        "contract": "xp_explain_only_partial_quarantine_v1",
        "batch_id": args.batch_id,
        "source_batch_root": str(batch_root),
        "quarantine_root": str(quarantine),
        "state_backup": str(state_backup),
        "formal_state_sha256_before": args.expected_state_sha256,
        "formal_state_sha256_after": sha256(state_path),
        "formal_state_mutated": False,
        "file_count": len(file_manifest),
        "file_manifest": file_manifest,
        "partial_stage": "explain_only_candidate_generation",
        "physical_workload_calls": 0,
        "valid_replays": 0,
        "technical_failures": 0,
        "accepted_added": 0,
        "incumbent_update_applied": False,
        "range_update_applied": False,
        "gradient_update_applied": False,
    }
    audit_path = quarantine / "handoff_audit.json"
    atomic_json(audit_path, audit)
    pointer = {
        "contract": audit["contract"],
        "audit_path": str(audit_path),
        "audit_sha256": sha256(audit_path),
        "formal_state_sha256": sha256(state_path),
        "physical_workload_calls": 0,
    }
    atomic_json(campaign / f"{args.batch_id}_v23_handoff.json", pointer)
    print(json.dumps(pointer, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
