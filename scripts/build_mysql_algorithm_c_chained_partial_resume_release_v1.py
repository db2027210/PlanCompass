#!/usr/bin/env python3
"""Publish v23/v30 for SHA-bound chained partial-outer continuation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v22/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v23/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v29/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v30/coordination_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def rebind(bindings: dict) -> None:
    for key, value in list(bindings.items()):
        sha_key = key[:-5] + "_sha256" if key.endswith("_path") else None
        if sha_key is None or sha_key not in bindings:
            continue
        path = (ROOT / str(value)).resolve()
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"runtime release binding differs: {path}")
        bindings[sha_key] = sha(path)


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {
        "path": rel(OLD_COORD), "sha256": sha(OLD_COORD),
    }
    coordination["successor_reason"] = (
        "bind chained partial-outer continuation; frozen PG cohort and dynamic "
        "isolation thresholds are unchanged"
    )
    rebind(coordination["source_bindings"])
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v23"
    optimization["predecessor"] = {
        "path": rel(OLD_OPT), "sha256": sha(OLD_OPT),
    }
    optimization["successor_reason"] = (
        "allow a second technical continuation to reuse only SHA-bound passed "
        "ranks inherited from the first continuation plus its local passed ranks"
    )
    optimization["co_resident_isolated_pg_allowance"][
        "coordination_contract_path"
    ] = rel(NEW_COORD)
    optimization["co_resident_isolated_pg_allowance"][
        "coordination_contract_sha256"
    ] = sha(NEW_COORD)
    optimization["chained_partial_outer_continuation"] = {
        "inherited_completed_rank_path_and_sha_required": True,
        "local_completed_rank_path_and_sha_required": True,
        "duplicate_or_missing_rank_action": "fail_closed",
        "interrupted_rank_replay_prohibited": True,
        "candidate_state_credit_increment": 0,
        "algorithm_acceptance_and_convergence_semantics_changed": False,
    }
    builder = Path(__file__).resolve()
    optimization["bindings"]["chained_partial_resume_release_builder_path"] = rel(builder)
    optimization["bindings"]["chained_partial_resume_release_builder_sha256"] = sha(builder)
    rebind(optimization["bindings"])
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
