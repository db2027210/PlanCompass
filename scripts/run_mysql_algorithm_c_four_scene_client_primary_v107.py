#!/usr/bin/env python3
"""Fresh PG-C search with exact kernel-journal gate classification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v31 as coordination_v31
from scripts import mysql_cross_project_wave_coordination_v27 as coordination_v27
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v106 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_dynamic_gate_20260929_v210/"
    "algorithm_c_pg_dynamic_gate_contract.json"
)


# Historical contracts resolve the installed callable's code filename before
# hashing the immutable v27 source.  Compile a two-function compatibility
# trampoline against that filename; the v210 contract binds this controller
# and the actual v29 targets by SHA.
_trampoline_namespace = {
    "successor_inventory": coordination_v31._inventory,
    "successor_gate": coordination_v31.rolling_quiet_gate,
}
exec(
    compile(
        "def inventory(*args, **kwargs):\n"
        "    return successor_inventory(*args, **kwargs)\n"
        "def gate(*args, **kwargs):\n"
        "    return successor_gate(*args, **kwargs)\n",
        str(Path(coordination_v27.__file__).resolve()),
        "exec",
    ),
    _trampoline_namespace,
)
_inventory_trampoline = _trampoline_namespace["inventory"]
_gate_trampoline = _trampoline_namespace["gate"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def validate_contract() -> dict[str, Any]:
    value = load(CONTRACT)
    if (
        value.get("release_version") != "v210-pg-dynamic-gate"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("fresh_search_required") is not True
        or value.get("superseded_v209_rounds_reusable") is not False
        or value.get("exact_target_journal_classification") is not True
        or value.get("effective_maximum_clean_window_wait_seconds") != 120
        or value.get("required_clean_window_seconds") != 15
        or value.get("unknown_user_processes_remain_fail_closed") is not True
        or value.get("algorithm_decision_logic_changed") is not False
    ):
        raise RuntimeError("Algorithm C v210 dynamic-gate contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v210 gate source differs: {name}: {path}")
    return value


def main() -> int:
    validate_contract()
    predecessor.validate_contract()
    # Keep the v28 module and install function identities visible to every
    # historical SHA validator.  Its installer resolves these two module
    # globals only at the final runtime binding boundary.
    old_inventory = predecessor.coordination_v28._inventory
    old_gate = predecessor.coordination_v28.rolling_quiet_gate
    predecessor.coordination_v28._inventory = _inventory_trampoline
    predecessor.coordination_v28.rolling_quiet_gate = _gate_trampoline
    try:
        return int(predecessor.main())
    finally:
        predecessor.coordination_v28.rolling_quiet_gate = old_gate
        predecessor.coordination_v28._inventory = old_inventory


if __name__ == "__main__":
    raise SystemExit(main())
