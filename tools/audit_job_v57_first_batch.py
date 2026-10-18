#!/usr/bin/env python3
"""Offline, zero-call audit for JOB v57 diagnostic and formal batches."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
from typing import Any, Mapping


REQUIRED_CANDIDATE_FIELDS = (
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
)
RESOURCES = ("cpu", "io", "memory")
CAPABILITY = {"cpu": 3.0, "io": 256.0, "memory": 2.91015625}
NEUTRAL_FLAGS = {
    "accepted": False,
    "gradient": False,
    "incumbent": False,
    "range": False,
}
RESOURCE_MEDIAN_CONTRACT = (
    "job_three_independent_exact_point_resource_median_rescored_v3"
)


class AuditFailure(RuntimeError):
    """A sealed JOB v57 artifact violates the experiment contract."""


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AuditFailure(f"cannot load JSON: {path}") from error
    if not isinstance(value, dict):
        raise AuditFailure(f"expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise AuditFailure(f"{label} is boolean")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise AuditFailure(f"{label} is not numeric") from error
    if not math.isfinite(result):
        raise AuditFailure(f"{label} is not finite")
    return result


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def candidate_list(selection: Mapping[str, Any], path: Path) -> list[dict[str, Any]]:
    candidates = selection.get("top5") or selection.get("candidates")
    if (
        not isinstance(candidates, list)
        or len(candidates) != 5
        or not all(isinstance(item, dict) for item in candidates)
    ):
        raise AuditFailure(f"selection is not an object Top-5: {path}")
    return candidates


def fingerprint_id(candidate: Mapping[str, Any]) -> str:
    return str((candidate.get("three_level_fingerprint") or {}).get("id") or "")


def audit_selection(
    path: Path,
    *,
    formal: bool,
    quarantined_fingerprint: str,
) -> dict[str, Any]:
    selection = load(path)
    candidates = candidate_list(selection, path)
    roles = [str(item.get("selection_role") or "") for item in candidates]
    allowed_roles = (
        (["trusted"] * 3 + ["probe"] * 2,)
        if formal
        else ((["probe"] * 5), (["trusted"] * 3 + ["probe"] * 2))
    )
    if roles not in allowed_roles:
        raise AuditFailure(f"invalid Top-5 roles {roles}: {path}")
    identifiers = []
    for rank, candidate in enumerate(candidates, 1):
        missing = [field for field in REQUIRED_CANDIDATE_FIELDS if field not in candidate]
        fingerprint = candidate.get("three_level_fingerprint") or {}
        identifier = str(fingerprint.get("id") or "")
        if (
            missing
            or candidate.get("boundary_corner") is not False
            or fingerprint.get("valid") is not True
            or not identifier.startswith("three-level-sha256:")
            or not fingerprint.get("plan_level")
            or not isinstance(fingerprint.get("feature_ratio_level"), Mapping)
            or not isinstance(fingerprint.get("predicted_resource_level"), Mapping)
        ):
            raise AuditFailure(
                f"rank {rank} candidate/fingerprint contract violation: {path}; missing={missing}"
            )
        if identifier == quarantined_fingerprint:
            raise AuditFailure(f"v55 failed fingerprint was retried: {path} rank={rank}")
        for resource in RESOURCES:
            finite(candidate["feature_ratio"].get(resource), f"rank {rank} feature_ratio.{resource}")
            finite(candidate["predicted_resource"].get(resource), f"rank {rank} predicted_resource.{resource}")
            finite(candidate["utilization"].get(resource), f"rank {rank} utilization.{resource}")
        finite(candidate["candidate_score"], f"rank {rank} candidate_score")
        finite(candidate["eta"], f"rank {rank} eta")
        if not isinstance(candidate["confidence"], Mapping):
            raise AuditFailure(f"rank {rank} confidence is not structured: {path}")
        if (
            not isinstance(candidate["dScore/dU"], Mapping)
            or not isinstance(candidate["T"], Mapping)
            or not isinstance(candidate["g_j"], Mapping)
            or not isinstance(candidate["delta_param"], Mapping)
        ):
            raise AuditFailure(f"rank {rank} gradient fields are not structured: {path}")
        identifiers.append(identifier)
    if len(set(identifiers)) != 5:
        raise AuditFailure(f"Top-5 fingerprints are not distinct: {path}")
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "roles": roles,
        "fingerprints": identifiers,
        "required_candidate_fields": "passed",
        "three_level_fingerprints": "five_distinct_noncorner",
    }


def _audit_row_neutral(row: Mapping[str, Any], label: str) -> None:
    if (
        row.get("accepted") is not False
        or row.get("gradient_update_applied") is not False
        or row.get("incumbent_update_applied") is not False
        or row.get("range_update_applied") is not False
    ):
        raise AuditFailure(f"{label} mutated formal state")
    before = row.get("incumbent_state_before")
    after = row.get("incumbent_state_after")
    if isinstance(before, Mapping) and isinstance(after, Mapping) and before != after:
        raise AuditFailure(f"{label} changed before/after state")


def audit_diagnostic(summary_path: Path, selection_path: Path) -> dict[str, Any]:
    summary = load(summary_path)
    successful = summary.get("successful") or []
    failures = summary.get("technical_failures") or []
    invocations = summary.get("invocations") or []
    if not all(isinstance(value, list) for value in (successful, failures, invocations)):
        raise AuditFailure(f"diagnostic ledgers are malformed: {summary_path}")
    physical = int(summary.get("physical_invocation_count", -1))
    valid = int(summary.get("successful_real_replay_count", -1))
    technical = int(summary.get("technical_failure_count", -1))
    if (
        summary.get("diagnostic_only") is not True
        or not (0 <= valid <= physical <= 5)
        or not (0 <= technical <= 5)
        or valid + technical > 5
        or len(successful) != valid
        or len(failures) != technical
        or len(invocations) != physical
        or summary.get("selection_sha256") != sha256_file(selection_path)
        or summary.get("baseline_params_restored") is not True
        or (summary.get("baseline_restore") or {}).get("success") is not True
        or summary.get("initial_state") != summary.get("final_state")
        or summary.get("formal_update_flags") != NEUTRAL_FLAGS
    ):
        raise AuditFailure(f"diagnostic state-neutral/physical contract failed: {summary_path}")
    for index, row in enumerate(successful + failures, 1):
        if not isinstance(row, Mapping):
            raise AuditFailure(f"diagnostic row {index} is malformed: {summary_path}")
        _audit_row_neutral(row, f"diagnostic row {index}")
    return {
        "summary_path": str(summary_path.resolve()),
        "summary_sha256": sha256_file(summary_path),
        "physical_calls": physical,
        "valid_replays": valid,
        "technical_failures": technical,
        "state_neutral": True,
        "baseline_restored": True,
    }


def ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    result = [0.0] * len(values)
    for rank, index in enumerate(order, 1):
        result[index] = float(rank)
    return result


def spearman(left: list[float], right: list[float]) -> float:
    left_ranks = ranks(left)
    right_ranks = ranks(right)
    left_mean = sum(left_ranks) / len(left_ranks)
    right_mean = sum(right_ranks) / len(right_ranks)
    numerator = sum(
        (x - left_mean) * (y - right_mean)
        for x, y in zip(left_ranks, right_ranks)
    )
    denominator = math.sqrt(
        sum((x - left_mean) ** 2 for x in left_ranks)
        * sum((y - right_mean) ** 2 for y in right_ranks)
    )
    if denominator == 0.0:
        raise AuditFailure("trusted ranks are not distinguishable")
    return numerator / denominator


def audit_clean5_rank_gate(
    selection_path: Path,
    summary_path: Path,
) -> dict[str, Any] | None:
    selection = load(selection_path)
    candidates = candidate_list(selection, selection_path)
    roles = [str(item.get("selection_role") or "") for item in candidates]
    if roles != ["trusted", "trusted", "trusted", "probe", "probe"]:
        return None
    summary = load(summary_path)
    successful = summary.get("successful") or []
    observations = {
        int(item.get("rank", -1)): item
        for item in successful
        if isinstance(item, Mapping)
    }
    if (
        int(summary.get("physical_invocation_count", -1)) != 5
        or int(summary.get("successful_real_replay_count", -1)) != 5
        or int(summary.get("technical_failure_count", -1)) != 0
        or set(observations) != {1, 2, 3, 4, 5}
    ):
        return None
    predicted = [finite(item["candidate_score"], "candidate_score") for item in candidates[:3]]
    actual = [finite(observations[rank]["actual_score"], "actual_score") for rank in (1, 2, 3)]
    rho = spearman(predicted, actual)
    resource_factors = {
        rank: {
            resource: max(
                finite(candidates[rank - 1]["predicted_resource"][resource], "predicted resource")
                / finite(observations[rank]["actual_resource"][resource], "actual resource"),
                finite(observations[rank]["actual_resource"][resource], "actual resource")
                / finite(candidates[rank - 1]["predicted_resource"][resource], "predicted resource"),
            )
            for resource in RESOURCES
        }
        for rank in (1, 2, 3)
    }
    maximum_factor = max(
        value for row in resource_factors.values() for value in row.values()
    )
    actual_best = max(
        observations,
        key=lambda rank: finite(observations[rank]["actual_score"], "actual score"),
    )
    checks = {
        "trusted_only_spearman_positive": rho > 0.0,
        "actual_best_in_predicted_top3": actual_best <= 3,
        "trusted_resource_no_order_of_magnitude_error": maximum_factor < 10.0,
    }
    return {
        "selection_path": str(selection_path.resolve()),
        "summary_path": str(summary_path.resolve()),
        "trusted_only_spearman": rho,
        "actual_best_predicted_rank": actual_best,
        "maximum_trusted_resource_factor": maximum_factor,
        "trusted_resource_factors": resource_factors,
        "checks": checks,
        "passed": all(checks.values()),
        "probe_rows_excluded_from_spearman": True,
        "qps_used_for_inner_scoring": False,
    }


def exact_parameter_set(path: Path) -> set[str]:
    return {
        json.dumps(item["candidate_params"], sort_keys=True, separators=(",", ":"))
        for item in candidate_list(load(path), path)
    }


def audit_cycle_repeat_binding(cycle: Path) -> list[dict[str, Any]]:
    discoveries = sorted(cycle.glob("discovery_replay_*/top5.json"))
    repeats = sorted(cycle.glob("repeat_*/top5.json"))
    if not repeats:
        return []
    clean_discoveries = [
        path
        for path in discoveries
        if (path.parent / "replay/summary.json").is_file()
        and int(load(path.parent / "replay/summary.json").get("technical_failure_count", -1)) == 0
        and int(load(path.parent / "replay/summary.json").get("successful_real_replay_count", -1)) == 5
    ]
    if not clean_discoveries:
        raise AuditFailure(f"repeat exists without one clean discovery: {cycle}")
    source = clean_discoveries[-1]
    source_params = exact_parameter_set(source)
    audits = []
    for repeat in repeats:
        if exact_parameter_set(repeat) != source_params:
            raise AuditFailure(f"exact repeat parameter set differs from discovery: {repeat}")
        gate = repeat.parent / "entry_gate_raw.json"
        second_gate = repeat.parent / "entry_gate_raw.second.json"
        binding = repeat.parent / "entry_gate_binding_audit.json"
        if gate.exists() != second_gate.exists():
            raise AuditFailure(f"repeat gate deterministic pair is partial: {repeat.parent}")
        if gate.exists() and gate.read_bytes() != second_gate.read_bytes():
            raise AuditFailure(f"repeat gate recomputation is nondeterministic: {repeat.parent}")
        if binding.exists():
            value = load(binding)
            if (
                value.get("selection_sha256") != sha256_file(repeat)
                or value.get("summary_sha256")
                != sha256_file(repeat.parent / "replay/summary.json")
                or value.get("new_workload_calls") != 0
            ):
                raise AuditFailure(f"repeat gate binding mismatch: {binding}")
        audits.append(
            {
                "repeat_selection": str(repeat.resolve()),
                "repeat_selection_sha256": sha256_file(repeat),
                "discovery_selection": str(source.resolve()),
                "discovery_selection_sha256": sha256_file(source),
                "exact_parameter_set_preserved": True,
                "gate_double_recompute_identical": gate.exists(),
                "gate_binding_present": binding.exists(),
            }
        )
    return audits


def partial_phase_audits(root: Path) -> list[dict[str, Any]]:
    partial = []
    for phase in sorted(
        [*root.glob("cycle_*/discovery_replay_*"), *root.glob("cycle_*/repeat_*")],
        key=str,
    ):
        if (phase / "diagnostic_audit.json").exists() or (phase / "replay/summary.json").exists():
            continue
        selection = phase / "top5.json"
        if not selection.is_file():
            continue
        replay = phase / "replay"
        workload_logs = sorted(replay.glob("rank_*/workload.log")) if replay.is_dir() else []
        result_files = sorted(replay.glob("rank_*/point_result.json")) if replay.is_dir() else []
        partial.append(
            {
                "phase": str(phase.resolve()),
                "selection_sha256": sha256_file(selection),
                "status": "running_or_unsealed",
                "physical_start_evidence_count": len(workload_logs),
                "complete_point_evidence_count": len(result_files),
                "known_technical_failures": None,
                "technical_classification_deferred_until_sealed_summary": True,
                "formal_state_conclusion_deferred": True,
            }
        )
    return partial


def directional_band_score(utilization: float, weight: float, target: bool) -> float:
    target_min, target_max, hard = ((0.85, 0.90, 0.95) if target else (0.30, 0.50, 0.90))
    if target:
        if utilization < target_min:
            gap = (target_min - utilization) / target_min
            return weight * (1.0 - gap * gap)
        if utilization <= target_max:
            position = (utilization - target_min) / (target_max - target_min)
            return weight * (1.0 - 0.2 * position)
        span = max(hard - target_max, 0.05)
        gap = (utilization - target_max) / span
        hard_excess = max(0.0, utilization - hard) / span
        return weight * (0.8 - gap * gap - 4.0 * hard_excess)
    if utilization < target_min:
        gap = (target_min - utilization) / target_min
        return weight * 0.8 * (1.0 - gap * gap)
    if utilization <= target_max:
        position = (utilization - target_min) / (target_max - target_min)
        return weight * (0.8 + 0.2 * position)
    span = max(hard - target_max, 0.05)
    gap = (utilization - target_max) / span
    hard_excess = max(0.0, utilization - hard) / span
    return weight * (1.0 - gap * gap - 4.0 * hard_excess)


def actual_score(utilization: Mapping[str, Any]) -> float:
    return sum(
        directional_band_score(
            finite(utilization[resource], f"utilization.{resource}"),
            weight,
            resource == "memory",
        )
        for resource, weight in (("cpu", 1.0), ("io", 3.0), ("memory", 1.0))
    )


def audit_robust_entry(entry: Mapping[str, Any], label: str) -> None:
    sources = entry.get("source_actual_resources")
    robust_resource = entry.get("robust_actual_resource")
    robust_utilization = entry.get("robust_actual_utilization")
    if (
        entry.get("aggregation") != "componentwise_median"
        or not isinstance(sources, list)
        or len(sources) != 3
        or not all(isinstance(item, Mapping) for item in sources)
        or not isinstance(robust_resource, Mapping)
        or not isinstance(robust_utilization, Mapping)
        or entry.get("qps_used_for_inner_scoring") is not False
    ):
        raise AuditFailure(f"{label} robust resource evidence is malformed")
    recomputed_resource = {
        name: float(statistics.median([finite(item[name], f"{label} source.{name}") for item in sources]))
        for name in RESOURCES
    }
    recomputed_utilization = {
        name: recomputed_resource[name] / CAPABILITY[name] for name in RESOURCES
    }
    for name in RESOURCES:
        if not math.isclose(
            finite(robust_resource.get(name), f"{label} robust_resource.{name}"),
            recomputed_resource[name],
            rel_tol=0.0,
            abs_tol=1e-12,
        ) or not math.isclose(
            finite(robust_utilization.get(name), f"{label} robust_utilization.{name}"),
            recomputed_utilization[name],
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise AuditFailure(f"{label} componentwise resource median mismatch")
    recomputed_score = actual_score(recomputed_utilization)
    if not math.isclose(
        finite(entry.get("robust_acceptance_score"), f"{label} robust score"),
        recomputed_score,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise AuditFailure(f"{label} robust resource rescore mismatch")


def audit_formal(summary_path: Path, selection_path: Path) -> dict[str, Any]:
    summary = load(summary_path)
    selection = load(selection_path)
    candidates = candidate_list(selection, selection_path)
    successful = summary.get("successful") or []
    failures = summary.get("technical_failures") or []
    physical = int(summary.get("physical_invocation_count", -1))
    valid = int(summary.get("successful_real_replay_count", -1))
    technical = int(summary.get("technical_failure_count", -1))
    if (
        not isinstance(successful, list)
        or not isinstance(failures, list)
        or not (0 <= valid <= physical <= 5)
        or valid + technical > 5
        or len(successful) != valid
        or len(failures) != technical
        or summary.get("selection_sha256") != sha256_file(selection_path)
        or summary.get("baseline_params_restored") is not True
        or (summary.get("baseline_restore") or {}).get("success") is not True
        or summary.get("resource_median_acceptance_contract") != RESOURCE_MEDIAN_CONTRACT
    ):
        raise AuditFailure(f"formal physical/robust contract failed: {summary_path}")
    audit_rows = summary.get("robust_resource_acceptance_audit") or []
    if not isinstance(audit_rows, list):
        raise AuditFailure(f"formal robust audit ledger is malformed: {summary_path}")
    audit_by_rank = {int(item.get("rank", -1)): item for item in audit_rows if isinstance(item, Mapping)}
    trusted_successes = 0
    for row in successful:
        if not isinstance(row, Mapping):
            raise AuditFailure(f"formal successful row is malformed: {summary_path}")
        rank = int(row.get("rank", -1))
        if rank < 1 or rank > 5:
            raise AuditFailure(f"formal rank is invalid: {summary_path}")
        role = str(candidates[rank - 1].get("selection_role") or "")
        if row.get("selection_role") != role:
            raise AuditFailure(f"formal row role differs from selection: rank={rank}")
        if role == "probe" or row.get("accepted") is not True:
            _audit_row_neutral(row, f"formal rank {rank}")
        if role == "probe" and row.get("accepted") is True:
            raise AuditFailure(f"formal probe accepted: rank={rank}")
        if role == "trusted":
            trusted_successes += 1
            entry = audit_by_rank.get(rank)
            if entry is None:
                raise AuditFailure(f"trusted formal row lacks robust audit: rank={rank}")
            audit_robust_entry(entry, f"formal rank {rank}")
            if row.get("accepted") is True:
                after = row.get("incumbent_state_after") or {}
                if not math.isclose(
                    finite(after.get("incumbent_score"), f"rank {rank} incumbent score"),
                    finite(entry.get("robust_acceptance_score"), f"rank {rank} robust score"),
                    rel_tol=0.0,
                    abs_tol=1e-12,
                ):
                    raise AuditFailure(f"accepted state score is not robust score: rank={rank}")
        provenance = ((row.get("measurement_protocol") or {}).get("plan_fingerprint_provenance") or {})
        if provenance.get("binding_implementation") != "job_v57_explicit_args_selection_v1":
            raise AuditFailure(f"formal row did not use explicit v57 selection binding: rank={rank}")
    if len(audit_by_rank) != trusted_successes:
        raise AuditFailure(f"formal robust audit cardinality mismatch: {summary_path}")
    for row in failures:
        if not isinstance(row, Mapping):
            raise AuditFailure(f"formal failure row is malformed: {summary_path}")
        _audit_row_neutral(row, f"formal technical rank {row.get('rank')}")
    return {
        "summary_path": str(summary_path.resolve()),
        "summary_sha256": sha256_file(summary_path),
        "physical_calls": physical,
        "valid_replays": valid,
        "technical_failures": technical,
        "trusted_robust_rows": trusted_successes,
        "resource_median_rescore": "passed",
        "probe_and_rejected_state_neutral": True,
    }


def diagnostic_roots(root: Path) -> list[Path]:
    roots = {
            audit.parent
            for audit in root.glob("cycle_*/**/diagnostic_audit.json")
            if "/formal/" not in str(audit)
        }
    roots.update(
        summary.parents[1]
        for summary in root.glob("cycle_*/**/replay/summary.json")
        if "/formal/" not in str(summary)
    )
    return sorted(roots, key=str)


def validate_gate_artifact(
    repeat_root: Path,
    recomputed: Mapping[str, Any],
) -> dict[str, Any]:
    gate_path = repeat_root / "entry_gate_raw.json"
    if not gate_path.is_file():
        return {"present": False, "passed": None}
    gate = load(gate_path)
    checks = gate.get("checks") or {}
    expected_checks = recomputed["checks"]
    for name, expected in expected_checks.items():
        if checks.get(name) is not expected:
            raise AuditFailure(f"gate check differs from recomputation: {name} {gate_path}")
    for name in (
        "trusted_only_spearman",
        "maximum_trusted_resource_factor",
    ):
        if not math.isclose(
            finite(gate.get(name), f"gate.{name}"),
            finite(recomputed.get(name), f"recomputed.{name}"),
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise AuditFailure(f"gate metric differs from recomputation: {name} {gate_path}")
    if int(gate.get("actual_best_predicted_rank", -1)) != int(
        recomputed["actual_best_predicted_rank"]
    ):
        raise AuditFailure(f"gate actual-best rank differs from recomputation: {gate_path}")
    return {
        "present": True,
        "path": str(gate_path.resolve()),
        "sha256": sha256_file(gate_path),
        "passed": gate.get("passed") is True,
        "metrics_match_recomputation": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-formal", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    quarantine_path = root / "v55_failed_formal_fingerprint_quarantine.json"
    state_path = root / "package/formal_state.json"
    if not root.is_dir() or not quarantine_path.is_file() or not state_path.is_file():
        raise AuditFailure("v57 root/state/quarantine is incomplete")
    quarantine = load(quarantine_path)
    quarantined = str(quarantine.get("fingerprint_id") or "")
    if (
        quarantine.get("status") != "quarantined_no_retry"
        or quarantine.get("retry_allowed") is not False
        or not quarantined.startswith("three-level-sha256:")
    ):
        raise AuditFailure("v55 failed fingerprint quarantine contract mismatch")

    selections = []
    diagnostics = []
    rank_gates = []
    for phase in diagnostic_roots(root):
        selection_path = phase / "top5.json"
        summary_path = phase / "replay/summary.json"
        if not selection_path.is_file() or not summary_path.is_file():
            raise AuditFailure(f"sealed diagnostic phase is incomplete: {phase}")
        selections.append(
            audit_selection(
                selection_path,
                formal=False,
                quarantined_fingerprint=quarantined,
            )
        )
        diagnostics.append(audit_diagnostic(summary_path, selection_path))
        rank_gate = audit_clean5_rank_gate(selection_path, summary_path)
        if rank_gate is not None:
            rank_gate["sealed_gate"] = validate_gate_artifact(phase, rank_gate)
            rank_gates.append(rank_gate)

    formals = []
    for formal_root in sorted(root.glob("cycle_*/formal")):
        selection_path = formal_root / "package/top5_promoted_formal.json"
        summary_path = formal_root / "replay/summary.json"
        if selection_path.is_file():
            selections.append(
                audit_selection(
                    selection_path,
                    formal=True,
                    quarantined_fingerprint=quarantined,
                )
            )
        if summary_path.is_file():
            if not selection_path.is_file():
                raise AuditFailure(f"formal summary has no selection: {formal_root}")
            cycle = formal_root.parent
            clean_discoveries = [
                path
                for path in sorted(cycle.glob("discovery_replay_*/top5.json"))
                if (path.parent / "replay/summary.json").is_file()
                and int(
                    load(path.parent / "replay/summary.json").get(
                        "successful_real_replay_count", -1
                    )
                )
                == 5
                and int(
                    load(path.parent / "replay/summary.json").get(
                        "technical_failure_count", -1
                    )
                )
                == 0
            ]
            if (
                not clean_discoveries
                or exact_parameter_set(selection_path)
                != exact_parameter_set(clean_discoveries[-1])
            ):
                raise AuditFailure(
                    f"formal candidates differ from clean discovery: {formal_root}"
                )
            passed_bindings = [
                load(path)
                for path in cycle.glob("repeat_*/entry_gate_binding_audit.json")
                if load(path).get("passed") is True
            ]
            if not passed_bindings:
                raise AuditFailure(f"formal started without a passed sealed gate: {formal_root}")
            formals.append(audit_formal(summary_path, selection_path))

    exclusions = []
    for path in sorted(root.glob("cycle_*/global_exclusion.json")):
        value = load(path)
        identifiers = {
            str((item.get("three_level_fingerprint") or {}).get("id") or "")
            for item in value.get("fingerprints") or []
            if isinstance(item, Mapping)
        }
        if quarantined not in identifiers:
            raise AuditFailure(f"v55 failed fingerprint absent from exclusion: {path}")
        exclusions.append({"path": str(path), "sha256": sha256_file(path), "failed_fingerprint_excluded": True})

    repeat_bindings = []
    for cycle in sorted(root.glob("cycle_*")):
        repeat_bindings.extend(audit_cycle_repeat_binding(cycle))

    # Every formal technical fingerprint becomes a permanent no-retry item for
    # lexically later selections.  The inherited v55 item is already checked
    # by audit_selection above.
    seen_technical = {quarantined}
    for cycle in sorted(root.glob("cycle_*")):
        candidate_paths = [
            *sorted(cycle.glob("fresh_discovery_*/top5.json")),
            *sorted(cycle.glob("discovery_replay_*/top5.json")),
            *sorted(cycle.glob("repeat_*/top5.json")),
        ]
        if candidate_paths:
            exclusion_path = cycle / "global_exclusion.json"
            if not exclusion_path.is_file():
                raise AuditFailure(f"cycle with candidates lacks global exclusion: {cycle}")
            excluded = {
                str((item.get("three_level_fingerprint") or {}).get("id") or "")
                for item in load(exclusion_path).get("fingerprints") or []
                if isinstance(item, Mapping)
            }
            if quarantined not in excluded:
                raise AuditFailure(f"cycle exclusion omits v55 failed fingerprint: {cycle}")
        formal_selection = cycle / "formal/package/top5_promoted_formal.json"
        if formal_selection.is_file():
            candidate_paths.append(formal_selection)
        for path in candidate_paths:
            identifiers = {fingerprint_id(item) for item in candidate_list(load(path), path)}
            retried = identifiers & seen_technical
            if retried:
                raise AuditFailure(
                    f"technical formal fingerprint retried in later selection: {path}: {sorted(retried)}"
                )
        formal_summary = cycle / "formal/replay/summary.json"
        if formal_summary.is_file() and formal_selection.is_file():
            selection_candidates = candidate_list(load(formal_selection), formal_selection)
            for failure in load(formal_summary).get("technical_failures") or []:
                if not isinstance(failure, Mapping):
                    raise AuditFailure(f"malformed formal technical row: {formal_summary}")
                rank = int(failure.get("rank", -1))
                if rank < 1 or rank > 5:
                    raise AuditFailure(f"formal technical rank is invalid: {formal_summary}")
                seen_technical.add(fingerprint_id(selection_candidates[rank - 1]))

    partial = partial_phase_audits(root)

    if args.require_formal and not formals:
        raise AuditFailure("no sealed v57 formal batch is available")
    state = load(state_path)
    accepted = int(state.get("accepted_count", -1))
    accepted_counter_path = root / "formal_search_accepted_counter.txt"
    accepted_counter = int(accepted_counter_path.read_text(encoding="utf-8").strip())
    if accepted < 0 or accepted > 10 or accepted != accepted_counter:
        raise AuditFailure("v57 state/accepted counter mismatch")
    controller_state_path = root / "controller_state.json"
    if controller_state_path.is_file():
        controller_state = load(controller_state_path)
        if (
            controller_state.get("manual_candidate_injection") is not False
            or controller_state.get("qps_used_for_inner_scoring") is not False
            or int(controller_state.get("target_accepted", -1)) != 10
        ):
            raise AuditFailure("v57 autonomous controller contract mismatch")
    result = {
        "schema_version": "job_v57_first_batch_offline_audit_v1",
        "status": (
            "passed_with_running_diagnostic"
            if partial
            else ("passed" if formals else "passed_waiting_for_formal")
        ),
        "root": str(root),
        "state_sha256": sha256_file(state_path),
        "accepted_count": accepted,
        "accepted_target": 10,
        "selection_audits": selections,
        "diagnostic_audits": diagnostics,
        "rank_gate_audits": rank_gates,
        "repeat_binding_audits": repeat_bindings,
        "partial_phase_audits": partial,
        "formal_audits": formals,
        "exclusion_audits": exclusions,
        "failed_fingerprint": quarantined,
        "failed_fingerprint_retry_observed": False,
        "physical_cap_per_batch": 5,
        "required_candidate_fields": list(REQUIRED_CANDIDATE_FIELDS),
        "qps_used_for_inner_scoring": False,
        "new_workload_calls": 0,
        "ssh_calls": 0,
        "mysql_touched": False,
    }
    if args.output:
        atomic_json(args.output.resolve(), result)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditFailure as error:
        print(json.dumps({"status": "failed", "error": str(error)}, sort_keys=True), flush=True)
        raise SystemExit(2)
