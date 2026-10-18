#!/usr/bin/env python3
"""Read-only validation of the TPC-H v50 liveness-fix upgrade boundary."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


REMOTE_V49 = Path("/tmp/tpch_v49_preparing_cycle_continuation.py")
LOCAL_V49 = Path(__file__).with_name("tpch_v49_preparing_cycle_continuation.py")
EXPECTED_V49_SHA256 = (
    "40231d64d94c98af5d98e28b9e27c82933aa46f9a36d4a4113d2cb7da747c405"
)
CONTRACT = "tpch_v50_liveness_fix_clean_preparing_cycle_upgrade_v1"
NEGATIVE_GATE_CONTRACT = "tpch_v44_deterministic_negative_gate_reexploration_v1"
ACQUISITION_SELECTION_CONTRACT = (
    "exact_structural_plan_regime_stability_probe5_diagnostic_v1"
)
REEXPLORE_PHRASE = (
    "probe-only trusted-evidence acquisition clean5 requires automatic "
    "reexploration"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_v49() -> Any:
    path = REMOTE_V49 if REMOTE_V49.is_file() else LOCAL_V49
    if not path.is_file() or sha256(path) != EXPECTED_V49_SHA256:
        raise RuntimeError("v49 boundary validator SHA mismatch")
    specification = importlib.util.spec_from_file_location(
        "tpch_v50_liveness_fix_v49",
        path,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import v49 boundary validator: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _fingerprint(item: dict[str, Any]) -> str:
    value = item.get("three_level_fingerprint") or {}
    return str(value.get("id") or "") if isinstance(value, dict) else ""


def _strict_probe5(selection: dict[str, Any]) -> bool:
    candidates = selection.get("top5") or []
    update_fields = (
        "accepted_update_allowed",
        "incumbent_update_allowed",
        "range_update_allowed",
        "gradient_update_allowed",
    )
    return bool(
        selection.get("selection_contract") == ACQUISITION_SELECTION_CONTRACT
        and selection.get("trusted_evidence_acquisition_mode") is True
        and selection.get("automatic_reexploration_required_after_clean5") is True
        and selection.get("diagnostic_only") is True
        and selection.get("formal_continuation") is False
        and selection.get("formal_admission_from_this_selection_allowed") is False
        and selection.get("unknown_plan_regime_trusted_allowed") is False
        and int(selection.get("maximum_real_replay_invocations", -1)) == 5
        and int(selection.get("trusted_slots", -1)) == 0
        and int(selection.get("probe_slots", -1)) == 5
        and selection.get("qps_used_for_inner_scoring") is False
        and all(selection.get(field) is False for field in update_fields)
        and isinstance(candidates, list)
        and len(candidates) == 5
        and all(
            isinstance(candidate, dict)
            and candidate.get("selection_role") == "probe"
            and candidate.get("trusted") is False
            and candidate.get("uncertain_probe") is True
            and candidate.get("trusted_evidence_acquisition_probe") is True
            and candidate.get("diagnostic_only") is True
            and candidate.get("qps_used_for_inner_scoring") is False
            and all(candidate.get(field) is False for field in update_fields)
            for candidate in candidates
        )
    )


def _clean_probe5(
    selection_path: Path,
    run_root: Path,
    expected_formal_sha256: str,
) -> dict[str, Any]:
    selection = load(selection_path)
    state_path = run_root / "diagnostic_state.json"
    summary_path = run_root / "diagnostic_summary.json"
    if not state_path.is_file() or not summary_path.is_file():
        raise RuntimeError("negative-gate diagnostic artifacts are incomplete")
    state = load(state_path)
    summary = load(summary_path)
    successful = summary.get("successful") or []
    candidates = selection.get("top5") or []
    selection_sha = sha256(selection_path)
    update_fields = (
        "accepted_update_applied",
        "incumbent_update_applied",
        "range_update_applied",
        "gradient_update_applied",
    )
    row_bindings = bool(
        isinstance(successful, list)
        and len(successful) == 5
        and all(
            isinstance(result, dict)
            and int(result.get("rank", -1)) == rank
            and result.get("selection_role") == "probe"
            and result.get("valid_replay") is True
            and result.get("technical_failure") is False
            and result.get("diagnostic_only") is True
            and result.get("accepted_update_eligible") is False
            and result.get("accepted_update_allowed") is False
            and result.get("incumbent_update_allowed") is False
            and result.get("range_update_allowed") is False
            and result.get("gradient_update_allowed") is False
            and result.get("qps_used_for_inner_scoring") is False
            and _fingerprint(result) == _fingerprint(candidates[rank - 1])
            and _fingerprint(result).startswith("three-level-sha256:")
            for rank, result in enumerate(successful, 1)
        )
    )
    if not (
        _strict_probe5(selection)
        and state.get("schema_version")
        == "tpch_exact_plan_regime_diagnostic_state_v1"
        and state.get("status") == "complete"
        and state.get("diagnostic_only") is True
        and int(state.get("accepted_count", -1)) == 1
        and int(state.get("physical_call_count", -1)) == 5
        and int(state.get("valid_replay_count", -1)) == 5
        and int(state.get("technical_failure_count", -1)) == 0
        and state.get("baseline_restored") is True
        and state.get("selection_sha256") == selection_sha
        and state.get("resume_state_sha256") == expected_formal_sha256
        and state.get("formal_state_mutation_allowed") is False
        and int(state.get("batch_added_accepted_count", -1)) == 0
        and state.get("full_selection_replay") is True
        and state.get("selection_replayed_ranks") == [1, 2, 3, 4, 5]
        and state.get("selection_excluded_ranks") == []
        and int(state.get("maximum_real_replay_invocations", -1)) == 5
        and summary.get("schema_version")
        == "tpch_exact_plan_regime_diagnostic_batch_summary_v1"
        and summary.get("diagnostic_only") is True
        and summary.get("formal_state_mutation_allowed") is False
        and int(summary.get("accepted_count", -1)) == 1
        and int(summary.get("batch_added_accepted_count", -1)) == 0
        and int(summary.get("physical_call_count", -1)) == 5
        and int(summary.get("valid_replay_count", -1)) == 5
        and int(summary.get("technical_failure_count", -1)) == 0
        and summary.get("baseline_restored") is True
        and summary.get("selection_sha256") == selection_sha
        and summary.get("full_selection_replay") is True
        and summary.get("selection_replayed_ranks") == [1, 2, 3, 4, 5]
        and summary.get("selection_excluded_ranks") == []
        and int(summary.get("maximum_real_replay_invocations", -1)) == 5
        and summary.get("technical_failures") == []
        and all(summary.get(field) is False for field in update_fields)
        and row_bindings
    ):
        raise RuntimeError("cycle5 acquisition diagnostic is not clean probe5")
    return {
        "selection": str(selection_path.resolve()),
        "selection_sha256": selection_sha,
        "run_root": str(run_root.resolve()),
        "state_sha256": sha256(state_path),
        "summary_sha256": sha256(summary_path),
        "resume_state_sha256": expected_formal_sha256,
        "physical_calls": 5,
        "valid_replays": 5,
        "technical_failures": 0,
    }


def _preworkload_cycle(
    v49: Any,
    v48: Any,
    cycle_root: Path,
) -> dict[str, Any]:
    if not cycle_root.is_dir():
        raise RuntimeError("next preparing-cycle directory is missing")
    v48.ensure_no_formal_started(cycle_root)
    forbidden = []
    unexpected = []
    observed_files = []
    for path in cycle_root.rglob("*"):
        relative = str(path.relative_to(cycle_root))
        if path.is_dir() and (
            path.name.startswith("attempt_")
            or path.name.startswith("diagnostic_")
            or path.name.startswith("formal_")
        ):
            forbidden.append(relative)
        if path.is_file():
            observed_files.append(relative)
            if not v49._allowed_preworkload_file(relative):
                unexpected.append(relative)
    if forbidden or unexpected:
        raise RuntimeError(
            "cycle is not a zero-workload preparing boundary: "
            f"forbidden={sorted(forbidden)} unexpected={sorted(unexpected)}"
        )
    for first_name, second_name, label in (
        ("config.a.json", "config.b.json", "config"),
        ("probe_top5.a.json", "probe_top5.b.json", "probe selection"),
    ):
        first = cycle_root / first_name
        second = cycle_root / second_name
        if first.exists() != second.exists():
            raise RuntimeError(f"partial deterministic {label} pair")
        if first.is_file() and first.read_bytes() != second.read_bytes():
            raise RuntimeError(f"deterministic {label} pair mismatch")
    expansion_root = cycle_root / "dynamic_expansion_v32"
    expansion_outputs = (
        expansion_root / "expanded_candidates.a.json",
        expansion_root / "expanded_candidates.b.json",
        expansion_root / "audit.json",
    )
    expansion_complete = all(path.is_file() for path in expansion_outputs)
    if any(path.exists() for path in expansion_outputs) and not expansion_complete:
        raise RuntimeError("partial expansion output set cannot be resumed implicitly")
    config = cycle_root / "config.a.json"
    probe = cycle_root / "probe_top5.a.json"
    if observed_files and not config.is_file():
        raise RuntimeError("preworkload artifacts exist without deterministic config")
    if probe.is_file() and not expansion_complete:
        raise RuntimeError("probe selection exists without completed expansion")
    protected_logs = [
        path
        for path in (
            cycle_root / "probe_builder_a.log",
            cycle_root / "probe_builder_b.log",
            expansion_root / "expand_a.log",
            expansion_root / "expand_b.log",
        )
        if path.is_file()
    ]
    return {
        "existing_config": config if config.is_file() else None,
        "existing_probe": probe if probe.is_file() else None,
        "expansion_complete": expansion_complete,
        "protected_logs": protected_logs,
        "observed_files": sorted(observed_files),
    }


def _negative_gate_boundary(
    v49: Any,
    v48: Any,
    v41: Any,
    paths: dict[str, Path],
    *,
    expected_state_sha: str,
    expected_formal_sha: str,
    expected_counter_sha: str,
    expected_cycle: int,
) -> dict[str, Any]:
    state_path = paths["state"]
    if not state_path.is_file() or sha256(state_path) != expected_state_sha:
        raise RuntimeError("negative-gate controller_state SHA mismatch")
    state = load(state_path)
    cycles = state.get("cycles") or []
    last_cycle = cycles[-1] if cycles else {}
    previous_cycle = expected_cycle - 1
    if not (
        state.get("schema_version") == "tpch_fresh10_target10_loop_v26"
        and state.get("status") == "preparing_cycle"
        and int(state.get("accepted_count", -1)) == 1
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_cycle", -1)) == expected_cycle
        and last_cycle.get("contract") == NEGATIVE_GATE_CONTRACT
        and int(last_cycle.get("cycle", -1)) == previous_cycle
        and last_cycle.get("status") == "reexplore_after_exact_repeat"
        and last_cycle.get("deterministic_negative_gate") is True
        and last_cycle.get("failure_stage") == "exact_repeat"
        and last_cycle.get("failure_reason") == REEXPLORE_PHRASE
        and int(last_cycle.get("accepted_before", -1)) == 1
        and int(last_cycle.get("accepted_after", -1)) == 1
        and int(last_cycle.get("physical_workload_calls", -1)) == 5
        and int(last_cycle.get("valid_replays", -1)) == 5
        and int(last_cycle.get("technical_failures", -1)) == 0
        and int(last_cycle.get("negative_gate_new_workload_calls", -1)) == 0
        and last_cycle.get("formal_started") is False
        and last_cycle.get("formal_state_mutated") is False
        and last_cycle.get("accepted_counter_mutated") is False
        and last_cycle.get("manual_candidate_injection") is False
        and last_cycle.get("thresholds_relaxed") is False
        and int(last_cycle.get("next_cycle", -1)) == expected_cycle
        and last_cycle.get("next_action")
        == "automatic_bounded_explain_reexploration"
        and last_cycle.get("role_gate_unchanged") == "trusted3_probe2"
        and int(last_cycle.get("per_diagnostic_physical_cap", -1)) == 5
        and last_cycle.get("qps_role") == "outer_audit_only_not_inner_score"
    ):
        raise RuntimeError("controller is not at the clean cycle5 negative gate")
    resume_state = Path(str(state.get("resume_state") or ""))
    if (
        str(resume_state.resolve()) != str(last_cycle.get("formal_state") or "")
        or last_cycle.get("formal_state_sha256") != expected_formal_sha
    ):
        raise RuntimeError("negative-gate formal-state binding mismatch")
    v48.validate_counter(paths["counter"], expected_counter_sha)
    v48.validate_formal_authority(resume_state, expected_formal_sha)

    cycle_root = v41.V41_LOOP / f"cycle_{previous_cycle:03d}"
    v48.ensure_no_formal_started(cycle_root)
    audit_path = Path(str(last_cycle.get("negative_gate_path") or ""))
    failure_path = Path(str(last_cycle.get("failure_evidence_path") or ""))
    if not (
        audit_path.is_file()
        and audit_path.resolve() == (cycle_root / "v44_deterministic_negative_gate.json").resolve()
        and _within(failure_path, cycle_root)
        and failure_path.is_file()
        and sha256(audit_path) == last_cycle.get("negative_gate_sha256")
        and sha256(failure_path) == last_cycle.get("failure_evidence_sha256")
        and REEXPLORE_PHRASE in failure_path.read_text(encoding="utf-8")
    ):
        raise RuntimeError("negative-gate audit/failure SHA binding mismatch")
    audit = load(audit_path)
    if any(last_cycle.get(key) != value for key, value in audit.items()):
        raise RuntimeError("negative-gate state/audit payload mismatch")
    diagnostics = last_cycle.get("diagnostics") or []
    if not isinstance(diagnostics, list) or len(diagnostics) != 1:
        raise RuntimeError("exact-repeat negative gate must bind one clean diagnostic")
    sealed = diagnostics[0]
    selection = Path(str(sealed.get("selection") or ""))
    run_root = Path(str(sealed.get("run_root") or ""))
    if not (_within(selection, cycle_root) and _within(run_root, cycle_root)):
        raise RuntimeError("negative-gate diagnostic escaped its cycle")
    clean = _clean_probe5(selection, run_root, expected_formal_sha)
    if any(sealed.get(key) != value for key, value in clean.items()):
        raise RuntimeError("negative-gate clean5 evidence binding mismatch")

    next_cycle_root = v41.V41_LOOP / f"cycle_{expected_cycle:03d}"
    preworkload = _preworkload_cycle(v49, v48, next_cycle_root)
    return {
        "state": state,
        "cycle_root": next_cycle_root,
        "evidence_selection": selection,
        "evidence_run": run_root,
        "builder_selection": selection,
        "builder_summary": run_root / "diagnostic_summary.json",
        "existing_config": preworkload["existing_config"],
        "existing_probe": preworkload["existing_probe"],
        "expansion_complete": preworkload["expansion_complete"],
        "protected_logs": preworkload["protected_logs"],
        "preflight": {
            "status": "ready_to_continue_clean_preparing_cycle",
            "boundary_kind": "cycle5_exact_repeat_negative_gate",
            "cycle": expected_cycle,
            "accepted_count": 1,
            "controller_state_sha256": expected_state_sha,
            "formal_state_sha256": expected_formal_sha,
            "accepted_counter_sha256": expected_counter_sha,
            "evidence_selection": str(selection.resolve()),
            "evidence_selection_sha256": sha256(selection),
            "builder_selection": str(selection.resolve()),
            "builder_selection_sha256": sha256(selection),
            "builder_summary": str((run_root / "diagnostic_summary.json").resolve()),
            "builder_summary_sha256": sha256(run_root / "diagnostic_summary.json"),
            "observed_preworkload_files": preworkload["observed_files"],
            "existing_config_reused": preworkload["existing_config"] is not None,
            "existing_probe_reused": preworkload["existing_probe"] is not None,
            "completed_expansion_reused": preworkload["expansion_complete"],
            "physical_explain_calls_repeated": 0,
            "protected_logs": [
                str(path.resolve()) for path in preworkload["protected_logs"]
            ],
            "new_workload_calls": 0,
            "formal_state_mutated": False,
            "accepted_counter_mutated": False,
            "manual_candidate_injection": False,
            "thresholds_relaxed": False,
            "next_action": "resume_v48_run_loop_at_existing_cycle",
        },
    }


def validate_boundary(
    v49: Any,
    v48: Any,
    v41: Any,
    paths: dict[str, Path],
    *,
    expected_state_sha: str,
    expected_formal_sha: str,
    expected_counter_sha: str,
    expected_cycle: int,
) -> dict[str, Any]:
    state = load(paths["state"])
    cycles = state.get("cycles") or []
    last_cycle = cycles[-1] if cycles else {}
    if last_cycle.get("contract") == NEGATIVE_GATE_CONTRACT:
        return _negative_gate_boundary(
            v49,
            v48,
            v41,
            paths,
            expected_state_sha=expected_state_sha,
            expected_formal_sha=expected_formal_sha,
            expected_counter_sha=expected_counter_sha,
            expected_cycle=expected_cycle,
        )
    return v49.validate_boundary(
        v48,
        v41,
        paths,
        expected_state_sha=expected_state_sha,
        expected_formal_sha=expected_formal_sha,
        expected_counter_sha=expected_counter_sha,
        expected_cycle=expected_cycle,
    )


def validate(
    expected_controller_state_sha256: str,
    expected_formal_state_sha256: str,
    expected_counter_sha256: str,
    expected_cycle: int,
) -> dict[str, Any]:
    v49 = import_v49()
    v48 = v49.import_v48()
    v41 = v48.verified_v41()
    paths = v48.current_paths(v41)
    boundary = validate_boundary(
        v49,
        v48,
        v41,
        paths,
        expected_state_sha=expected_controller_state_sha256,
        expected_formal_sha=expected_formal_state_sha256,
        expected_counter_sha=expected_counter_sha256,
        expected_cycle=expected_cycle,
    )
    preflight = dict(boundary["preflight"])
    preflight.update(
        {
            "contract": CONTRACT,
            "safe_to_replace_inactive_v50_code": True,
            "new_workload_calls": 0,
            "formal_state_mutated": False,
            "accepted_counter_mutated": False,
            "required_old_controller_status": "preparing_cycle",
            "required_next_action": "deploy_then_run_new_v50_preflight",
        }
    )
    return preflight


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-controller-state-sha256", required=True)
    parser.add_argument("--expected-formal-state-sha256", required=True)
    parser.add_argument("--expected-counter-sha256", required=True)
    parser.add_argument("--expected-cycle", type=int, required=True)
    args = parser.parse_args()
    for value in (
        args.expected_controller_state_sha256,
        args.expected_formal_state_sha256,
        args.expected_counter_sha256,
    ):
        if len(value) != 64:
            raise RuntimeError("expected SHA256 values must contain 64 characters")
    if args.expected_cycle <= 0:
        raise RuntimeError("expected cycle must be positive")
    print(
        json.dumps(
            validate(
                args.expected_controller_state_sha256,
                args.expected_formal_state_sha256,
                args.expected_counter_sha256,
                args.expected_cycle,
            ),
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
