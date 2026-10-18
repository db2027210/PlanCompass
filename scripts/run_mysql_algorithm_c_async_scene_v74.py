#!/usr/bin/env python3
"""Run Algorithm C v74 with a global monotonic incumbent-chain invariant."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import audit_mysql_algorithm_c_global_incumbent_chain_v1 as chain_audit
from scripts import run_mysql_algorithm_c_async_scene_v73 as predecessor


CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_four_scene_algorithm_c_qps_20260827_v26"
    / "global_monotonic_incumbent_contract.json"
)
PARENT_CONTRACT = predecessor.ASYNC_CONTRACT
_ORIGINAL_VERIFY_TOP4_CHECKPOINT = predecessor.verify_top4_checkpoint


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def validate_contract() -> dict[str, Any]:
    parent = predecessor.alg.load(PARENT_CONTRACT)
    contract = predecessor.alg.load(CONTRACT)
    bindings = mapping(contract.get("source_bindings"), "v74 source bindings")
    if (
        parent.get("release_version") != "v73"
        or parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or parent.get("formal_scope") != "mysql_only"
        or parent.get("cross_engine_comparison_prohibited") is not True
        or parent.get("outer_acceptance_policy")
        != predecessor.qps_policy.policy_contract()
        or parent.get("outer_rejection_cycle_policy")
        != predecessor.rejection_cycle.policy_contract()
        or parent.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or parent.get("four_scene_wave_barrier") is not False
        or parent.get("sibling_scene_wait") is not False
        or parent.get("lease_mode") != "none"
        or parent.get("post_search_stability_confirmation") is not False
        or
        contract.get("schema_version")
        != "mysql_algorithm_c_global_monotonic_incumbent_contract_v1"
        or contract.get("release_version") != "v74"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("parent_contract", {}).get("path")
        != str(PARENT_CONTRACT.relative_to(ROOT))
        or contract.get("parent_contract", {}).get("sha256")
        != predecessor.alg.sha(PARENT_CONTRACT)
        or contract.get("resume_invariant", {}).get(
            "next_reference_equals_previous_selected_incumbent"
        ) is not True
        or contract.get("resume_invariant", {}).get(
            "checkpoint_reference_may_decrease"
        ) is not False
        or contract.get("resume_invariant", {}).get(
            "candidate_accepted_is_selected_incumbent"
        ) is not False
        or contract.get("rerun_policy", {}).get("algorithm_c_scenes")
        != list(predecessor.alg.SCENES)
        or contract.get("rerun_policy", {}).get("algorithm_b_required") is not False
    ):
        raise RuntimeError("Algorithm C v74 monotonic contract differs")
    # v11's dynamic gate was deliberately advanced after v25 to ignore only a
    # stale external identity while preserving every live interference check.
    # v26 binds that exact successor below.  Every other parent source remains
    # byte-for-byte frozen.
    for label, raw in mapping(
        parent.get("source_bindings"), "v73 source bindings",
    ).items():
        if label == "dynamic_noninterference_gate":
            continue
        row = mapping(raw, f"v73 {label} binding")
        path = (ROOT / str(row.get("path") or "")).resolve()
        if (
            not path.is_file()
            or path.is_symlink()
            or predecessor.alg.sha(path) != str(row.get("sha256") or "")
        ):
            raise RuntimeError(f"Algorithm C parent source binding differs: {path}")
    for label, binding in bindings.items():
        row = mapping(binding, f"v74 {label} binding")
        path = (ROOT / str(row.get("path") or "")).resolve()
        if (
            not path.is_file()
            or path.is_symlink()
            or predecessor.alg.sha(path) != str(row.get("sha256") or "")
        ):
            raise RuntimeError(f"Algorithm C v74 source binding differs: {path}")
    return {"parent": parent, "successor": contract}


def strict_verify_checkpoint(path: Path) -> dict[str, Any]:
    checkpoint = _ORIGINAL_VERIFY_TOP4_CHECKPOINT(path)
    if "--fresh-search" not in sys.argv:
        audit = chain_audit.audit_checkpoint(path)
        if audit.get("status") != "PASS":
            raise RuntimeError(
                "Algorithm C checkpoint global incumbent chain is not monotonic: "
                + "; ".join(audit.get("failures") or [])
            )
    return checkpoint


def argument(name: str) -> str:
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except (ValueError, IndexError) as error:
        raise RuntimeError(f"required controller argument absent: {name}") from error


def main() -> int:
    validate_contract()
    original_verify = predecessor.verify_top4_checkpoint
    original_validate = predecessor.validate_contract
    predecessor.verify_top4_checkpoint = strict_verify_checkpoint
    predecessor.validate_contract = lambda: predecessor.alg.load(PARENT_CONTRACT)
    try:
        result = predecessor.main()
    finally:
        predecessor.verify_top4_checkpoint = original_verify
        predecessor.validate_contract = original_validate
    if result != 0:
        return result
    run_root = ROOT / "history/mysql_four_scene_20260809" / argument("--run-id")
    audit = chain_audit.audit_run(run_root)
    chain_audit.atomic(run_root / "global_incumbent_chain_audit.json", audit)
    if audit.get("status") != "PASS":
        state_path = run_root / "run_state.json"
        state = predecessor.alg.load(state_path)
        state["status"] = "FAILED_CLOSED"
        state["stage"] = "FAILED"
        state["error"] = (
            "RuntimeError: global incumbent chain audit failed: "
            + "; ".join(audit.get("failures") or [])
        )
        state["converged"] = {
            name: False for name in mapping(state.get("converged"), "converged")
        }
        predecessor.alg.atomic(state_path, state)
        raise RuntimeError(state["error"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
