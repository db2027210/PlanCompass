#!/usr/bin/env python3
"""Seal Algorithm C after correcting a serialization-only boundary miss.

The source run is immutable.  This builder replays no workload and changes no
accepted point.  It verifies the completed Outer-18 evidence, then recomputes
only saved-interval membership with the canonical narrow float tolerance.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v8 as prior_builder
from scripts import run_mysql_four_scene_algorithm_c_v2 as algorithm


SCHEMA = "mysql_algorithm_c_float_boundary_terminal_release_v1"
SCENES = (
    "job_memory",
    "tpch_sf4_memory",
    "hybench_xp_cpu",
    "hybench_xp_io",
)
OUTER18_ACTIVE = ("job_memory", "hybench_xp_cpu", "hybench_xp_io")
EXPECTED_CONFIRMATIONS = {
    "job_memory": 3,
    "tpch_sf4_memory": 0,
    "hybench_xp_cpu": 2,
    "hybench_xp_io": 1,
}
EXPECTED_SOURCE_STABLE = {
    "job_memory": 1,
    "tpch_sf4_memory": 2,
    "hybench_xp_cpu": 1,
    "hybench_xp_io": 1,
}
EXPECTED_SOURCE_CONVERGED = {
    "job_memory": False,
    "tpch_sf4_memory": True,
    "hybench_xp_cpu": False,
    "hybench_xp_io": False,
}
XP_REQUIRED_IQ = {f"IQ{index}" for index in range(1, 8)}
XP_REQUIRED_AT = {f"AT{index}" for index in range(1, 7)}
TPCH_REQUIRED = {f"Q{index}" for index in range(1, 23)}
XP_NATIVE_PROPERTIES = {
    "apclient": "8",
    "tpclient": "8",
    "xapclient": "8",
    "xtpclient": "8",
    "at1_percent": "35",
    "at2_percent": "25",
    "at3_percent": "15",
    "at4_percent": "15",
    "at5_percent": "7",
    "at6_percent": "3",
}
XP_PROHIBITED_KEYS = {
    "ap_query_ids",
    "ap_query_weights",
    "ap_query_sequence",
    "ap_query_sequence_limit",
}


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def _scene_mapping(value: Any, label: str) -> dict[str, Any]:
    result = _mapping(value, label)
    if set(result) != set(SCENES):
        raise RuntimeError(f"{label} scene set differs")
    return result


def _bound(path: Path, label: str) -> dict[str, str]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{label} regular file required: {path}")
    return {"path": str(path), "sha256": algorithm.sha(path)}


def _parse_properties(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip().lower()] = value.strip()
    return result


def _verify_job_workload(row: Mapping[str, Any]) -> dict[str, Any]:
    workload = _mapping(row.get("workload_result"), "JOB workload")
    coverage = _mapping(workload.get("query_coverage"), "JOB coverage")
    required = {str(value) for value in coverage.get("required_queries") or []}
    completed = {str(value) for value in coverage.get("completed_queries") or []}
    if (
        workload.get("adapter") != "job"
        or int(workload.get("query_count") or 0) != 113
        or int(workload.get("concurrency") or 0) != 16
        or int(workload.get("maximum_concurrent_heavy_queries") or 0) != 4
        or coverage.get("coverage_complete") is not True
        or coverage.get("missing_queries") not in ([], None)
        or len(required) != 113
        or completed != required
    ):
        raise RuntimeError("full JOB/113 workload contract differs")
    return {"query_count": 113, "workers": 16, "maximum_heavy": 4}


def _verify_tpch_workload(row: Mapping[str, Any]) -> dict[str, Any]:
    workload = _mapping(row.get("workload_result"), "TPC-H workload")
    coverage = _mapping(workload.get("query_coverage"), "TPC-H coverage")
    required = {str(value) for value in coverage.get("required_queries") or []}
    completed = {str(value) for value in coverage.get("completed_queries") or []}
    lanes = workload.get("lanes") or []
    if (
        len(lanes) != 5
        or coverage.get("coverage_complete") is not True
        or coverage.get("missing_queries") not in ([], None)
        or required != TPCH_REQUIRED
        or completed != TPCH_REQUIRED
        or any(
            not isinstance(lane, Mapping)
            or _mapping(lane.get("query_coverage"), "TPC-H lane coverage").get(
                "coverage_complete"
            ) is not True
            for lane in lanes
        )
    ):
        raise RuntimeError("full TPC-H SF4 Q1-Q22 five-lane contract differs")
    return {"query_count": 22, "lanes": 5, "scale_factor": 4}


def _verify_xp_workload(
    row: Mapping[str, Any], *, worker_root: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    workload = _mapping(row.get("workload_result"), "XP workload")
    coverage = _mapping(workload.get("workload_coverage"), "XP coverage")
    required_iq = {str(value) for value in coverage.get("required_iq") or []}
    completed_iq = {str(value) for value in coverage.get("completed_iq") or []}
    required_at = {str(value) for value in coverage.get("required_at") or []}
    completed_at = {str(value) for value in coverage.get("completed_at") or []}
    properties_path = worker_root / "hybench_mysql.properties"
    coverage_path = worker_root / "native_xp_coverage.json"
    properties = _parse_properties(properties_path)
    if (
        workload.get("adapter") != "hybench"
        or workload.get("mode") != "xp"
        or coverage.get("coverage_complete") is not True
        or required_iq != XP_REQUIRED_IQ
        or completed_iq != XP_REQUIRED_IQ
        or required_at != XP_REQUIRED_AT
        or completed_at != XP_REQUIRED_AT
        or any(properties.get(key) != value for key, value in XP_NATIVE_PROPERTIES.items())
        or XP_PROHIBITED_KEYS.intersection(properties)
    ):
        raise RuntimeError("native HyBench XP workload contract differs")
    return (
        {
            "iq": sorted(XP_REQUIRED_IQ),
            "at": sorted(XP_REQUIRED_AT),
            "at_percentages": [35, 25, 15, 15, 7, 3],
            "xap_xtp_ratio": "1:1",
            "runtime_jar_sha256": str(workload.get("runtime_jar_sha256") or ""),
        },
        {
            "properties": _bound(properties_path, "XP properties"),
            "coverage": _bound(coverage_path, "XP native coverage"),
        },
    )


def _verify_outer18_wave(
    run_root: Path, rank: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    outer_root = run_root / "outer_018"
    batch_path = outer_root / f"top5_batch_{rank}" / "batch_result.json"
    batch = algorithm.load(batch_path)
    rows = _mapping(batch.get("measurements"), "Outer-18 measurements")
    rows_by_scene = {
        str(_mapping(row, "measurement").get("scene")): _mapping(row, "measurement")
        for row in rows.values()
    }
    if (
        batch.get("status") != "PASS"
        or batch.get("formal_scope") != "mysql_only"
        or batch.get("cross_engine_comparison_prohibited") is not True
        or batch.get("failures") not in ([], None)
        or set(rows_by_scene) != set(SCENES)
        or not 600 <= float(batch.get("elapsed_seconds") or 0) <= 900
    ):
        raise RuntimeError(f"Outer-18 rank {rank} batch contract differs")
    for scene, row in rows_by_scene.items():
        expected_rank = 0 if scene == "tpch_sf4_memory" else rank
        hard_checks = _mapping(row.get("hard_gate_checks"), "measurement hard gates")
        if (
            int(row.get("rank") or 0) != expected_rank
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            or not hard_checks
            or any(value is not True for value in hard_checks.values())
        ):
            raise RuntimeError(f"Outer-18 rank {rank} {scene} hard gate differs")

    workload_summary: dict[str, Any] = {
        "job_memory": _verify_job_workload(rows_by_scene["job_memory"]),
        "tpch_sf4_memory": _verify_tpch_workload(rows_by_scene["tpch_sf4_memory"]),
    }
    xp_bindings: dict[str, dict[str, dict[str, str]]] = {}
    for scene, worker in (
        ("hybench_xp_cpu", "xp_cpu_w1"),
        ("hybench_xp_io", "xp_io_w1"),
    ):
        summary, bindings = _verify_xp_workload(
            rows_by_scene[scene],
            worker_root=outer_root / f"top5_batch_{rank}" / worker,
        )
        workload_summary[scene] = summary
        xp_bindings[scene] = bindings
    if workload_summary["hybench_xp_cpu"] != workload_summary["hybench_xp_io"]:
        raise RuntimeError("XP/CPU and XP/I/O native workload semantics differ")

    quiet_path = outer_root / (
        f"top5_batch_{rank}_cross_project_wave_quiet_gate_attempt_001.json"
    )
    quiet = algorithm.load(quiet_path)
    if (
        quiet.get("status") != "PASS"
        or quiet.get("formal_scope") != "mysql_only"
        or quiet.get("cross_engine_comparison_prohibited") is not True
        or quiet.get("errors") not in ([], None)
    ):
        raise RuntimeError(f"Outer-18 rank {rank} rolling quiet gate differs")
    return (
        {
            "rank": rank,
            "elapsed_seconds": float(batch["elapsed_seconds"]),
            "hard_gate_passed": True,
            "coverage_complete": True,
            "workloads": workload_summary,
        },
        {
            "batch_result": _bound(batch_path, "Outer-18 batch result"),
            "rolling_quiet_gate": _bound(quiet_path, "rolling quiet gate"),
            "xp": xp_bindings,
        },
    )


def _accepted_evaluation(decision: Mapping[str, Any]) -> dict[str, Any]:
    rows = [
        _mapping(row, "Outer-18 evaluation")
        for row in decision.get("evaluations") or []
        if isinstance(row, Mapping)
        and isinstance(row.get("outer_decision"), Mapping)
        and row["outer_decision"].get("accepted") is True
    ]
    rows.sort(key=lambda row: int(row.get("rank") or 0))
    if not rows:
        raise RuntimeError("accepted Outer-18 evaluation required")
    return rows[0]


def _boundary_audit(
    *, scene: str, prior_state: Mapping[str, Any], decision: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        decision.get("scene") != scene
        or int(decision.get("outer_attempt") or 0) != 18
        or decision.get("accepted") is not True
        or int(decision.get("accepted_rounds") or 0) != 10
        or int(
            _mapping(
                decision.get("post_target_stability_confirmation"),
                "post-target confirmation",
            ).get("confirmation_ordinal")
            or 0
        )
        != EXPECTED_CONFIRMATIONS[scene]
        or int(decision.get("accepted_state_credit_increment") or 0) != 0
    ):
        raise RuntimeError(f"{scene} Outer-18 accepted confirmation differs")
    evaluations = [
        _mapping(row, "decision evaluation")
        for row in decision.get("evaluations") or []
    ]
    if (
        len(evaluations) != 5
        or sorted(int(row.get("rank") or 0) for row in evaluations)
        != [1, 2, 3, 4, 5]
        or any(
            row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            for row in evaluations
        )
    ):
        raise RuntimeError(f"{scene} real Top-5 evidence differs")

    chosen = _accepted_evaluation(decision)
    candidate = {
        name: float(value)
        for name, value in _mapping(chosen.get("params"), "chosen params").items()
    }
    active = list(_scene_mapping(prior_state["active_by_scene"], "active params")[scene])
    previous_interval = _scene_mapping(
        prior_state["stable_intervals"], "prior stable intervals"
    )[scene]
    prior_ranges = _scene_mapping(prior_state["ranges_by_scene"], "prior ranges")[scene]
    shrunk = algorithm.shrink_ranges(prior_ranges, candidate, active)
    persisted = _mapping(decision.get("interval_convergence"), "persisted convergence")
    if (
        persisted.get("candidate_inside_previous_interval") is not False
        or int(persisted.get("stable_rounds") or 0) != 1
        or persisted.get("converged") is not False
        or persisted.get("all_active_parameters_within_threshold") is not True
        or persisted.get("stable_interval") != shrunk
    ):
        raise RuntimeError(f"{scene} persisted false-negative signature differs")

    violations: dict[str, float] = {}
    for name in active:
        bounds = _mapping(previous_interval[name], f"{scene} {name} prior bounds")
        value = float(candidate[name])
        lower = float(bounds["lower"])
        upper = float(bounds["upper"])
        violations[name] = max(lower - value, value - upper, 0.0)
    if max(violations.values()) <= 0:
        raise RuntimeError(f"{scene} has no strict serialized boundary miss")

    recomputed = algorithm.advance_interval_convergence(
        ranges=shrunk,
        active=active,
        candidate_params=candidate,
        accepted=True,
        previous_stable_rounds=1,
        previous_interval=previous_interval,
    )
    if (
        recomputed.get("candidate_inside_previous_interval") is not True
        or int(recomputed.get("stable_rounds") or 0) != 2
        or recomputed.get("converged") is not True
        or recomputed.get("all_active_parameters_within_threshold") is not True
        or float(recomputed.get("boundary_membership_rel_tolerance") or 0)
        != algorithm.CONVERGENCE_BOUNDARY_REL_TOL
        or float(recomputed.get("boundary_membership_abs_tolerance") or 0)
        != algorithm.CONVERGENCE_BOUNDARY_ABS_TOL
    ):
        raise RuntimeError(f"{scene} tolerance-only convergence did not prove")
    return (
        {
            "chosen_rank": int(chosen["rank"]),
            "candidate_params": candidate,
            "strict_membership": False,
            "tolerant_membership": True,
            "absolute_boundary_violations": violations,
            "maximum_absolute_boundary_violation": max(violations.values()),
            "persisted_convergence": persisted,
            "recomputed_convergence": recomputed,
        },
        {
            "params": candidate,
            "incumbent": {
                key: copy.deepcopy(chosen[key])
                for key in ("db_qps", "p95_ms", "p99_ms", "resource_means")
            },
            "ranges": shrunk,
            "stable_interval": copy.deepcopy(recomputed["stable_interval"]),
        },
    )


def _watchdog_audit(path: Path) -> dict[str, Any]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    interference = sum(row.get("interference_detected") is True for row in rows)
    transient_probe_failures = sum(
        isinstance(row.get("co_resident_pg_isolation"), Mapping)
        and row["co_resident_pg_isolation"].get("passed") is False
        for row in rows
    )
    if (
        not rows
        or rows[-1].get("event") != "formal_unit_terminal"
        or rows[-1].get("interference_detected") is not False
        or interference != 0
    ):
        raise RuntimeError("runtime interference watchdog terminal evidence differs")
    return {
        "sample_count": len(rows),
        "interference_detected_true_count": interference,
        "transient_inventory_probe_failure_count": transient_probe_failures,
        "five_pre_wave_rolling_gates_still_required_and_passed": True,
    }


def build_release(run_root: Path, prior_checkpoint_path: Path) -> dict[str, Any]:
    run_root = run_root.resolve()
    prior_checkpoint_path = prior_checkpoint_path.resolve()
    state_path = run_root / "run_state.json"
    source = algorithm.load(state_path)
    if (
        source.get("status") != "NON_CONVERGED"
        or source.get("stage") != "FINISHED"
        or int(source.get("outer_attempt") or 0) != 18
        or source.get("formal_scope") != "mysql_only"
        or source.get("cross_engine_comparison_prohibited") is not True
        or source.get("formal_evidence_eligible") is not True
        or source.get("accepted_state_credit_prohibited") is not False
        or source.get("error") not in (None, "")
        or _scene_mapping(source.get("accepted_rounds"), "source accepted rounds")
        != {scene: 10 for scene in SCENES}
        or _scene_mapping(source.get("interval_stable_rounds"), "source stability")
        != EXPECTED_SOURCE_STABLE
        or _scene_mapping(source.get("converged"), "source convergence")
        != EXPECTED_SOURCE_CONVERGED
        or _scene_mapping(
            source.get("post_target_stability_confirmation_counts"),
            "source confirmation counts",
        )
        != EXPECTED_CONFIRMATIONS
    ):
        raise RuntimeError("Outer-18 terminal source authority differs")
    if (
        Path(str(source.get("checkpoint_path") or "")).resolve()
        != prior_checkpoint_path
        or algorithm.sha(prior_checkpoint_path)
        != str(source.get("checkpoint_sha256") or "")
    ):
        raise RuntimeError("Outer-17 checkpoint binding differs")

    prior = prior_builder.verify_checkpoint(prior_checkpoint_path)
    prior_state = _mapping(prior.get("reconstructed_state"), "prior state")
    if (
        int(prior.get("completed_outer_attempts") or 0) != 17
        or int(prior_state.get("outer_attempt") or 0) != 17
        or _scene_mapping(prior_state.get("accepted_rounds"), "prior accepted rounds")
        != {scene: 10 for scene in SCENES}
        or _scene_mapping(prior_state.get("interval_stable_rounds"), "prior stability")
        != EXPECTED_SOURCE_STABLE
        or _scene_mapping(prior_state.get("converged"), "prior convergence")
        != EXPECTED_SOURCE_CONVERGED
    ):
        raise RuntimeError("Outer-17 checkpoint convergence state differs")

    wave_audits: list[dict[str, Any]] = []
    wave_bindings: list[dict[str, Any]] = []
    for rank in range(1, 6):
        audit, bindings = _verify_outer18_wave(run_root, rank)
        wave_audits.append(audit)
        wave_bindings.append(bindings)

    boundary_audits: dict[str, Any] = {}
    accepted_updates: dict[str, Any] = {}
    decision_bindings: dict[str, Any] = {}
    for scene in OUTER18_ACTIVE:
        decision_path = run_root / "outer_018" / f"{scene}.decision.json"
        decision = algorithm.load(decision_path)
        audit, update = _boundary_audit(
            scene=scene, prior_state=prior_state, decision=decision,
        )
        boundary_audits[scene] = audit
        accepted_updates[scene] = update
        decision_bindings[scene] = _bound(decision_path, f"{scene} decision")

    corrected = copy.deepcopy(prior_state)
    for scene in OUTER18_ACTIVE:
        update = accepted_updates[scene]
        corrected["params_by_scene"][scene] = copy.deepcopy(update["params"])
        corrected["incumbents"][scene] = copy.deepcopy(update["incumbent"])
        corrected["ranges_by_scene"][scene] = copy.deepcopy(update["ranges"])
        corrected["stable_intervals"][scene] = copy.deepcopy(
            update["stable_interval"]
        )
        corrected["interval_stable_rounds"][scene] = 2
        corrected["converged"][scene] = True
        corrected["replayed_param_keys_by_scene"][scene] = []
    for field in (
        "accepted_rounds",
        "rejected_outer_attempts",
        "acceptance_tier",
        "consecutive_failed_rounds",
        "full_plan_replay_history_v2",
    ):
        corrected[field] = copy.deepcopy(
            _scene_mapping(source.get(field), f"source {field}")
        )
    corrected["post_target_stability_confirmation_counts"] = copy.deepcopy(
        EXPECTED_CONFIRMATIONS
    )
    corrected["outer_attempt"] = 18
    if (
        corrected["accepted_rounds"] != {scene: 10 for scene in SCENES}
        or corrected["interval_stable_rounds"]
        != {scene: 2 for scene in SCENES}
        or corrected["converged"] != {scene: True for scene in SCENES}
        or any(
            not algorithm.interval_convergence_audit(
                corrected["ranges_by_scene"][scene],
                corrected["active_by_scene"][scene],
            )["all_active_parameters_within_threshold"]
            for scene in SCENES
        )
    ):
        raise RuntimeError("corrected terminal state does not prove convergence")

    companion_path = run_root / "outer_018" / "companion_audit.json"
    companion = algorithm.load(companion_path)
    if (
        companion.get("status") != "PASS"
        or companion.get("full_workload_contract_preserved") is not True
    ):
        raise RuntimeError("Outer-18 companion audit differs")
    watchdog_path = run_root / "runtime_external_interference_watchdog.jsonl"
    watchdog_audit = _watchdog_audit(watchdog_path)
    predicted_path = run_root / "outer_018" / "predicted_rankings.json"

    source_bindings: dict[str, Any] = {
        "source_run_state": _bound(state_path, "source run state"),
        "prior_outer17_checkpoint": _bound(
            prior_checkpoint_path, "prior Outer-17 checkpoint"
        ),
        "predicted_rankings": _bound(predicted_path, "predicted rankings"),
        "companion_audit": _bound(companion_path, "companion audit"),
        "runtime_interference_watchdog": _bound(watchdog_path, "watchdog"),
        "decisions": decision_bindings,
        "waves": wave_bindings,
        "algorithm_source": _bound(
            ROOT / "scripts/run_mysql_four_scene_algorithm_c_v2.py",
            "Algorithm C source",
        ),
        "regression_test_source": _bound(
            ROOT / "tests/test_mysql_algorithm_c_interval_convergence.py",
            "boundary regression test",
        ),
        "builder_source": _bound(Path(__file__), "terminal release builder"),
    }
    for name, path_key, sha_key in (
        ("resource_freeze", "freeze_path", "freeze_sha256"),
        (
            "resource_model_contract",
            "resource_model_contract_path",
            "resource_model_contract_sha256",
        ),
        (
            "optimization_contract",
            "optimization_contract_path",
            "optimization_contract_sha256",
        ),
    ):
        path = Path(str(source.get(path_key) or "")).resolve()
        binding = _bound(path, name)
        if binding["sha256"] != source.get(sha_key):
            raise RuntimeError(f"{name} source binding differs")
        source_bindings[name] = binding

    document: dict[str, Any] = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "algorithm": "C",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_run_root": str(run_root),
        "source_state_preserved_immutable": True,
        "workload_replayed_for_recovery": False,
        "accepted_point_changed_by_recovery": False,
        "completed_outer_attempts": 18,
        "terminal_rule": {
            "accepted_binary_shrinks": 10,
            "maximum_interval_ratio": 1.01,
            "consecutive_stable_accepted_rounds": 2,
            "boundary_membership_rel_tolerance": (
                algorithm.CONVERGENCE_BOUNDARY_REL_TOL
            ),
            "boundary_membership_abs_tolerance": (
                algorithm.CONVERGENCE_BOUNDARY_ABS_TOL
            ),
        },
        "terminal_summary": {
            "accepted_rounds": {scene: 10 for scene in SCENES},
            "interval_stable_rounds": {scene: 2 for scene in SCENES},
            "converged": {scene: True for scene in SCENES},
            "all_four_scenes_converged": True,
            "all_five_outer18_waves_passed": True,
            "all_workload_and_hard_gates_passed": True,
            "all_outer18_batch_elapsed_seconds_within_600_900": True,
        },
        "boundary_recovery_audit": boundary_audits,
        "outer18_wave_audit": wave_audits,
        "runtime_watchdog_audit": watchdog_audit,
        "reconstructed_terminal_state": corrected,
        "source_bindings": source_bindings,
    }
    document["release_sha256"] = _canonical_sha256(document)
    return document


def verify_release(path: Path) -> dict[str, Any]:
    value = algorithm.load(path)
    payload = copy.deepcopy(value)
    expected_sha = str(payload.pop("release_sha256", ""))
    bindings = _mapping(value.get("source_bindings"), "release bindings")
    source_state = _mapping(bindings.get("source_run_state"), "source state binding")
    prior = _mapping(
        bindings.get("prior_outer17_checkpoint"), "prior checkpoint binding"
    )
    if (
        value.get("schema_version") != SCHEMA
        or value.get("status") != "COMPLETE"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or _canonical_sha256(payload) != expected_sha
    ):
        raise RuntimeError("terminal release contract or canonical SHA differs")
    rebuilt = build_release(
        Path(str(value.get("source_run_root") or "")),
        Path(str(prior.get("path") or "")),
    )
    if rebuilt != value:
        raise RuntimeError("terminal release no longer derives from bound evidence")
    if algorithm.sha(Path(str(source_state.get("path") or ""))) != source_state.get(
        "sha256"
    ):
        raise RuntimeError("source terminal state binding differs")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--prior-checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify is not None:
        value = verify_release(args.verify.resolve())
        print(json.dumps({
            "status": value["status"],
            "release": str(args.verify.resolve()),
            "release_sha256": value["release_sha256"],
            "converged": value["terminal_summary"]["converged"],
        }, sort_keys=True))
        return 0
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory name")
    if args.output_dir.exists():
        raise RuntimeError(f"fresh terminal release output exists: {args.output_dir}")
    document = build_release(
        args.run_root.resolve(), args.prior_checkpoint.resolve()
    )
    args.output_dir.mkdir(parents=True, mode=0o700)
    path = args.output_dir / "algorithm_c_terminal_release.json"
    algorithm.atomic(path, document)
    verify_release(path)
    print(json.dumps({
        "status": document["status"],
        "release": str(path),
        "release_sha256": document["release_sha256"],
        "converged": document["terminal_summary"]["converged"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
