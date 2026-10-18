#!/usr/bin/env python3
"""Quarantine XP cont015 after a zero-call transient PG connect failure."""

from __future__ import annotations

import copy
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
PARTIAL = ROOT / "batches" / "cont015"
EXPECTED = {
    STATE: "1824e8dce30c8b455eee710cbdf1b0aa6ceeca886530bcbc3fd7549e18d6bba2",
    PARTIAL / "accepted_incumbent.json": (
        "b6945ed418023c5b916d6ca82a2e693cfa75c8f35d59db6b024e926a54fe0085"
    ),
    PARTIAL / "exact_plan_repeat" / "automatic_reexploration.json": (
        "7b068d6035a1600a5b4d2b581ee2583daf1595b78bfcce82333a24675183744a"
    ),
    PARTIAL / "exact_plan_repeat" / "builder.log": (
        "8ce222892c7a37b524d9b47ef88c5fba7df751da2a8f86dd044585567b6a946d"
    ),
}
FORMAL_FIELDS = (
    "accepted_count",
    "accepted_fingerprints",
    "incumbent",
    "incumbent_params",
    "incumbent_score",
    "parameter_ranges",
    "range_state",
    "replayed_fingerprints",
    "training_state_sha256",
    "batch_history",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
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


def projection(state: Dict[str, Any]) -> Dict[str, Any]:
    return {name: copy.deepcopy(state.get(name)) for name in FORMAL_FIELDS}


def main() -> int:
    for path, expected_sha in EXPECTED.items():
        if not path.is_file() or sha256(path) != expected_sha:
            raise RuntimeError(f"sealed XP connect recovery input changed: {path}")
    state = load(STATE)
    history = state.get("batch_history") or []
    if not (
        state.get("status") == "blocked_contract"
        and state.get("last_error") == "OperationalError: "
        and int(state.get("accepted_count", -1)) == 1
        and int(state.get("next_batch_index", -1)) == 15
        and history
        and history[-1].get("batch_id") == "cont014"
        and (state.get("last_batch") or {}).get("batch_id") == "cont014"
    ):
        raise RuntimeError("XP state is not at the transient connect boundary")
    files = sorted(path.relative_to(PARTIAL).as_posix() for path in PARTIAL.rglob("*") if path.is_file())
    expected_files = sorted(
        [
            "accepted_incumbent.json",
            "exact_plan_repeat/automatic_reexploration.json",
            "exact_plan_repeat/builder.log",
        ]
    )
    if files != expected_files:
        raise RuntimeError("XP transient failure contains unexpected evidence")

    before = projection(state)
    timestamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
    quarantine = ROOT / f"quarantine_cont015_pg_connect_{timestamp}"
    if quarantine.exists():
        raise RuntimeError(f"quarantine already exists: {quarantine}")
    shutil.move(str(PARTIAL), str(quarantine))
    atomic_json(quarantine / "state_before_recovery.json", state)

    recovered = copy.deepcopy(state)
    recovered["status"] = "running"
    recovered["last_error"] = None
    if projection(recovered) != before:
        raise RuntimeError("connect recovery would mutate formal state")
    atomic_json(STATE, recovered)
    audit = {
        "contract": "xp_cont015_transient_pg_connect_zero_call_recovery_v1",
        "status": "reopened_same_batch_after_bounded_retry_fix",
        "accepted_count": 1,
        "next_batch_index": 15,
        "physical_workload_calls": 0,
        "valid_replays": 0,
        "technical_failures": 0,
        "formal_state_mutated": False,
        "state_sha256_before": EXPECTED[STATE],
        "state_sha256_after": sha256(STATE),
        "quarantine_root": str(quarantine),
    }
    atomic_json(quarantine / "recovery_audit.json", audit)
    print(json.dumps(audit, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
