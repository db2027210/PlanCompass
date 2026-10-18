#!/usr/bin/env python3
"""Close a completed Algorithm-C memory-pair run after the v2 chain audit."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(relative_path: str) -> Path:
    path = (ROOT / relative_path).resolve()
    path.relative_to(ROOT)
    return path


def validate_binding(name: str, binding: dict[str, Any]) -> Path:
    path = resolve(str(binding["path"]))
    actual = sha256(path)
    expected = str(binding["sha256"])
    if actual != expected:
        raise RuntimeError(f"{name} sha256 mismatch: expected={expected} actual={actual}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    contract_path = resolve(args.contract)
    contract = load_json(contract_path)
    bindings = contract["source_bindings"]
    paths = {name: validate_binding(name, binding) for name, binding in bindings.items()}

    state = load_json(paths["completed_run_state"])
    audit = load_json(paths["global_incumbent_chain_audit_v2"])
    old_launch = load_json(paths["historical_failed_launcher_manifest"])

    expected_scenes = ["job_memory", "tpch_sf4_memory"]
    checks = {
        "state_complete": state.get("status") == "COMPLETE" and state.get("stage") == "FINISHED",
        "state_error_absent": state.get("error") is None,
        "exact_memory_pair": state.get("active_scenes") == expected_scenes
        and state.get("memory_pair_controller") is True,
        "outer_attempt_16": state.get("outer_attempt") == 16,
        "both_converged": state.get("converged") == {scene: True for scene in expected_scenes},
        "formal_evidence_eligible": state.get("formal_evidence_eligible") is True,
        "audit_pass": audit.get("status") == "PASS" and audit.get("failures") == [],
        "audit_exact_memory_pair": audit.get("active_scenes") == expected_scenes
        and audit.get("memory_pair_controller") is True,
        "audit_32_decisions": audit.get("decision_path_count") == 32,
        "audit_non_mutating": audit.get("measurement_or_optimizer_state_mutated") is False
        and audit.get("accepted_or_rejected_credit_mutated") is False,
        "final_qps_matches": audit.get("final_expected_qps")
        == {scene: state["incumbents"][scene]["db_qps"] for scene in expected_scenes},
        "historical_launcher_failed_only_post_run": old_launch.get("status") == "FAILED_CLOSED"
        and old_launch.get("returncode") == 1,
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise RuntimeError("memory-pair completion closure failed: " + ", ".join(failures))

    output_dir = resolve(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    result = {
        "schema_version": "mysql_c_memory_pair_completion_closure_v102",
        "status": "PASS",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "C",
        "active_scenes": expected_scenes,
        "completed_run": str(paths["completed_run_state"].relative_to(ROOT)),
        "completed_at": state["completed_at"],
        "outer_attempt": state["outer_attempt"],
        "accepted_rounds": state["accepted_rounds"],
        "rejected_outer_attempts": state["rejected_outer_attempts"],
        "incumbent_qps": audit["final_expected_qps"],
        "convergence_rule": state["completion_reason"],
        "checks": checks,
        "measurement_replayed": False,
        "measurement_or_optimizer_state_mutated": False,
        "historical_launcher_failure_class": "post_run_audit_v1_rejected_legitimate_memory_pair_shape",
        "historical_launcher_manifest_remains_immutable": True,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "contract": {
            "path": str(contract_path.relative_to(ROOT)),
            "sha256": sha256(contract_path),
        },
        "source_bindings": bindings,
    }
    result_path = output_dir / "completion_closure.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = sha256(result_path)
    (output_dir / "completion_closure.json.sha256").write_text(
        f"{digest}  completion_closure.json\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
