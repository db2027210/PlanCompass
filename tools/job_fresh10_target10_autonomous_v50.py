#!/usr/bin/env python3
"""JOB target10 v50: seal short technical batches and rediscover fresh points."""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping, Sequence


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
import job_fresh10_target10_autonomous_v49 as v49  # noqa: E402


EXPECTED_V49_SHA256 = (
    "3688f00b0a32c490f5dd3781b26642f48361c3ba541256f9855730284016035c"
)
HISTORICAL_STATE = v49.STATE
HISTORICAL_ACCEPTED_COUNTER = v49.ACCEPTED_COUNTER
HISTORICAL_INITIAL_FORMAL = v49.INITIAL_FORMAL
FRESH_ROOT = v49.T / "autonomous_target10_v50_fresh0"
FRESH_STATE = FRESH_ROOT / "package/formal_state.json"
FRESH_ACCEPTED_COUNTER = FRESH_ROOT / "formal_search_accepted_counter.txt"
CAMPAIGN_CONTRACT = "job_v50_fresh0_historical3_excluded_v1"
v49.ROOT = FRESH_ROOT
v49.STATE = FRESH_STATE
v49.ACCEPTED_COUNTER = FRESH_ACCEPTED_COUNTER
NEUTRAL_FLAGS = {
    "accepted": False,
    "gradient": False,
    "incumbent": False,
    "range": False,
}
MAX_DIAGNOSTIC_BATCHES = 5
BASELINE_SCORE = 4.416709205648302
BASELINE_PARAMS = {
    "cpu_index_tuple_cost": 0.0005,
    "cpu_operator_cost": 0.0025,
    "cpu_tuple_cost": 0.01,
    "parallel_setup_cost": 7000.0,
    "parallel_tuple_cost": 0.001,
    "random_page_cost": 4.0,
    "seq_page_cost": 1.0,
}
CONTROLLER_VERSION = "v50"
FROZEN_BASE_RUNNER_V6 = v49.BASE_RUNNER
BASE_RUNNER_V7 = TOOLS_DIR / "run_job_round8_strict_serial_base_v7_explicit_preclaim.py"
EXPECTED_BASE_RUNNER_V7_SHA256 = (
    "839833efdde6a1294fc43bdd07012562a768039e65c1a146513a1d7911611dd4"
)
REMOTE_BRIDGE = (
    v49.B.parents[3]
    / "launch_outer_attempt4_formal_dbqps_v1_retry3/formal_job_outer_replay.py"
)
EXPECTED_REMOTE_BRIDGE_SHA256 = (
    "240235dac61731fc4355ba21ffc7dd701d3c2154da7b6e49933ea2f9e01f5b0a"
)
ROBUST_RUNNER_V9 = TOOLS_DIR / "run_job_round8_strict_serial_robust_v9_state_guard.py"
ROBUST_RUNNER_V8_MODULE = TOOLS_DIR / "run_job_round8_strict_serial_robust_v8_apply_failure.py"
EXPECTED_ROBUST_RUNNER_V9_SHA256 = (
    "e717449a109669d6ed274e29d5aaf9f4285af8b37c586aeb0ad768970ba02693"
)
v49.ROBUST_RUNNER = ROBUST_RUNNER_V9
v49.BASE_RUNNER = BASE_RUNNER_V7


class RepeatTechnicalFailure(v49.FailClosed):
    """A sealed repeat batch that requires fresh discovery before more replay."""

    def __init__(self, attempt: int, root: Path) -> None:
        super().__init__(f"repeat technical batch sealed: {root}")
        self.attempt = attempt
        self.root = root


