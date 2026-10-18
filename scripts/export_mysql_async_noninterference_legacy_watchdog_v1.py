#!/usr/bin/env python3
"""Project the lock-free async observer log into the legacy C resume schema.

The boundary-resume validator predates the full-parallel successor and reads
``runtime_external_interference_watchdog.jsonl``.  The lock-free observer
records the same interruption under ``runtime_noninterference_observer.jsonl``.
This tool creates a minimal, provenance-bound compatibility projection; it
does not change the source state, measurements, or interruption evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise RuntimeError("observer row is not an object")
            rows.append(value)
    return rows


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.run_root.resolve(strict=True)
    source = root / "runtime_noninterference_observer.jsonl"
    state_path = root / "run_state.json"
    destination = root / "runtime_external_interference_watchdog.jsonl"
    manifest_path = root / "runtime_watchdog_compatibility_projection.json"
    if destination.exists() or manifest_path.exists():
        raise RuntimeError("compatibility projection already exists")
    if not source.is_file() or source.is_symlink() or not state_path.is_file():
        raise RuntimeError("regular observer and state evidence required")
    rows = load_rows(source)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    events = [
        row for row in rows
        if row.get("event")
        == "runtime_interference_detected_under_exclusive_wave_lease"
    ]
    terminals = [
        row for row in rows
        if row.get("event") == "formal_forced_stopped_after_sigint_timeout"
    ]
    interruption = state.get("watchdog_interruption") or {}
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or len(events) != 1
        or len(terminals) != 1
        or interruption.get("runtime_stage") != events[0].get("runtime_stage")
        or interruption.get("formal_state_credit_increment") != 0
        or interruption.get("interrupted_candidate_replay_prohibited") is not True
        or not (events[0].get("blockers") or [])
    ):
        raise RuntimeError("source interruption boundary differs")
    source_sha = sha256(source)
    state_sha = sha256(state_path)
    started = {
        "captured_at": rows[0].get("captured_at"),
        "event": "watchdog_started",
        "formal_run_id": rows[0].get("formal_run_id"),
        "formal_unit": rows[0].get("formal_unit"),
        "projection_source_path": str(source),
        "projection_source_sha256": source_sha,
        "cross_scene_lock_held": False,
        "resource_lease_held": False,
    }
    projected_event = dict(events[0])
    projected_event.update({
        "projection_source_path": str(source),
        "projection_source_sha256": source_sha,
        "projection_state_path": str(state_path),
        "projection_state_sha256": state_sha,
    })
    terminal = dict(terminals[0])
    terminal.update({
        "projection_source_path": str(source),
        "projection_source_sha256": source_sha,
    })
    projected = [started, projected_event, terminal]
    atomic_text(
        destination,
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in projected),
    )
    manifest = {
        "schema_version": "mysql_async_noninterference_legacy_projection_v1",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "source_path": str(source),
        "source_sha256": source_sha,
        "state_path": str(state_path),
        "state_sha256": state_sha,
        "destination_path": str(destination),
        "destination_sha256": sha256(destination),
        "source_rows": len(rows),
        "projected_rows": len(projected),
        "measurement_files_modified": False,
        "state_modified": False,
        "formal_state_credit_increment": 0,
        "cross_scene_lock_held": False,
        "resource_lease_held": False,
    }
    atomic_text(
        manifest_path,
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    atomic_text(
        manifest_path.with_suffix(manifest_path.suffix + ".sha256"),
        f"{sha256(manifest_path)}  {manifest_path.name}\n",
    )
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
