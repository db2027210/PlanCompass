#!/usr/bin/env python3
"""Publish v43/v49 with the audited non-target bottleneck hard gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v42/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v43/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v48/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v49/coordination_contract.json"
AUDIT_SOURCE = ROOT / "scripts/audit_mysql_algorithm_c_non_target_bottleneck_v1.py"
AUDIT = ROOT / "history/mysql_four_scene_20260809/algorithm_c_non_target_audit_v1_20260815T1654p0800/audit.json"
CONTROLLER = ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v2.py"
TEST = ROOT / "tests/test_mysql_algorithm_c_boundary_technical_isolation.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def bind(bindings: dict, label: str, path: Path) -> None:
    bindings[f"{label}_path"] = rel(path)
    bindings[f"{label}_sha256"] = sha(path)


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
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    expected = {
        "job_memory": 5,
        "tpch_sf4_memory": 5,
        "hybench_xp_cpu": 4,
        "hybench_xp_io": 3,
    }
    if (
        audit.get("status") != "PASS"
        or audit.get("failure_count") != 0
        or audit.get("currently_credited_counts") != expected
        or float(audit.get("threshold") or 0.0) != 0.8
        or audit.get("formal_state_mutation") is not False
        or audit.get("accepted_state_credit_mutation") is not False
    ):
        raise RuntimeError("non-target bottleneck provenance audit differs")

    policy = {
        "threshold": 0.8,
        "comparison": "each_non_target_resource_strictly_below_threshold",
        "applies_to_acceptance_tiers": [
            "strict_three_level", "qps_target", "fallback_qps"
        ],
        "candidate_score_mutation": False,
        "qps_resource_prediction_gradient_or_score_role": False,
        "prior_credited_acceptance_audit_status": "PASS",
        "prior_credited_acceptance_mutation": False,
        "future_violation_effect": "hard_gate_failed",
    }

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {
        "path": rel(OLD_COORD), "sha256": sha(OLD_COORD)
    }
    coordination["successor_reason"] = (
        "enforce the already-declared non-target bottleneck limit as a hard "
        "acceptance gate after a complete inherited-provenance audit"
    )
    coordination["non_target_bottleneck_hard_gate"] = policy
    rebind(coordination["source_bindings"])
    bind(coordination["source_bindings"], "non_target_bottleneck_audit_source", AUDIT_SOURCE)
    bind(coordination["source_bindings"], "non_target_bottleneck_audit", AUDIT)
    bind(coordination["source_bindings"], "non_target_bottleneck_release_builder", Path(__file__).resolve())
    bind(coordination["source_bindings"], "non_target_bottleneck_test", TEST)
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v43"
    optimization["predecessor"] = {
        "path": rel(OLD_OPT), "sha256": sha(OLD_OPT)
    }
    optimization["successor_reason"] = (
        "make the audited non-target resource 0.8 limit fail closed for every "
        "future Algorithm C acceptance tier"
    )
    optimization["non_target_bottleneck_hard_gate"] = policy
    allowance = optimization["co_resident_isolated_pg_allowance"]
    allowance["coordination_contract_path"] = rel(NEW_COORD)
    allowance["coordination_contract_sha256"] = sha(NEW_COORD)
    bindings = optimization["bindings"]
    rebind(bindings)
    bind(bindings, "non_target_bottleneck_audit_source", AUDIT_SOURCE)
    bind(bindings, "non_target_bottleneck_audit", AUDIT)
    bind(bindings, "non_target_bottleneck_release_builder", Path(__file__).resolve())
    bind(bindings, "non_target_bottleneck_test", TEST)
    write(NEW_OPT, optimization)

    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
        "controller_sha256": sha(CONTROLLER),
        "audit_sha256": sha(AUDIT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
