#!/usr/bin/env python3
"""Publish v16/v23 runtime contracts for watchdog-interrupted rank recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v17/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v18/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v24/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v25/coordination_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def rebind(bindings: dict) -> None:
    for key, value in list(bindings.items()):
        if key.endswith("_path") and key[:-5] + "_sha256" in bindings:
            path = (ROOT / str(value)).resolve()
            if not path.is_file() or path.is_symlink():
                raise RuntimeError(f"runtime release binding differs: {path}")
            bindings[key[:-5] + "_sha256"] = sha(path)


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = (
        "bind fail-closed runtime-interruption continuation; lease and gate semantics unchanged"
    )
    rebind(coordination["source_bindings"])
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v18"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = (
        "reuse only SHA-bound completed ranks after watchdog interruption and restart the interrupted rank"
    )
    optimization["runtime_interruption_continuation"] = {
        "required_watchdog_event": "runtime_interference_detected_under_exclusive_wave_lease",
        "required_terminal_event": "formal_forced_stopped_after_sigint_timeout",
        "interrupted_rank_replay_prohibited": True,
        "completed_rank_reuse_requires_pass_coverage_and_hard_gates": True,
        "outer_decision_before_all_five_ranks_prohibited": True,
        "measurement_acceptance_convergence_and_pruning_unchanged": True,
        "watchdog_atomically_marks_stale_running_state_failed_closed": True,
        "watchdog_state_credit_increment": 0,
    }
    builder = Path(__file__).resolve()
    optimization["bindings"]["runtime_interruption_release_builder_path"] = rel(builder)
    optimization["bindings"]["runtime_interruption_release_builder_sha256"] = sha(builder)
    rebind(optimization["bindings"])
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
