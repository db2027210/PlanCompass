#!/usr/bin/env python3
"""Quarantine XP cont014 generated with a stale safe-utilization envelope."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any, Dict


ROOT = Path(
    "/home/anonymous/program/resource_adaptive/history/"
    "xp_final_algorithm_fresh10_v26_20260731T1935"
)
STATE = ROOT / "state.json"
PARTIAL = ROOT / "batches" / "cont014"
PARTIAL_SELECTION = PARTIAL / "exact_plan_repeat" / "selection.json"
EXPECTED_STATE_SHA = (
    "58b74926543461213bc9616e18aa530b266aba0b47a860fb60c35be758d053b3"
)
EXPECTED_SELECTION_SHA = (
    "db1c3bdb685eef35f8996b58aad34573fe0cb6ddff72f6334aa34dbdc83c2676"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    if sha256(STATE) != EXPECTED_STATE_SHA:
        raise RuntimeError("XP formal state SHA changed; refuse recovery")
    if sha256(PARTIAL_SELECTION) != EXPECTED_SELECTION_SHA:
        raise RuntimeError("XP partial cont014 selection SHA changed")
    state = load(STATE)
    if not (
        int(state.get("accepted_count", -1)) == 1
        and int(state.get("next_batch_index", -1)) == 14
        and state.get("status") == "blocked_contract"
        and state.get("last_error")
        == "uncertain probe exceeds safe predicted utilization"
        and (state.get("last_batch") or {}).get("batch_id") == "cont013"
        and (state.get("last_batch") or {}).get("status") == "complete"
    ):
        raise RuntimeError("XP state does not match the sealed cont014 failure")
    forbidden = list(PARTIAL.rglob("result.json"))
    forbidden.extend(PARTIAL.rglob("replay_summary.json"))
    forbidden.extend(PARTIAL.rglob("workload.log"))
    if forbidden:
        raise RuntimeError("partial cont014 contains workload evidence")

    timestamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
    quarantine = ROOT / f"quarantine_cont014_stale_safe_envelope_{timestamp}"
    if quarantine.exists():
        raise RuntimeError(f"quarantine already exists: {quarantine}")
    shutil.move(str(PARTIAL), str(quarantine))
    audit = {
        "contract": "xp_cont014_stale_safe_envelope_zero_call_recovery_v1",
        "status": "quarantined_before_replay",
        "state_path": str(STATE),
        "state_sha256": EXPECTED_STATE_SHA,
        "accepted_count": 1,
        "next_batch_index": 14,
        "partial_selection_sha256": EXPECTED_SELECTION_SHA,
        "quarantine_root": str(quarantine),
        "physical_workload_calls": 0,
        "valid_replays": 0,
        "technical_failures": 0,
        "formal_state_mutated": False,
        "reason": "stale safe envelope rejected one cleanly observed probe",
    }
    atomic_json(quarantine / "recovery_audit.json", audit)
    print(json.dumps(audit, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
