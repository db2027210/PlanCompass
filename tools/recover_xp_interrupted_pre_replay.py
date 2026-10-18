#!/usr/bin/env python3
"""Quarantine an XP batch interrupted before any replay was selected."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--state-sha256", required=True)
    parser.add_argument("--batch-dir", required=True, type=Path)
    parser.add_argument("--expected-accepted", required=True, type=int)
    parser.add_argument("--expected-next-batch", required=True, type=int)
    parser.add_argument("--audit-dir", required=True, type=Path)
    args = parser.parse_args()

    state_path = args.state.resolve()
    batch_dir = args.batch_dir.resolve()
    if sha256(state_path) != args.state_sha256:
        raise RuntimeError("XP continuation state SHA mismatch")
    state = load_json(state_path)
    if (
        int(state.get("accepted_count", -1)) != args.expected_accepted
        or int(state.get("next_batch_index", -1))
        != args.expected_next_batch
        or state.get("status") != "blocked_contract"
    ):
        raise RuntimeError("XP continuation state does not match interruption")
    if batch_dir.name != f"cont{args.expected_next_batch:03d}":
        raise RuntimeError("batch directory does not match next batch index")

    observed_files = {
        str(path.relative_to(batch_dir))
        for path in batch_dir.rglob("*")
        if path.is_file()
    }
    required_files = {
        "accepted_incumbent.json",
        "explain_only/explain_only_generation.json",
    }
    unexpected_files = {
        relative
        for relative in observed_files
        if relative != "accepted_incumbent.json"
        and not relative.startswith("explain_only/")
    }
    if not required_files.issubset(observed_files) or unexpected_files:
        raise RuntimeError(
            "partial batch lacks required EXPLAIN evidence or contains "
            f"non-EXPLAIN files: unexpected={sorted(unexpected_files)}"
        )
    forbidden_names = {
        "selection.json",
        "replay_summary.json",
        "events.jsonl",
        "started.json",
        "result.json",
    }
    if any(path.name in forbidden_names for path in batch_dir.rglob("*")):
        raise RuntimeError("partial batch contains replay evidence")

    file_hashes = {
        relative: sha256(batch_dir / relative)
        for relative in sorted(required_files)
    }
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    quarantine = batch_dir.with_name(
        f"{batch_dir.name}_interrupted_pre_replay_{timestamp}"
    )
    if quarantine.exists():
        raise RuntimeError("quarantine destination already exists")
    batch_dir.rename(quarantine)

    audit = {
        "contract": "xp_interrupted_pre_replay_quarantine_v1",
        "status": "quarantined",
        "original_batch_dir": str(batch_dir),
        "quarantine_dir": str(quarantine),
        "state_path": str(state_path),
        "state_sha256_before": args.state_sha256,
        "state_sha256_after": sha256(state_path),
        "accepted_count_preserved": args.expected_accepted,
        "next_batch_index_preserved": args.expected_next_batch,
        "physical_workload_calls": 0,
        "valid_replays": 0,
        "formal_state_updates": 0,
        "quarantined_file_sha256": file_hashes,
    }
    if audit["state_sha256_after"] != args.state_sha256:
        raise RuntimeError("XP state changed during quarantine")
    args.audit_dir.resolve().mkdir(parents=True, exist_ok=True)
    atomic_json(quarantine / "quarantine_audit.json", audit)
    atomic_json(
        args.audit_dir.resolve() / f"cont002_quarantine_{timestamp}.json",
        audit,
    )
    print(json.dumps(audit, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