def rebase_training_discovery_to_fresh0(value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep historical ranking/calibration, but bind formal deltas to baseline."""
    result = copy.deepcopy(dict(value))
    state = v49.read_json(FRESH_STATE)
    if (
        int(state.get("accepted_count", -1)) != 0
        or float(state.get("incumbent_score", -1)) != BASELINE_SCORE
        or state.get("incumbent_params") != BASELINE_PARAMS
        or state.get("range_by_parameter")
        != {
            name: {"min": amount, "max": amount}
            for name, amount in BASELINE_PARAMS.items()
        }
    ):
        raise v49.FailClosed("fresh0 baseline state is not frozen")
    training_authority = copy.deepcopy(result.get("authoritative_formal_state"))
    result["historical_training_authority"] = training_authority
    result["historical_training_only"] = True
    result["historical_incumbent_or_range_inherited"] = False
    result["authoritative_formal_state"] = {
        "path": str(FRESH_STATE),
        "sha256": v49.sha256_file(FRESH_STATE),
        "accepted_count": 0,
        "incumbent_score": BASELINE_SCORE,
        "incumbent_params": dict(BASELINE_PARAMS),
        "range_by_parameter": copy.deepcopy(state["range_by_parameter"]),
        "historical_accepted_excluded": 3,
    }

    collections = []
    for name in ("top5", "ranked_candidates"):
        candidates = result.get(name)
        if isinstance(candidates, list):
            collections.append(candidates)
    for candidates in collections:
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise v49.FailClosed("generated candidate is not an object")
            params = candidate.get("candidate_params")
            if not isinstance(params, Mapping) or set(params) != set(BASELINE_PARAMS):
                raise v49.FailClosed("generated candidate parameter vector is incomplete")
            normalized = {name: float(params[name]) for name in BASELINE_PARAMS}
            candidate["initial_params"] = dict(BASELINE_PARAMS)
            candidate["initial_score"] = BASELINE_SCORE
            candidate["score_improvement"] = float(
                candidate.get("candidate_score", candidate.get("final_score"))
            ) - BASELINE_SCORE
            candidate["candidate_score_delta"] = candidate["score_improvement"]
            candidate["delta_param"] = {
                name: {
                    "absolute": normalized[name] - baseline,
                    "after_clip": normalized[name],
                    "before": baseline,
                    "clipped": False,
                    "proposed_before_clip": normalized[name],
                    "relative": (normalized[name] - baseline) / baseline,
                }
                for name, baseline in BASELINE_PARAMS.items()
            }
            candidate["source_is_formal_incumbent_anchor"] = False
            candidate["historical_training_only"] = True
            candidate["fresh0_baseline_rebased"] = True
    return result


def write_rebased_discovery(raw_path: Path, output: Path) -> None:
    rebased = rebase_training_discovery_to_fresh0(v49.read_json(raw_path))
    if output.exists():
        if v49.read_json(output) != rebased:
            raise v49.FailClosed("sealed fresh0 rebased discovery changed")
    else:
        v49.atomic_json(output, rebased)


def write_adaptive_discovery(raw_path: Path, output: Path) -> None:
    if output.exists():
        if output.read_bytes() != raw_path.read_bytes():
            raise v49.FailClosed("sealed adaptive discovery changed")
    else:
        shutil.copy2(raw_path, output)


def _fingerprint(record: Mapping[str, Any]) -> str:
    binding = record.get("candidate_binding") or {}
    value = binding.get("fingerprint_id")
    if value:
        return str(value)
    fingerprint = binding.get("three_level_fingerprint") or {}
    return str(fingerprint.get("id") or "")


def validate_diagnostic_v50(
    selection_path: Path,
    summary_path: Path,
    runner_log_path: Path,
    state_sha_before: str,
    counters_before: Mapping[str, int],
) -> tuple[bool, dict[str, Any]]:
    """Accept a sealed 1..5-attempt batch only when an early stop is technical."""
    summary = v49.read_json(summary_path)
    physical = int(summary.get("physical_invocation_count", -1))
    real_physical = int(summary.get("real_workload_invocation_count", -1))
    valid = int(summary.get("successful_real_replay_count", -1))
    technical = int(summary.get("technical_failure_count", -1))
    attempts = valid + technical
    successful = summary.get("successful") or []
    failures = summary.get("technical_failures") or []
    invocations = summary.get("invocations") or []
    if (
        not all(isinstance(items, list) for items in (successful, failures, invocations))
        or not all(
            isinstance(item, Mapping)
            for items in (successful, failures, invocations)
            for item in items
        )
    ):
        raise v49.FailClosed(f"diagnostic replay ledger type violation: {summary_path}")
    successful_ranks = [int(item.get("rank", -1)) for item in successful]
    failure_ranks = [int(item.get("rank", -1)) for item in failures]
    invocation_ranks = [int(item.get("rank", -1)) for item in invocations]
    attempt_ranks = successful_ranks + failure_ranks
    missing_ranks = sorted(set(failure_ranks) - set(invocation_ranks))
    declared_preclaim_ranks = summary.get(
        "pre_invocation_technical_failure_ranks", []
    )
    preclaim_is_sealed = bool(
        summary.get("preclaim_no_ordinal_claim") is True
        and isinstance(declared_preclaim_ranks, list)
        and sorted(int(rank) for rank in declared_preclaim_ranks) == missing_ranks
        and int(summary.get("pre_invocation_technical_failure_count", -1))
        == len(missing_ranks)
    )
    if not runner_log_path.is_file():
        raise v49.FailClosed(f"diagnostic runner log is missing: {runner_log_path}")
    runner_log = runner_log_path.read_text(encoding="utf-8", errors="replace")
    apply_mismatch = "ValueError: physical invocation ledger is inconsistent"
    early_stop_is_technical = attempts == 5 or technical > 0
    ledger_valid = bool(
        1 <= attempts <= 5
        and early_stop_is_technical
        and len(successful) == valid
        and len(failures) == technical
        and len(invocations) == physical
        and real_physical == physical
        and len(set(attempt_ranks)) == len(attempt_ranks)
        and sorted(attempt_ranks) == list(range(1, attempts + 1))
        and len(set(invocation_ranks)) == len(invocation_ranks)
        and set(successful_ranks).issubset(set(invocation_ranks))
        and set(invocation_ranks).issubset(set(attempt_ranks))
        and len(missing_ranks) == attempts - physical
        and all(
            item.get("technical_failure") is True
            and item.get("accepted") is False
            and item.get("gradient_update_applied") is False
            and item.get("incumbent_update_applied") is False
            and item.get("range_update_applied") is False
            for item in failures
        )
        and all(
            item.get("technical_failure") is False
            and item.get("accepted") is False
            and item.get("gradient_update_applied") is False
            and item.get("incumbent_update_applied") is False
            and item.get("range_update_applied") is False
            for item in successful
        )
        and (
            (not missing_ranks and "Traceback (most recent call last)" not in runner_log)
            or (
                bool(missing_ranks)
                and (
                    runner_log.rstrip().endswith(apply_mismatch)
                    or (
                        preclaim_is_sealed
                        and '"preclaim_no_ordinal_claim": true' in runner_log
                        and "Traceback (most recent call last)" not in runner_log
                    )
                )
            )
        )
    )
    if (
        summary.get("diagnostic_only") is not True
        or summary.get("selection_sha256") != v49.sha256_file(selection_path)
        or not ledger_valid
        or not (0 <= valid <= physical <= attempts <= 5)
        or summary.get("baseline_params_restored") is not True
        or (summary.get("baseline_restore") or {}).get("success") is not True
        or summary.get("initial_state") != summary.get("final_state")
        or summary.get("formal_update_flags") != NEUTRAL_FLAGS
        or v49.sha256_file(v49.STATE) != state_sha_before
        or v49.counter(v49.GLOBAL_COUNTER) - int(counters_before["global"])
        != physical
        or v49.counter(v49.FORMAL_REPLAY_COUNTER)
        != int(counters_before["formal_replay"])
        or v49.counter(v49.ACCEPTED_COUNTER) != int(counters_before["accepted"])
    ):
        raise v49.FailClosed(f"diagnostic replay contract violation: {summary_path}")
    return physical == 5 and valid == 5 and technical == 0, summary


ORIGINAL_RUN_DIAGNOSTIC = v49.run_diagnostic


def ensure_batch_sequence(phase_root: Path) -> int:
    sequence_path = phase_root / "v50_batch_sequence.json"
    cycle_root = next(
        (
            parent
            for parent in (phase_root, *phase_root.parents)
            if parent.name.startswith("cycle_")
        ),
        None,
    )
    if cycle_root is None:
        raise v49.FailClosed("diagnostic phase is outside a cycle root")
    existing = []
    for path in cycle_root.rglob("v50_batch_sequence.json"):
        value = v49.read_json(path)
        sequence = int(value.get("sequence", -1))
        if sequence <= 0:
            raise v49.FailClosed(f"invalid v50 batch sequence: {path}")
        existing.append((sequence, path))
    if sequence_path.is_file():
        sequence = int(v49.read_json(sequence_path).get("sequence", -1))
        if sum(1 for observed, _path in existing if observed == sequence) != 1:
            raise v49.FailClosed("duplicate v50 diagnostic batch sequence")
        return sequence
    sequence = max((observed for observed, _path in existing), default=0) + 1
    v49.atomic_json(
        sequence_path,
        {
            "schema_version": "job_v50_diagnostic_batch_sequence_v1",
            "sequence": sequence,
            "phase_root": str(phase_root.resolve()),
        },
    )
    return sequence


def run_diagnostic_v50(
    selection: Path,
    replay: Path,
    phase_root: Path,
) -> tuple[bool, dict[str, Any]]:
    batch_sequence = ensure_batch_sequence(phase_root)
    audit_path = phase_root / "diagnostic_audit.json"
    audit_existed = audit_path.exists()
    clean5, summary = ORIGINAL_RUN_DIAGNOSTIC(selection, replay, phase_root)
    if not audit_existed and audit_path.is_file():
        audit = v49.read_json(audit_path)
        attempted = [
            identifier
            for identifier in (
                _fingerprint(item)
                for item in (summary.get("successful") or [])
                + (summary.get("technical_failures") or [])
            )
            if identifier
        ]
        failed = [
            identifier
            for identifier in (
                _fingerprint(item) for item in summary.get("technical_failures") or []
            )
            if identifier
        ]
        if len(attempted) != len(set(attempted)) or (
            int(summary.get("technical_failure_count", 0)) > 0 and not failed
        ):
            raise v49.FailClosed("diagnostic fingerprint quarantine ledger is incomplete")
        audit.update(
            {
                "schema_version": "job_v50_state_neutral_diagnostic_audit_v1",
                "attempted_fingerprints": attempted,
                "technical_failure_fingerprints": failed,
                "fresh_discovery_required": not clean5,
                "diagnostic_batch_sequence": batch_sequence,
            }
        )
        v49.atomic_json(audit_path, audit)
    return clean5, summary


def _selection_fingerprints(path: Path) -> set[str]:
    return v49.fingerprint_set(path)


def validate_candidate_reachability(path: Path) -> dict[str, Any]:
    """Reject five fingerprints that do not represent five reachable points."""
    selection = v49.read_json(path)
    candidates = v49.candidate_list(selection)
    state = v49.read_json(FRESH_STATE)
    incumbent = state.get("incumbent_params")
    ranges = selection.get("candidate_generation_ranges")
    if (
        not isinstance(incumbent, Mapping)
        or set(incumbent) != set(BASELINE_PARAMS)
        or not isinstance(ranges, Mapping)
        or set(ranges) != set(BASELINE_PARAMS)
    ):
        raise v49.FailClosed("candidate reachability authority is incomplete")
    parameter_ids: set[str] = set()
    changed_dimensions: set[str] = set()
    for candidate in candidates:
        params = candidate.get("candidate_params")
        delta = candidate.get("delta_param")
        if (
            not isinstance(params, Mapping)
            or set(params) != set(BASELINE_PARAMS)
            or not isinstance(delta, Mapping)
            or set(delta) != set(BASELINE_PARAMS)
        ):
            raise v49.FailClosed("candidate reachability vector is incomplete")
        normalized = {name: float(params[name]) for name in BASELINE_PARAMS}
        parameter_ids.add(
            json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        )
        if v49.same_params(normalized, incumbent):
            raise v49.FailClosed("candidate discovery contains the incumbent point")
        for name, value in normalized.items():
            bounds = ranges.get(name)
            if not isinstance(bounds, Mapping):
                raise v49.FailClosed("candidate generation range is malformed")
            low = float(bounds.get("min_value"))
            high = float(bounds.get("max_value"))
            if low > high or value < low - 1e-15 or value > high + 1e-15:
                raise v49.FailClosed(f"candidate is outside generation range: {name}")
            before = float(incumbent[name])
            record = delta[name]
            if (
                not isinstance(record, Mapping)
                or not math.isclose(float(record.get("before")), before, rel_tol=0.0, abs_tol=1e-15)
                or not math.isclose(float(record.get("after_clip")), value, rel_tol=0.0, abs_tol=1e-15)
                or not math.isclose(float(record.get("absolute")), value - before, rel_tol=0.0, abs_tol=1e-15)
            ):
                raise v49.FailClosed(f"candidate delta is not state-bound: {name}")
            if not math.isclose(value, before, rel_tol=0.0, abs_tol=1e-15):
                changed_dimensions.add(name)
    if len(parameter_ids) != 5:
        raise v49.FailClosed("five fingerprints do not bind five distinct parameter points")
    point_range = all(
        float((state["range_by_parameter"][name])["min"])
        == float((state["range_by_parameter"][name])["max"])
        for name in BASELINE_PARAMS
    )
    if point_range and len(changed_dimensions) < 2:
        raise v49.FailClosed(
            "fresh point-range exploration is not reachable in two dimensions"
        )
    return {
        "distinct_parameter_points": len(parameter_ids),
        "changed_dimensions": sorted(changed_dimensions),
        "formal_range_is_point": point_range,
        "point_range_escape_is_explain_only": point_range,
    }


def evaluate_gate_v50(target_root: Path) -> tuple[bool, Path]:
    """Recompute the zero-call gate twice and bind it to exact inputs."""
    passed, output = v49.evaluate_gate(target_root)
    second = target_root / "entry_gate_raw.second.json"
    if not second.exists():
        v49.run_logged(
            [
                sys.executable,
                str(v49.GATE_TOOL),
                "--selection",
                str(target_root / "top5.json"),
                "--summary",
                str(target_root / "replay/summary.json"),
                "--output",
                str(second),
            ],
            target_root / "gate.second.log",
            timeout_seconds=600,
            allowed_returncodes=(0, 2),
        )
    if output.read_bytes() != second.read_bytes():
        raise v49.FailClosed("entry gate is stale or nondeterministic")
    gate = v49.read_json(output)
    checks = gate.get("checks")
    recomputed_passed = bool(
        gate.get("passed") is True
        and isinstance(checks, Mapping)
        and checks
        and all(value is True for value in checks.values())
    )
    if passed != recomputed_passed:
        raise v49.FailClosed("entry gate pass decision changed during binding")
    audit = {
        "schema_version": "job_v50_exact_gate_binding_v1",
        "selection_sha256": v49.sha256_file(target_root / "top5.json"),
        "summary_sha256": v49.sha256_file(target_root / "replay/summary.json"),
        "gate_sha256": v49.sha256_file(output),
        "second_gate_sha256": v49.sha256_file(second),
        "passed": recomputed_passed,
        "new_workload_calls": 0,
    }
    audit_path = target_root / "entry_gate_binding_audit.json"
    if audit_path.exists():
        if v49.read_json(audit_path) != audit:
            raise v49.FailClosed("sealed entry gate binding changed")
    else:
        v49.atomic_json(audit_path, audit)
    return recomputed_passed, output


def prepare_formal_v50(*args: Any, **kwargs: Any) -> Path:
    """Package the guarded formal runner and its frozen v8 dependency."""
    cycle_root = Path(args[0])
    package = cycle_root / "formal/package"
    package.mkdir(parents=True, exist_ok=True)
    dependency = package / ROBUST_RUNNER_V8_MODULE.name
    if not dependency.exists():
        shutil.copy2(ROBUST_RUNNER_V8_MODULE, dependency)
    if v49.sha256_file(dependency) != v49.sha256_file(ROBUST_RUNNER_V8_MODULE):
        raise v49.FailClosed("formal robust-v8 dependency copy mismatch")
    return v49.prepare_formal(*args, **kwargs)


def build_retry_exclusion(cycle_root: Path, replay_attempt: int) -> Path:
    inventory_path = cycle_root / "global_exclusion.json"
    inventory = v49.read_json(inventory_path)
    identifiers = {
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in inventory.get("fingerprints") or []
        if isinstance(item, Mapping)
    }
    identifiers.discard("")
    sources = list(inventory.get("sources") or [])
    prior_roots = sorted(cycle_root.glob("discovery_replay_[0-9][0-9]"))
    prior_roots.extend(sorted(cycle_root.glob("repeat_[0-9][0-9]")))
    for root in prior_roots:
        selection = root / "top5.json"
        if not selection.is_file():
            continue
        identifiers.update(_selection_fingerprints(selection))
        sources.append({"path": str(selection), "sha256": v49.sha256_file(selection)})
    output = cycle_root / f"fresh_discovery_{replay_attempt:02d}/exclusion.json"
    payload = {
        "schema_version": "job_v50_fresh_diagnostic_exclusion_v1",
        "sources": sources,
        "fingerprint_count": len(identifiers),
        "fingerprints": [
            {"three_level_fingerprint": {"id": identifier}}
            for identifier in sorted(identifiers)
        ],
    }
    if output.exists():
        if v49.read_json(output) != payload:
            raise v49.FailClosed("sealed fresh-discovery exclusion changed")
    else:
        v49.atomic_json(output, payload)
    return output


def generate_fresh_discovery(
    cycle_root: Path,
    replay_attempt: int,
    *,
    evidence_selection: Path,
    evidence_events: Path,
    incumbent_events: Path,
    point_result: Path,
    state_sha_before: str,
) -> Path:
    fresh_root = cycle_root / f"fresh_discovery_{replay_attempt:02d}"
    fresh_root.mkdir(parents=True, exist_ok=True)
    output = fresh_root / "top5.json"
    second = fresh_root / "top5.second.json"
    raw_a = fresh_root / "historical_training_a.json"
    raw_b = fresh_root / "historical_training_b.json"
    exclusion = build_retry_exclusion(cycle_root, replay_attempt)
    fresh_state = v49.read_json(FRESH_STATE)
    bootstrap_training_mode = int(fresh_state.get("accepted_count", -1)) == 0
    generator_state = HISTORICAL_STATE if bootstrap_training_mode else FRESH_STATE
    excluded = {
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in v49.read_json(exclusion).get("fingerprints") or []
        if isinstance(item, Mapping)
    }
    excluded.discard("")
    command = [
        sys.executable,
        str(v49.GENERATOR),
        "--history",
        str(v49.HISTORY),
        "--evidence-top5",
        str(evidence_selection),
        "--events",
        str(evidence_events),
        "--formal-state",
        str(generator_state),
        "--incumbent-events",
        str(incumbent_events),
        "--config",
        str(v49.CONFIG),
        "--capture",
        str(v49.CAPTURE),
        "--incumbent-point-result",
        str(point_result),
        "--code-root",
        str(v49.CODE_ROOT),
        "--exclude",
        str(exclusion),
        "--mode",
        "diagnostic",
        "--force-probe-discovery",
    ]
    for target, log_name in ((raw_a, "generator_a.log"), (raw_b, "generator_b.log")):
        if not target.exists():
            v49.run_logged(
                [*command, "--output", str(target)],
                fresh_root / log_name,
                timeout_seconds=7200,
            )
    if bootstrap_training_mode:
        write_rebased_discovery(raw_a, output)
        write_rebased_discovery(raw_b, second)
    else:
        write_adaptive_discovery(raw_a, output)
        write_adaptive_discovery(raw_b, second)
    if output.read_bytes() != second.read_bytes():
        raise v49.FailClosed("fresh successor discovery is not deterministic")
    v49.validate_top5(output, roles=("probe",) * 5, diagnostic_only=True)
    identifiers = _selection_fingerprints(output)
    if len(identifiers) != 5 or identifiers & excluded:
        raise v49.FailClosed("fresh discovery repeated an excluded/failed fingerprint")
    if v49.sha256_file(v49.STATE) != state_sha_before:
        raise v49.FailClosed("fresh EXPLAIN discovery mutated formal state")
    reachability = validate_candidate_reachability(output)
    v49.atomic_json(
        fresh_root / "discovery_audit.json",
        {
            "status": (
                "five_fresh0_noncorner_probe_fingerprints"
                if replay_attempt == 1
                else "five_fresh_noncorner_probe_fingerprints_after_technical"
            ),
            "replay_attempt": replay_attempt,
            "selection_sha256": v49.sha256_file(output),
            "fingerprints": sorted(identifiers),
            "excluded_fingerprint_count_before": len(excluded),
            "new_workload_calls": 0,
            "formal_state_sha256": state_sha_before,
            "bootstrap_historical_training_mode": bootstrap_training_mode,
            "generator_state_path": str(generator_state),
            "candidate_reachability": reachability,
        },
    )
    return output


def obtain_clean_discovery_v50(
    initial_discovery: Path,
    cycle_root: Path,
    *,
    start_attempt: int = 1,
    evidence_selection: Path,
    evidence_events: Path,
    incumbent_events: Path,
    point_result: Path,
    state_sha_before: str,
) -> tuple[Path, Path, int]:
    current = initial_discovery
    previous_ids: set[str] = set()
    if start_attempt < 1 or start_attempt > MAX_DIAGNOSTIC_BATCHES:
        raise v49.FailClosed("fresh discovery start attempt is outside 1..5")
    for attempt in range(start_attempt, MAX_DIAGNOSTIC_BATCHES + 1):
        root = cycle_root / f"discovery_replay_{attempt:02d}"
        selection = root / "top5.json"
        root.mkdir(parents=True, exist_ok=True)
        if selection.exists():
            # Existing v49 replay02 is quarantined and may overlap replay01;
            # it is validated/sealed but never invoked again by v50.
            if not (root / "replay/summary.json").is_file() and selection.read_bytes() != current.read_bytes():
                raise v49.FailClosed("unsealed discovery replay selection changed")
            current = selection
        else:
            shutil.copy2(current, selection)
        clean5, summary = run_diagnostic_v50(selection, root / "replay", root)
        current_ids = _selection_fingerprints(selection)
        if clean5:
            gate_discovery_audit = {
                "schema_version": "job_v50_clean_discovery_binding_v1",
                "status": "five_fresh_noncorner_fingerprints_clean5",
                "selection_sha256": v49.sha256_file(selection),
                "fingerprints": sorted(current_ids),
                "formal_state_sha256": state_sha_before,
                "diagnostic_summary_sha256": v49.sha256_file(
                    root / "replay/summary.json"
                ),
                "new_workload_calls": 0,
            }
            gate_audit_path = root / "discovery_audit.json"
            if gate_audit_path.exists():
                if v49.read_json(gate_audit_path) != gate_discovery_audit:
                    raise v49.FailClosed("clean discovery gate binding changed")
            else:
                v49.atomic_json(gate_audit_path, gate_discovery_audit)
            return selection, root, attempt
        if int(summary.get("technical_failure_count", 0)) <= 0:
            raise v49.FailClosed("non-clean diagnostic stopped without technical failure")
        previous_ids.update(current_ids)
        if attempt == MAX_DIAGNOSTIC_BATCHES:
            break
        next_root = cycle_root / f"discovery_replay_{attempt + 1:02d}"
        next_selection = next_root / "top5.json"
        if next_selection.is_file():
            if not (next_root / "replay/summary.json").is_file():
                raise v49.FailClosed("partial next discovery replay cannot be replaced")
            current = next_selection
            continue
        current = generate_fresh_discovery(
            cycle_root,
            attempt + 1,
            evidence_selection=evidence_selection,
            evidence_events=evidence_events,
            incumbent_events=incumbent_events,
            point_result=point_result,
            state_sha_before=state_sha_before,
        )
        if _selection_fingerprints(current) & previous_ids:
            raise v49.FailClosed("fresh discovery repeated a prior diagnostic fingerprint")
    raise v49.FailClosed("five fresh discovery diagnostic batches exhausted without clean5")


def obtain_passed_repeat_v50(
    discovery_root: Path,
    cycle_root: Path,
    *,
    start_attempt: int = 1,
) -> tuple[Path, Path, Path, int]:
    source = discovery_root
    if start_attempt < 1 or start_attempt > MAX_DIAGNOSTIC_BATCHES:
        raise v49.FailClosed("repeat start attempt is outside 1..5")
    for attempt in range(start_attempt, MAX_DIAGNOSTIC_BATCHES + 1):
        target = cycle_root / f"repeat_{attempt:02d}"
        selection = v49.build_repeat(source, target)
        if v49.exact_parameter_set(selection) != v49.exact_parameter_set(
            discovery_root / "top5.json"
        ):
            raise v49.FailClosed("repeat candidates differ from fresh discovery points")
        clean5, summary = run_diagnostic_v50(selection, target / "replay", target)
        if not clean5:
            if int(summary.get("technical_failure_count", 0)) <= 0:
                raise v49.FailClosed("non-clean repeat stopped without technical failure")
            raise RepeatTechnicalFailure(attempt, target)
        passed, gate = evaluate_gate_v50(target)
        if passed:
            return source, target, gate, attempt
        source = target
    raise v49.FailClosed("five clean5 repeat gate attempts exhausted without admission")


def write_controller_state(**values: Any) -> None:
    v49.atomic_json(
        v49.ROOT / "controller_state.json",
        {
            "schema_version": f"job_fresh10_target10_autonomous_{CONTROLLER_VERSION}",
            "target_accepted": v49.TARGET_ACCEPTED,
            "qps_used_for_inner_scoring": False,
            "manual_candidate_injection": False,
            "technical_batch_requires_fresh_fingerprints": True,
            "historical_accepted_excluded": 3,
            "countable_formal_accepted_origin": 0,
            **values,
        },
    )


def require_v50_hashes() -> None:
    if hashlib.sha256(Path(v49.__file__).read_bytes()).hexdigest() != EXPECTED_V49_SHA256:
        raise v49.FailClosed("v50 base v49 SHA mismatch")
    v49.require_hashes()
    if (
        not BASE_RUNNER_V7.is_file()
        or v49.sha256_file(BASE_RUNNER_V7) != EXPECTED_BASE_RUNNER_V7_SHA256
    ):
        raise v49.FailClosed("v50 explicit-cost base runner SHA mismatch")
    if (
        not REMOTE_BRIDGE.is_file()
        or v49.sha256_file(REMOTE_BRIDGE) != EXPECTED_REMOTE_BRIDGE_SHA256
    ):
        raise v49.FailClosed("v54 single-network bridge SHA mismatch")
    if (
        not ROBUST_RUNNER_V9.is_file()
        or v49.sha256_file(ROBUST_RUNNER_V9)
        != EXPECTED_ROBUST_RUNNER_V9_SHA256
    ):
        raise v49.FailClosed("v50 robust-v9 state guard SHA mismatch")


def validate_pending_diagnostic_counters_v50(
    latest_formal: Path,
    first_cycle: int,
) -> None:
    """Chain interleaved discovery/repeat batches by their sealed ordinals."""
    _state_sha, state = v49.state_pin()
    running_global = int(state.get("global_ordinal", -1))
    formal_replay = int(state.get("formal_replay_count", -1))
    accepted = int(state.get("accepted_count", -1))
    latest_cycle = (
        0
        if latest_formal == HISTORICAL_INITIAL_FORMAL
        else int(latest_formal.parent.name.rsplit("_", 1)[-1])
    )
    roots = [
        v49.ROOT / f"cycle_{ordinal:03d}"
        for ordinal in range(latest_cycle + 1, first_cycle + 1)
        if (v49.ROOT / f"cycle_{ordinal:03d}").is_dir()
    ]
    pending = []
    for root in roots:
        for before_path in root.rglob("counters_before.json"):
            phase = before_path.parent
            before = v49.read_json(before_path)
            summary_path = phase / "replay/summary.json"
            audit_path = phase / "diagnostic_audit.json"
            if not summary_path.is_file():
                raise v49.FailClosed(
                    f"partial diagnostic has no sealed summary; refusing replay: {phase}"
                )
            summary = v49.read_json(summary_path)
            physical = int(summary.get("physical_invocation_count", -1))
            if not (0 <= physical <= 5):
                raise v49.FailClosed(f"diagnostic physical count is invalid: {phase}")
            if audit_path.is_file():
                sealed = v49.read_json(audit_path)
                if sealed.get("summary_sha256") != v49.sha256_file(summary_path):
                    raise v49.FailClosed(f"diagnostic summary SHA mismatch: {phase}")
            sequence_path = phase / "v50_batch_sequence.json"
            sequence = (
                int(v49.read_json(sequence_path).get("sequence", -1))
                if sequence_path.is_file()
                else None
            )
            pending.append((phase, before, physical, sequence))
    unused = list(pending)
    sequenced = [item for item in pending if item[3] is not None]
    if sequenced:
        if len(sequenced) != len(pending):
            # Legacy v49 batches are allowed only as a strict physical-ordinal
            # prefix before all v50 sequences.
            legacy = [item for item in pending if item[3] is None]
            legacy = sorted(legacy, key=lambda item: int(item[1].get("global", -1)))
            ordered_v50 = sorted(sequenced, key=lambda item: int(item[3]))
            unused = [*legacy, *ordered_v50]
        else:
            unused = sorted(sequenced, key=lambda item: int(item[3]))
        sequences = [int(item[3]) for item in sequenced]
        if len(set(sequences)) != len(sequences):
            raise v49.FailClosed("duplicate v50 diagnostic batch sequence")
        for phase, before, physical, _sequence in unused:
            if (
                int(before.get("global", -1)) != running_global
                or int(before.get("formal_replay", -1)) != formal_replay
                or int(before.get("accepted", -1)) != accepted
            ):
                raise v49.FailClosed(
                    f"sequenced diagnostic counter chain mismatch: {phase}"
                )
            running_global += int(physical)
        unused = []
    while unused:
        matches = [
            item
            for item in unused
            if int(item[1].get("global", -1)) == running_global
            and int(item[1].get("formal_replay", -1)) == formal_replay
            and int(item[1].get("accepted", -1)) == accepted
        ]
        if len(matches) != 1:
            raise v49.FailClosed(
                "interleaved diagnostic counter chain is ambiguous or broken"
            )
        selected = matches[0]
        unused.remove(selected)
        running_global += int(selected[2])
    if (
        running_global != v49.counter(v49.GLOBAL_COUNTER)
        or formal_replay != v49.counter(v49.FORMAL_REPLAY_COUNTER)
        or accepted != v49.counter(v49.ACCEPTED_COUNTER)
    ):
        raise v49.FailClosed("pending diagnostic/global counter accounting mismatch")


def resume_position_v50() -> tuple[Path, int]:
    v49.validate_committed_formal(
        HISTORICAL_INITIAL_FORMAL,
        require_current_state=False,
    )
    if not FRESH_STATE.is_file() or not FRESH_ACCEPTED_COUNTER.is_file():
        raise v49.FailClosed("fresh0 state/counter bootstrap is missing")
    fresh_state_sha, fresh_state = v49.state_pin()
    if (
        int(fresh_state.get("historical_accepted_excluded", -1)) != 3
        or int(fresh_state.get("accepted_count", -1)) < 0
        or int(fresh_state.get("accepted_count", -1)) > v49.TARGET_ACCEPTED
        or int(fresh_state.get("accepted_count", -1))
        != v49.counter(FRESH_ACCEPTED_COUNTER)
        or str(fresh_state.get("campaign_contract"))
        != CAMPAIGN_CONTRACT
    ):
        raise v49.FailClosed("fresh0 state contract is invalid")
    _ = fresh_state_sha
    latest = HISTORICAL_INITIAL_FORMAL
    cycle = 1
    while True:
        cycle_root = v49.ROOT / f"cycle_{cycle:03d}"
        if not cycle_root.exists():
            break
        audit_path = cycle_root / "cycle_audit.json"
        transaction_path = (
            cycle_root / "formal/replay/formal_state_commit_transaction.json"
        )
        if audit_path.exists():
            cycle_audit = v49.read_json(audit_path)
            if cycle_audit.get("status") == (
                "abandoned_zero_formal_calls_fingerprint_collision"
            ):
                if (
                    int(cycle_audit.get("cycle", -1)) != cycle
                    or int(cycle_audit.get("formal_physical_calls", -1)) != 0
                    or transaction_path.exists()
                    or (cycle_root / "formal/replay/summary.json").exists()
                ):
                    raise v49.FailClosed(f"abandoned cycle audit mismatch: {cycle_root}")
                cycle += 1
                continue
            v49.validate_committed_formal(
                cycle_root / "formal",
                require_current_state=False,
            )
            transaction = v49.read_json(transaction_path)
            if (
                cycle_audit.get("status") != "complete"
                or int(cycle_audit.get("cycle", -1)) != cycle
                or int(cycle_audit.get("accepted_after", -1))
                != int(transaction.get("accepted_after", -2))
            ):
                raise v49.FailClosed(f"completed cycle audit mismatch: {cycle_root}")
            latest = cycle_root / "formal"
            cycle += 1
            continue
        if transaction_path.exists():
            transaction = v49.read_json(transaction_path)
            if transaction.get("status") != "committed":
                raise v49.FailClosed(f"uncommitted formal transaction: {transaction_path}")
            v49.atomic_json(
                cycle_root / "cycle_audit.json",
                completed_cycle_audit_v50(cycle_root),
            )
            latest = cycle_root / "formal"
            cycle += 1
            continue
        break
    if latest == HISTORICAL_INITIAL_FORMAL:
        _state_sha, state = v49.state_pin()
        if (
            int(state.get("global_ordinal", -1)) != v49.counter(v49.GLOBAL_COUNTER)
            or int(state.get("formal_replay_count", -1))
            != v49.counter(v49.FORMAL_REPLAY_COUNTER)
            or int(state.get("accepted_count", -1))
            != v49.counter(v49.ACCEPTED_COUNTER)
        ):
            raise v49.FailClosed("fresh0 bootstrap state/counter mismatch")
    else:
        v49.validate_committed_formal(latest, require_current_state=True)
    later = sorted(
        path
        for path in v49.ROOT.glob("cycle_[0-9][0-9][0-9]")
        if path.is_dir() and path.name > f"cycle_{cycle:03d}"
    )
    if later:
        raise v49.FailClosed("cycle directory gap detected")
    validate_pending_diagnostic_counters_v50(latest, cycle)
    return latest, cycle


def completed_cycle_audit_v50(cycle_root: Path) -> dict[str, Any]:
    """Recover the crash window after a committed v50/v51 formal batch."""
    formal = cycle_root / "formal"
    v49.validate_committed_formal(formal, require_current_state=True)
    selection_path, summary_path, transaction_path = v49.formal_artifacts(formal)
    transaction = v49.read_json(transaction_path)
    gate = v49.read_json(formal / "package/entry_gate.json")
    return {
        "status": "complete",
        "cycle": int(cycle_root.name.rsplit("_", 1)[-1]),
        "accepted_before": int(transaction["accepted_before"]),
        "accepted_after": int(transaction["accepted_after"]),
        "accepted_delta": (
            int(transaction["accepted_after"])
            - int(transaction["accepted_before"])
        ),
        "state_sha256_before": str(gate["formal_state_sha256_before"]),
        "state_sha256_after": str(transaction["state_sha256_after"]),
        "novel_discovery_selection_sha256": str(
            gate["novel_discovery_selection_sha256"]
        ),
        "formal_selection_sha256": v49.sha256_file(selection_path),
        "formal_summary_sha256": v49.sha256_file(summary_path),
        "manual_candidate_injection": False,
        "qps_used_for_inner_scoring": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-cycles", type=int, default=v49.MAX_CYCLES)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    if args.max_cycles < 1 or args.max_cycles > v49.MAX_CYCLES:
        raise v49.FailClosed("max-cycles must be in 1..64")
    require_v50_hashes()
    v49.validate_diagnostic = validate_diagnostic_v50
    v49.ROOT.mkdir(parents=True, exist_ok=True)
    lock_handle = (v49.ROOT / "controller.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise v49.FailClosed("another JOB target10 controller holds the lock") from error
    latest_formal, first_cycle = resume_position_v50()
    if args.preflight_only:
        state_sha, state = v49.state_pin()
        print(
            json.dumps(
                {
                    "status": "passed",
                    "controller": f"job_{CONTROLLER_VERSION}",
                    "accepted_count": int(state["accepted_count"]),
                    "first_cycle": first_cycle,
                    "formal_state_sha256": state_sha,
                    "new_workload_calls": 0,
                    "explain_calls": 0,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    for cycle in range(first_cycle, first_cycle + args.max_cycles):
        state_sha_before, state = v49.state_pin()
        accepted_before = int(state["accepted_count"])
        if accepted_before == v49.TARGET_ACCEPTED:
            write_controller_state(status="complete", accepted_count=accepted_before)
            return 0
        cycle_root = v49.ROOT / f"cycle_{cycle:03d}"
        cycle_root.mkdir(parents=True, exist_ok=True)
        write_controller_state(
            status="running",
            cycle=cycle,
            phase="successor_discovery",
            accepted_count=accepted_before,
            formal_state_sha256=state_sha_before,
        )
        formal_selection = v49.formal_artifacts(latest_formal)[0]
        evidence_selection, _summary, evidence_events = v49.clean_seed_from_formal(formal_selection)
        generation_state = (
            v49.read_json(HISTORICAL_STATE) if accepted_before == 0 else state
        )
        incumbent_events, _event, point_result = v49.incumbent_evidence(generation_state)
        v49.exclusion_inventory(cycle_root / "global_exclusion.json")
        initial_discovery = generate_fresh_discovery(
            cycle_root,
            1,
            evidence_selection=evidence_selection,
            evidence_events=evidence_events,
            incumbent_events=incumbent_events,
            point_result=point_result,
            state_sha_before=state_sha_before,
        )
        write_controller_state(
            status="running",
            cycle=cycle,
            phase="diagnostic_discovery",
            accepted_count=accepted_before,
            formal_state_sha256=state_sha_before,
        )
        discovery, discovery_root, discovery_attempt = obtain_clean_discovery_v50(
            initial_discovery,
            cycle_root,
            evidence_selection=evidence_selection,
            evidence_events=evidence_events,
            incumbent_events=incumbent_events,
            point_result=point_result,
            state_sha_before=state_sha_before,
        )
        repeat_attempt = 1
        while True:
            write_controller_state(
                status="running",
                cycle=cycle,
                phase="repeat_gate",
                accepted_count=accepted_before,
                formal_state_sha256=state_sha_before,
            )
            try:
                first_source, passed_source, raw_gate, _repeat_used = (
                    obtain_passed_repeat_v50(
                        discovery_root,
                        cycle_root,
                        start_attempt=repeat_attempt,
                    )
                )
                break
            except RepeatTechnicalFailure as error:
                repeat_attempt = error.attempt + 1
                discovery_attempt += 1
                if (
                    repeat_attempt > MAX_DIAGNOSTIC_BATCHES
                    or discovery_attempt > MAX_DIAGNOSTIC_BATCHES
                ):
                    raise v49.FailClosed(
                        "technical repeat exhausted fresh diagnostic budget"
                    ) from error
                write_controller_state(
                    status="running",
                    cycle=cycle,
                    phase="fresh_discovery_after_repeat_technical",
                    accepted_count=accepted_before,
                    formal_state_sha256=state_sha_before,
                    sealed_repeat_root=str(error.root),
                )
                fresh = generate_fresh_discovery(
                    cycle_root,
                    discovery_attempt,
                    evidence_selection=evidence_selection,
                    evidence_events=evidence_events,
                    incumbent_events=incumbent_events,
                    point_result=point_result,
                    state_sha_before=state_sha_before,
                )
                discovery, discovery_root, discovery_attempt = (
                    obtain_clean_discovery_v50(
                        fresh,
                        cycle_root,
                        start_attempt=discovery_attempt,
                        evidence_selection=evidence_selection,
                        evidence_events=evidence_events,
                        incumbent_events=incumbent_events,
                        point_result=point_result,
                        state_sha_before=state_sha_before,
                    )
                )
        if v49.sha256_file(v49.STATE) != state_sha_before:
            raise v49.FailClosed("diagnostic phases mutated formal state")
        write_controller_state(
            status="running",
            cycle=cycle,
            phase="formal",
            accepted_count=accepted_before,
            formal_state_sha256=state_sha_before,
        )
        try:
            formal = prepare_formal_v50(
                cycle_root,
                discovery,
                first_source,
                passed_source,
                raw_gate,
                state_sha_before,
            )
        except v49.NoveltyCollision as error:
            if v49.sha256_file(v49.STATE) != state_sha_before:
                raise v49.FailClosed("novelty collision coincided with state mutation") from error
            v49.atomic_json(
                cycle_root / "cycle_audit.json",
                {
                    "status": "abandoned_zero_formal_calls_fingerprint_collision",
                    "cycle": cycle,
                    "accepted_before": accepted_before,
                    "accepted_after": accepted_before,
                    "accepted_delta": 0,
                    "formal_physical_calls": 0,
                    "state_sha256_before": state_sha_before,
                    "state_sha256_after": state_sha_before,
                    "novel_discovery_selection_sha256": v49.sha256_file(discovery),
                    "error": str(error),
                    "manual_candidate_injection": False,
                    "qps_used_for_inner_scoring": False,
                },
            )
            continue
        v49.execute_formal(formal, state_sha_before)
        state_sha_after, state_after = v49.state_pin()
        accepted_after = int(state_after["accepted_count"])
        if accepted_after < accepted_before or accepted_after > v49.TARGET_ACCEPTED:
            raise v49.FailClosed("invalid accepted transition after formal cycle")
        v49.atomic_json(
            cycle_root / "cycle_audit.json",
            {
                "status": "complete",
                "cycle": cycle,
                "accepted_before": accepted_before,
                "accepted_after": accepted_after,
                "accepted_delta": accepted_after - accepted_before,
                "state_sha256_before": state_sha_before,
                "state_sha256_after": state_sha_after,
                "novel_discovery_selection_sha256": v49.sha256_file(discovery),
                "formal_selection_sha256": v49.sha256_file(v49.formal_artifacts(formal)[0]),
                "formal_summary_sha256": v49.sha256_file(v49.formal_artifacts(formal)[1]),
                "manual_candidate_injection": False,
                "qps_used_for_inner_scoring": False,
            },
        )
        latest_formal = formal
    state_sha, state = v49.state_pin()
    write_controller_state(
        status="fail_closed_cycle_cap",
        accepted_count=int(state["accepted_count"]),
        formal_state_sha256=state_sha,
        maximum_cycles=args.max_cycles,
    )
    raise v49.FailClosed("autonomous cycle cap reached before accepted=10")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except v49.FailClosed as error:
        try:
            write_controller_state(status="fail_closed", error=str(error))
        except Exception:
            pass
        print(
            f"JOB target10 {CONTROLLER_VERSION} fail closed: {error}",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2)
