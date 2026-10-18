#!/usr/bin/env python3
"""Publish v55 with a read-only external PG affinity observer."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OLD = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v54"
    / "optimization_contract.json"
)
NEW = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v55"
    / "optimization_contract.json"
)
OBSERVER = ROOT / "scripts/observe_frozen_external_pg_client_affinity_v1.py"
CONTROLLER = ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v14.py"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v14.py"
R0_RELEASE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v58_target_identifiability_r0_from_attempt012_20260817/r0_binding.json"
)
R0_BUILDER = ROOT / "scripts/reclassify_mysql_four_scene_r0_target_identifiability_v1.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular evidence required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def bind(bindings: dict[str, Any], label: str, path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular binding required: {path}")
    bindings[f"{label}_path"] = rel(path)
    bindings[f"{label}_sha256"] = sha(path)


def main() -> int:
    if NEW.exists():
        raise RuntimeError(f"fresh successor already exists: {NEW}")
    old = load(OLD)
    bindings = old.get("bindings") or {}
    old_guard = ROOT / str(bindings.get("external_pg_affinity_guard_path") or "")
    if (
        old.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or old.get("formal_scope") != "mysql_only"
        or old.get("cross_engine_comparison_prohibited") is not True
        or not old_guard.is_file()
        or sha(old_guard) != bindings.get("external_pg_affinity_guard_sha256")
        or load(R0_RELEASE).get("status") != "PASS"
    ):
        raise RuntimeError("v54 predecessor authority differs")
    successor = dict(old)
    successor["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v55"
    successor["predecessor"] = {"path": rel(OLD), "sha256": sha(OLD)}
    successor["successor_reason"] = (
        "replace the external PostgreSQL affinity mutator with a read-only observer; "
        "mismatches remain fail-closed through the runtime watchdog; workload, frozen "
        "resource envelope, Top-5, scoring, acceptance, binary shrink and convergence "
        "semantics are unchanged"
    )
    successor["external_affinity_observation"] = {
        "mode": "read_only",
        "external_process_affinity_mutation": False,
        "external_container_mutation": False,
        "mismatch_action": "runtime_watchdog_fail_closed",
        "algorithm_semantics_changed": False,
    }
    successor_bindings = dict(bindings)
    bind(successor_bindings, "external_pg_affinity_guard", OBSERVER)
    bind(successor_bindings, "algorithm_c_v55_entrypoint", CONTROLLER)
    bind(successor_bindings, "algorithm_c_v55_launcher", LAUNCHER)
    bind(successor_bindings, "target_identifiability_r0_release", R0_RELEASE)
    bind(successor_bindings, "target_identifiability_r0_builder", R0_BUILDER)
    bind(successor_bindings, "read_only_affinity_release_builder", Path(__file__))
    successor["bindings"] = successor_bindings
    NEW.parent.mkdir(parents=True, mode=0o700)
    temporary = NEW.with_name(f".{NEW.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(successor, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, NEW)
    NEW.with_name(NEW.name + ".sha256").write_text(
        f"{sha(NEW)}  {NEW.name}\n", encoding="ascii",
    )
    print(json.dumps({"status": "PASS", "contract": str(NEW), "sha256": sha(NEW)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
