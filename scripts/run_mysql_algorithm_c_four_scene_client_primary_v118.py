#!/usr/bin/env python3
"""Resume a PG-exact search from a verified chained scene boundary."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v10 as checkpoint_v10
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v4 as checkpoint_v4
from scripts import mysql_algorithm_c_plan_capture_timeout_v1 as plan_timeout
from scripts import mysql_outer_rejection_cycle_v3 as pg_rejection
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v13 as client_v13
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v30 as client_v30
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v66 as client_v66
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v96 as release_v96
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v97 as release_v97
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v98 as release_v98
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v99 as release_v99
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v100 as release_v100
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v54 as runtime
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_exact_resume_20260929_v197/"
    "algorithm_c_pg_exact_resume_contract.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def _argument_value(name: str) -> str:
    try:
        index = sys.argv.index(name)
        return sys.argv[index + 1]
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"required argument missing: {name}") from exc


def validate_contract() -> dict[str, Any]:
    value = load(CONTRACT)
    if (
        value.get("release_version") != "v197-pg-exact-scene-boundary-resume"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("fresh_search_lineage_required") is not True
        or value.get("fresh_search_reset_on_resume") is not False
        or value.get("completed_rounds_preserved") is not True
        or value.get("incomplete_outer_credit") != 0
        or value.get("pg_interval_release") != "v186"
        or value.get("pg_rejection_release") != "v195"
        or value.get("workload_or_candidate_policy_changed") is not False
        or value.get("resource_envelope_changed") is not False
    ):
        raise RuntimeError("Algorithm C v197 resume contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v197 source differs: {name}: {path}")
    return value


def _resume_v30_main() -> int:
    """Run the sealed v120 binding without resetting a verified successor state."""
    client_v30.validate_contract()
    old_validate = client_v30.predecessor.validate_contract
    old_contract = client_v30.predecessor.CONTRACT
    client_v30.predecessor.validate_contract = client_v30.validate_contract
    client_v30.predecessor.CONTRACT = client_v30.CONTRACT
    try:
        with client_v30.bind_runtime():
            return int(client_v30.predecessor.main())
    finally:
        client_v30.predecessor.validate_contract = old_validate
        client_v30.predecessor.CONTRACT = old_contract


def main() -> int:
    validate_contract()
    release_v96.validate_contract()
    release_v97.validate_contract()
    release_v98.validate_contract()
    release_v99.validate_contract()
    release_v100.validate_contract()
    if "--fresh-search" in sys.argv[1:]:
        raise RuntimeError("v197 resume must preserve the completed fresh-search boundary")
    checkpoint_path = Path(_argument_value("--checkpoint")).expanduser().resolve()
    checkpoint = checkpoint_v10.verify_checkpoint(checkpoint_path)
    source_state = load(Path(str(checkpoint["evidence"]["source_state"]["path"])))
    completed = int(checkpoint.get("completed_outer_attempts") or 0)
    scene = str(checkpoint.get("scene") or "")
    root_outer = int(source_state.get("outer_attempt") or 0)
    immediate_binding = checkpoint["evidence"].get("immediate_failed_source_state")
    immediate_state = None
    if immediate_binding:
        immediate_path = Path(str(immediate_binding.get("path") or "")).resolve()
        if (
            immediate_path.is_symlink()
            or not immediate_path.is_file()
            or sha(immediate_path) != immediate_binding.get("sha256")
        ):
            raise RuntimeError("chained immediate failed source differs")
        immediate_state = load(immediate_path)
    audit = checkpoint.get("scene_resume_audit") or {}
    reconstructed = checkpoint.get("reconstructed_state") or {}
    accepted = int((reconstructed.get("accepted_rounds") or {}).get(scene, -1))
    rejected = int(
        (reconstructed.get("rejected_outer_attempts") or {}).get(scene, -1)
    )
    chained_source_valid = (
        immediate_state is not None
        and root_outer < completed
        and immediate_state.get("status") == "FAILED_CLOSED"
        and int(immediate_state.get("outer_attempt") or -1) == completed
        and int(immediate_state.get("completed_predecessor_outer_attempts") or -1)
        == root_outer
        and int(audit.get("incomplete_outer_attempt") or -1) == completed + 1
        and int(audit.get("incomplete_outer_credit", -1)) == 0
        and accepted + rejected == completed
    )
    if (
        scene not in alg.SCENES
        or completed < 1
        or source_state.get("fresh_search") is not True
        or int(source_state.get("completed_predecessor_outer_attempts", -1)) != 0
        or not (root_outer == completed or chained_source_valid)
    ):
        raise RuntimeError("checkpoint is not a verified fresh-search lineage boundary")

    old_optimization = (
        client_v13.OPTIMIZATION_CONTRACT,
        client_v66.OPTIMIZATION_CONTRACT,
    )
    rejection_bindings = [
        (client_v13, "rejection_cycle"),
        (client_v13.latest_runtime, "rejection_cycle"),
        (client_v30, "rejection_cycle"),
        (client_v30.runtime, "rejection_cycle"),
    ]
    old_rejection = [
        (owner, name, getattr(owner, name)) for owner, name in rejection_bindings
    ]
    old_explain = alg.explain
    old_runtime = client_v66.runtime
    old_checkpoint = runtime.scene_checkpoint
    old_v30_main = client_v30.main
    old_standard_verify = checkpoint_v4._ORIGINAL_STANDARD_VERIFY
    hwloc_existed = "HWLOC_COMPONENTS" in os.environ
    old_hwloc = os.environ.get("HWLOC_COMPONENTS")

    client_v13.OPTIMIZATION_CONTRACT = release_v100.OPTIMIZATION
    client_v66.OPTIMIZATION_CONTRACT = release_v100.OPTIMIZATION
    for owner, name in rejection_bindings:
        setattr(owner, name, pg_rejection)
    alg.explain = plan_timeout.explain
    os.environ["HWLOC_COMPONENTS"] = release_v98.HWLOC_COMPONENTS
    client_v66.runtime = runtime
    runtime.scene_checkpoint = checkpoint_v10
    client_v30.main = _resume_v30_main

    def schema_aware_standard_verify(path: Path):
        value = checkpoint_v10._load(path)
        if value.get("schema_version") == checkpoint_v10.SCHEMA:
            return checkpoint_v10.verify_checkpoint(path)
        return old_standard_verify(path)

    checkpoint_v4._ORIGINAL_STANDARD_VERIFY = schema_aware_standard_verify
    try:
        return int(client_v66.main())
    finally:
        checkpoint_v4._ORIGINAL_STANDARD_VERIFY = old_standard_verify
        client_v30.main = old_v30_main
        runtime.scene_checkpoint = old_checkpoint
        client_v66.runtime = old_runtime
        if hwloc_existed:
            os.environ["HWLOC_COMPONENTS"] = str(old_hwloc)
        else:
            os.environ.pop("HWLOC_COMPONENTS", None)
        alg.explain = old_explain
        for owner, name, value in reversed(old_rejection):
            setattr(owner, name, value)
        (
            client_v13.OPTIMIZATION_CONTRACT,
            client_v66.OPTIMIZATION_CONTRACT,
        ) = old_optimization


if __name__ == "__main__":
    raise SystemExit(main())
