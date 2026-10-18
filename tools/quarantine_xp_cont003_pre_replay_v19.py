#!/usr/bin/env python3
"""Quarantine the blocked XP cont003 generation before regenerating it."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


ROOT = Path(
    "/home/anonymous/program/resource_adaptive/history/"
    "xp_autonomous_formal_target10_v1_20260731"
)
STATE = ROOT / "state.json"
SOURCE = ROOT / "batches/cont003"
DESTINATION = ROOT / "batches/cont003_blocked_pre_replay_v19_20260731T1615"
EXPECTED = {
    STATE: "17b6e841a4f47b51a408c6c8a6e9d506fcb6c37e6278032ba48b670f0c822391",
    SOURCE / "diagnostic_bundle/manifest.json": (
        "0cde30b8283a8c0750a697918a8a926a7f2f79ef77f7f9878d298a3254b8c5a3"
    ),
    SOURCE / "diagnostic_bundle/selection.json": (
        "3af6e30406e5e78ba61c391e6ca5282ba31c07f3a1ce9b2f2f014b52f95bed5f"
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
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
    if DESTINATION.exists():
        raise FileExistsError(DESTINATION)
    for path, expected in EXPECTED.items():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"missing or symlinked authority input: {path}")
        observed = sha256(path)
        if observed != expected:
            raise RuntimeError(
                f"fail closed SHA mismatch: {path}: {observed} != {expected}"
            )

    state = load_json(STATE)
    if (
        state.get("accepted_count") != 4
        or state.get("next_batch_index") != 3
        or state.get("status") != "blocked_contract"
        or state.get("last_error")
        != "uncertain probe exceeds safe predicted utilization"
        or (state.get("last_batch") or {}).get("batch_id") != "cont002"
    ):
        raise RuntimeError("formal state does not exactly match blocked pre-replay state")

    manifest = load_json(SOURCE / "diagnostic_bundle/manifest.json")
    selection = load_json(SOURCE / "diagnostic_bundle/selection.json")
    if (
        manifest.get("status") != "ready"
        or selection.get("replay_authorized") is not True
        or selection.get("batch_mode") != "diagnostic_validation"
        or len(selection.get("top5") or []) != 5
        or len(
            {
                str((item.get("three_level_fingerprint") or {}).get("id"))
                for item in selection.get("top5") or []
            }
        )
        != 5
    ):
        raise RuntimeError("blocked cont003 selection does not match sealed ready audit")

    forbidden_names = {
        "replay_summary.json",
        "ledger.json",
        "events.jsonl",
    }
    for path in SOURCE.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(SOURCE)
        if (
            path.name in forbidden_names
            or "rank_" in relative.as_posix()
            or "replay" in relative.parts
        ):
            raise RuntimeError(f"physical replay evidence exists; refusing rename: {relative}")

    state_sha_before = sha256(STATE)
    os.replace(SOURCE, DESTINATION)
    audit = {
        "contract": "xp_cont003_blocked_pre_replay_quarantine_v1",
        "reason": (
            "v19 controller normalized dynamic safe-frontier candidates "
            "against stale absolute limits before any physical call"
        ),
        "physical_workload_invocation_count": 0,
        "accepted_count_preserved": 4,
        "next_batch_index_preserved": 3,
        "formal_state_sha256_before": state_sha_before,
        "formal_state_sha256_after": sha256(STATE),
        "source_path": str(SOURCE),
        "quarantine_path": str(DESTINATION),
        "manifest_sha256": EXPECTED[
            SOURCE / "diagnostic_bundle/manifest.json"
        ],
        "selection_sha256": EXPECTED[
            SOURCE / "diagnostic_bundle/selection.json"
        ],
    }
    if audit["formal_state_sha256_after"] != state_sha_before:
        raise RuntimeError("formal state changed during pre-replay quarantine")
    atomic_json(DESTINATION / "quarantine_audit.json", audit)
    print(json.dumps(audit, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
