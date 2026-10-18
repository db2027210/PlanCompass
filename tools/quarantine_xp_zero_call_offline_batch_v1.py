#!/usr/bin/env python3
"""Quarantine an XP offline-only failed batch and reopen its state boundary."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Mapping


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp.%d" % os.getpid())
    with temporary.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def directory_digest(root: Path) -> tuple[str, int]:
    records = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise RuntimeError("offline batch contains a symlink: %s" % path)
        records.append((str(path.relative_to(root)), sha256(path)))
    encoded = json.dumps(records, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), len(records)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--quarantine-name", required=True)
    parser.add_argument("--expected-state-sha256", required=True)
    parser.add_argument("--expected-error", required=True)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    campaign = args.campaign_root.resolve()
    state_path = campaign / "state.json"
    batch_dir = campaign / "batches" / args.batch_id
    quarantine_dir = campaign / args.quarantine_name
    if not state_path.is_file() or sha256(state_path) != args.expected_state_sha256:
        raise RuntimeError("state SHA mismatch")
    if not batch_dir.is_dir() or batch_dir.is_symlink():
        raise RuntimeError("offline batch directory is missing")
    if quarantine_dir.exists():
        raise RuntimeError("quarantine destination already exists")
    for forbidden in (
        batch_dir / "selection.json",
        batch_dir / "replay_summary.json",
        batch_dir / "serial_replay",
    ):
        if forbidden.exists():
            raise RuntimeError("batch is not zero-workload offline-only: %s" % forbidden)
    nested_selection = batch_dir / "diagnostic_bundle" / "selection.json"
    nested_manifest = batch_dir / "diagnostic_bundle" / "manifest.json"
    if not nested_selection.is_file() or not nested_manifest.is_file():
        raise RuntimeError("offline failure evidence is incomplete")
    selection = json.loads(nested_selection.read_text(encoding="utf-8"))
    manifest = json.loads(nested_manifest.read_text(encoding="utf-8"))
    if (
        selection.get("replay_authorized") is not False
        or len(selection.get("top5") or []) != 0
        or manifest.get("status") != "blocked_missing_input"
        or int((manifest.get("counts") or {}).get("selected_top5", -1)) != 0
    ):
        raise RuntimeError("nested selection is not an offline fail-closed result")

    sys.path.insert(0, str(args.bundle_root.resolve()))
    from xp_unattended_continuation import AtomicStateStore  # type: ignore

    store = AtomicStateStore(state_path)
    state = store.load()
    expected_index = int(args.batch_id.removeprefix("cont"))
    if (
        state.get("status") != "blocked_contract"
        or state.get("last_error") != args.expected_error
        or int(state.get("next_batch_index", -1)) != expected_index
        or (state.get("last_batch") or {}).get("batch_id") == args.batch_id
    ):
        raise RuntimeError("state does not match the offline failed boundary")
    digest, file_count = directory_digest(batch_dir)
    next_state = copy.deepcopy(state)
    next_state["status"] = "running"
    next_state.pop("last_error", None)
    preview: Dict[str, Any] = {
        "status": "ready_to_commit" if args.commit else "dry_run_ready",
        "contract": "xp_zero_call_offline_batch_quarantine_v1",
        "batch_id": args.batch_id,
        "accepted_count_before": state["accepted_count"],
        "accepted_count_after": next_state["accepted_count"],
        "next_batch_index_before": state["next_batch_index"],
        "next_batch_index_after": next_state["next_batch_index"],
        "physical_workload_calls": 0,
        "formal_state_mutation": False,
        "offline_file_count": file_count,
        "offline_directory_digest": digest,
        "state_sha256_before": sha256(state_path),
        "failure_manifest_sha256": sha256(nested_manifest),
        "failure_selection_sha256": sha256(nested_selection),
    }
    if not args.commit:
        print(json.dumps(preview, indent=2, sort_keys=True), flush=True)
        return 0

    if sha256(state_path) != args.expected_state_sha256:
        raise RuntimeError("state changed immediately before quarantine")
    shutil.move(str(batch_dir), str(quarantine_dir))
    shutil.copy2(state_path, quarantine_dir / "state_before_quarantine.json")
    store.commit(next_state)
    preview.update(
        {
            "status": "committed",
            "state_sha256_after": sha256(state_path),
            "state_backup_sha256": sha256(
                quarantine_dir / "state_before_quarantine.json"
            ),
        }
    )
    atomic_write(quarantine_dir / "quarantine_audit.json", preview)
    print(json.dumps(preview, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
