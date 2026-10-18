#!/usr/bin/env python3
"""Recover XP v37 after a zero-call serial-runner contract mismatch."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any


STATE = Path(
    "/home/anonymous/program/resource_adaptive/history/"
    "xp_final_algorithm_fresh10_v37_20260803/state.json"
)
EXPECTED_STATE_SHA256 = (
    "909b01b195222928e129140ffb21f9a30d3789c5ae608d380d69e675fac35584"
)
EXPECTED_ERROR = "ValueError: unexpected selection contract"
PROTECTED_FIELDS = (
    "accepted_count",
    "accepted_fingerprints",
    "incumbent_score",
    "incumbent_params",
    "parameter_ranges",
    "gradient_state",
    "batch_history",
    "last_batch",
    "replayed_fingerprints",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    if not STATE.is_file() or sha256(STATE) != EXPECTED_STATE_SHA256:
        raise RuntimeError("XP v37 recovery source state SHA mismatch")
    value = json.loads(STATE.read_text(encoding="utf-8"))
    if (
        value.get("status") != "blocked_contract"
        or value.get("last_error") != EXPECTED_ERROR
        or int(value.get("accepted_count", -1)) != 0
        or int(value.get("accepted_target", -1)) != 10
        or int(value.get("next_batch_index", -1)) != 2
        or int((value.get("gradient_state") or {}).get("update_count", -1)) != 0
        or (value.get("last_batch") or {}).get("batch_id") != "cont001"
        or int((value.get("last_batch") or {}).get("physical_calls", -1)) != 5
        or int((value.get("last_batch") or {}).get("technical_failures", -1)) != 0
    ):
        raise RuntimeError("XP v37 recovery observed state mismatch")

    protected_before = {name: copy.deepcopy(value.get(name)) for name in PROTECTED_FIELDS}
    protected_sha = digest(protected_before)
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    backup = STATE.with_name(f"state.before_contract_registry.{timestamp}.json")
    if backup.exists():
        raise RuntimeError(f"XP v37 recovery backup already exists: {backup}")
    shutil.copy2(STATE, backup)

    value["status"] = "initialized_fresh0_runtime_anchor_ready"
    value.pop("last_error", None)
    value["v37_selection_contract_registry_recovery"] = {
        "contract": "xp_v37_zero_call_exact_repeat_contract_registry_recovery_v1",
        "source_state_sha256": EXPECTED_STATE_SHA256,
        "source_error": EXPECTED_ERROR,
        "registered_selection_contract": (
            "xp_robust_exact_regime_top3_plus_two_uncertain_probes_v1"
        ),
        "backup": str(backup),
        "backup_sha256": sha256(backup),
        "protected_state_sha256": protected_sha,
        "workload_calls_added": 0,
        "accepted_incumbent_range_gradient_mutations": 0,
        "manual_candidate_injection": False,
    }
    protected_after = {name: copy.deepcopy(value.get(name)) for name in PROTECTED_FIELDS}
    if digest(protected_after) != protected_sha:
        raise RuntimeError("XP v37 recovery would mutate protected formal state")
    atomic_json(STATE, value)
    print(
        json.dumps(
            {
                "status": "recovered_zero_call",
                "state": str(STATE),
                "state_sha256": sha256(STATE),
                "backup": str(backup),
                "backup_sha256": sha256(backup),
                "protected_state_sha256": protected_sha,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
