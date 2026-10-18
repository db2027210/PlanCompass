#!/usr/bin/env python3
"""Durable formal MySQL Recost Session 6 worker."""

from __future__ import annotations

from dataclasses import asdict
import argparse
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import sys
import time
from typing import Any, Mapping, Sequence

from formal_acceptance_policy import (
    EligibleFinalPoint,
    LivePoint,
    decide_outer_candidate,
    select_final_point,
)
from formal_baseline_gates import PressureEvidence, evaluate_default_baseline
from formal_mysql_random_walk_point import run_point
import formal_mysql_random_walk_session as rw
from formal_mysql_recost_spec import (
    ENGINE,
    METHOD,
    SESSION_ID,
    SHARED_FROZEN,
    RecostRunSpec,
    load_recost_schedule,
    validate_against_frozen_matrices,
    validate_authoritative_files,
)
from formal_random_walk_artifacts import (
    append_jsonl,
    atomic_write_bytes,
    atomic_write_json,
    fsync_artifact_tree,
    sha256_file,
    validate_checksum_manifest,
    write_checksum_manifest,
)
from formal_recost_controller import ReplayResult, build_frozen_estimator_factory
from formal_recost_core import FrozenRecostProposalEngine, load_frozen_recost_contract
from formal_recost_scheduler import RecostScheduleLease, SchedulerNotOurTurn
from formal_session_runtime_20260721 import (
    FREEZE_ID,
    GLOBAL_CAMPAIGN_ID,
    GLOBAL_ROOT,
    SHARED_ROOT,
    load_json,
    validate_all_ready,
    validate_scheduler_state,
    validate_shared_bundle,
    utc_now,
)


METHOD_ROOT = GLOBAL_ROOT / "mysql" / METHOD
RUNNER_SOURCES = tuple(dict.fromkeys((
    Path(__file__).resolve(),
    Path(__file__).resolve().parent / "formal_mysql_recost_spec.py",
    Path(__file__).resolve().parent / "formal_recost_core.py",
    Path(__file__).resolve().parent / "formal_recost_controller.py",
    Path(__file__).resolve().parent / "formal_recost_scheduler.py",
    Path(__file__).resolve().parent / "formal_recost_estimator.py",
    *(
        path for path in rw.RUNNER_SOURCES
        if path.name != "formal_mysql_engine_gate_20260721.py"
    ),
)))


class SessionError(RuntimeError):
    pass


class RunBlocked(SessionError):
    pass


def _config(name: str) -> dict[str, Any]:
    value = load_json(SHARED_FROZEN / name)
    if not isinstance(value, dict):
        raise SessionError(f"frozen config must be an object: {name}")
    return value


def _source_hashes() -> dict[str, str]:
    return {str(path): sha256_file(path) for path in RUNNER_SOURCES}


def static_validation() -> dict[str, Any]:
    shared = validate_shared_bundle()
    authority = validate_authoritative_files()
    specs = load_recost_schedule()
    validate_against_frozen_matrices(specs)
    settings, parameters = load_frozen_recost_contract(
        SHARED_FROZEN / "method_protocols.json",
        SHARED_FROZEN / "search_spaces.json",
    )
    factory = build_frozen_estimator_factory(specs[0].case_id)
    if not callable(factory):
        raise SessionError("frozen estimator factory is not callable")
    return {
        "status": "passed",
        "shared": shared,
        "authority": authority,
        "target_count": len(specs),
        "target_ordinals": [item.ordinal for item in specs],
        "recost_settings": asdict(settings),
        "parameter_order": [item.name for item in parameters],
        "runner_source_sha256": _source_hashes(),
    }


def ready_validation() -> dict[str, Any]:
    return {"status": "passed", "ready": validate_all_ready()}


def _default_params() -> dict[str, float]:
    return {
        name: float(value)
        for name, value in _config("search_spaces.json")["mysql"]["default"].items()
    }


def _run_root(spec: RecostRunSpec) -> Path:
    path = GLOBAL_ROOT / "mysql" / spec.output_relative_dir
    if path.resolve(strict=False).parent != METHOD_ROOT.resolve(strict=False):
        raise SessionError(f"unsafe Run root: {path}")
    return path


def _state_path(run_root: Path) -> Path:
    return run_root / "run_state.json"


def _save_state(run_root: Path, state: Mapping[str, Any]) -> None:
    atomic_write_json(_state_path(run_root), state)


def _assert_source_pin(state: Mapping[str, Any]) -> None:
    expected = state["static_validation"]["runner_source_sha256"]
    observed = _source_hashes()
    if observed != expected:
        changed = {
            path: {"expected": expected.get(path), "observed": observed.get(path)}
            for path in set(expected) | set(observed)
            if expected.get(path) != observed.get(path)
        }
        raise RunBlocked(f"worker source changed after scheduler claim: {changed}")


def _new_state(
    run_root: Path,
    spec: RecostRunSpec,
    attempt_id: str,
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    if run_root.is_symlink():
        raise SessionError(f"claimed Run output is a symlink: {run_root}")
    if run_root.exists():
        if not run_root.is_dir() or {item.name for item in run_root.iterdir()} - {"method_manifest.json"}:
            raise SessionError(f"unrecognized pre-state Run output exists: {run_root}")
    else:
        run_root.mkdir(parents=True, mode=0o700)
    state: dict[str, Any] = {
        "schema_version": 1,
        "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID,
        "engine": ENGINE,
        "method": METHOD,
        "session_id": SESSION_ID,
        "status": "running",
        "stage": "baseline_search",
        "attempt_id": attempt_id,
        "schedule": spec.as_dict(),
        "started_at_utc": utc_now(),
        "static_validation": validation,
        "baseline_point": None,
        "baseline_gate": None,
        "correctness_signature": None,
        "candidates": [],
        "pending_candidate": None,
        "final_selection": None,
        "final_params": None,
        "default_validations": [],
        "final_validations": [],
        "blocker": None,
    }
    method_manifest = {
            "schema_version": 1,
            "freeze_id": FREEZE_ID,
            "global_campaign_id": GLOBAL_CAMPAIGN_ID,
            "engine": ENGINE,
            "method": METHOD,
            "frozen_protocol": _config("method_protocols.json")[METHOD],
            "frozen_estimator_contract_sha256": sha256_file(
                SHARED_FROZEN / "recost_estimator.json"
            ),
            "runner_source_sha256": validation["runner_source_sha256"],
        }
    method_path = run_root / "method_manifest.json"
    if method_path.exists():
        if load_json(method_path) != method_manifest:
            raise SessionError("pre-state method manifest differs during recovery")
    else:
        atomic_write_json(method_path, method_manifest, replace=False)
    _save_state(run_root, state)
    return state


def _load_state(run_root: Path, spec: RecostRunSpec, attempt_id: str) -> dict[str, Any]:
    state = load_json(_state_path(run_root))
    if not isinstance(state, dict):
        raise SessionError("run_state is not an object")
    fixed = {
        "schema_version": 1,
        "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID,
        "engine": ENGINE,
        "method": METHOD,
        "session_id": SESSION_ID,
        "attempt_id": attempt_id,
    }
    for name, expected in fixed.items():
        if state.get(name) != expected:
            raise SessionError(f"resumed run_state.{name} mismatch")
    if state.get("schedule") != spec.as_dict():
        raise SessionError("resumed Run schedule differs")
    if state.get("status") == "blocked":
        raise RunBlocked(str(state.get("blocker")))
    if state.get("status") != "running":
        raise SessionError(f"active entry has terminal state {state.get('status')}")
    _assert_source_pin(state)
    return state


def _point_parent(run_root: Path, label: str) -> Path:
    if label == "baseline_search":
        return run_root / "round_000_baseline"
    if label.startswith("candidate_"):
        return run_root / f"round_{int(label.split('_')[1]):03d}"
    return run_root / "validation" / label


def _terminal_point(path: Path) -> dict[str, Any] | None:
    result = path / "point_result.json"
    checksum = path / "checksums.sha256"
    if result.is_file() and checksum.is_file():
        try:
            validate_checksum_manifest(path)
        except Exception:
            return None
        value = load_json(result)
        if isinstance(value, dict) and value.get("status") in {
            "complete", "rejected", "failed", "timeout", "oom"
        }:
            return value
    return None


def _execute_point(
    run_root: Path,
    state: Mapping[str, Any],
    spec: RecostRunSpec,
    label: str,
    params: Mapping[str, float],
    expected_correctness: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], Path]:
    _assert_source_pin(state)
    parent = _point_parent(run_root, label)
    attempt = 1
    while (parent / f"attempt_{attempt:02d}").exists():
        path = parent / f"attempt_{attempt:02d}"
        existing = _terminal_point(path)
        if existing is not None:
            return existing, path
        import formal_mysql_campaign_20260721 as common
        machine = _config("machine_and_resources.json")
        container = machine["engines"]["mysql"]["containers"][spec.container_key]["name"]
        common._remove_container(container)
        working_root = Path(machine["engines"]["mysql"]["working_root"])
        working = working_root / METHOD / state["attempt_id"] / label / f"attempt_{attempt:02d}"
        common._cleanup_working(working, working_root)
        for ephemeral in (path / "private", path / "hybench_runtime"):
            if ephemeral.is_symlink():
                ephemeral.unlink()
            elif ephemeral.exists():
                shutil.rmtree(ephemeral)
        if spec.benchmark == "tpch":
            common._redact_benchbase_configs(path)
        marker = path / "interrupted_attempt.json"
        if not marker.exists():
            atomic_write_json(marker, {
                "status": "interrupted",
                "detected_at_utc": utc_now(),
                "replacement_attempt": attempt + 1,
                "candidate_budget_replacement": False,
            }, replace=False)
        fsync_artifact_tree(path)
        write_checksum_manifest(path)
        attempt += 1
    admission_path = run_root / "admission" / label / f"attempt_{attempt:02d}.jsonl"
    admission_path.parent.mkdir(parents=True, exist_ok=True)
    admission = rw.wait_host_admission(admission_path)
    output = parent / f"attempt_{attempt:02d}"
    result = run_point(
        run_attempt_id=str(state["attempt_id"]),
        point_id=label,
        point_attempt=attempt,
        case_id=spec.case_id,
        params=params,
        output=output,
        expected_correctness=expected_correctness,
        method=METHOD,
        lock_marker_environment="FORMAL_MYSQL_RECOST_LOCK_HELD",
    )
    atomic_write_json(
        run_root / "admission" / label / f"attempt_{attempt:02d}_summary.json",
        admission,
        replace=False,
    )
    validate_checksum_manifest(output)
    return result, output


def _block(run_root: Path, state: dict[str, Any], reason: str, evidence: Any) -> None:
    state["status"] = "blocked"
    state["blocker"] = {"reason": reason, "evidence": evidence, "recorded_at_utc": utc_now()}
    _save_state(run_root, state)
    raise RunBlocked(f"{reason}: {evidence}")


def _initialize_baseline(
    run_root: Path, state: dict[str, Any], spec: RecostRunSpec
) -> None:
    if state["baseline_point"] is not None:
        return
    result, path = _execute_point(
        run_root, state, spec, "baseline_search", _default_params(), None
    )
    if result["status"] != "complete":
        _block(run_root, state, "default_search_point_failed", {
            "path": str(path), "status": result["status"]
        })
    baseline_artifacts = {
        "round_manifest.json": {
            "schema_version": 1, "method": METHOD, "round": 0,
            "inner_loop_applicable": False, "point_role": "baseline",
        },
        "params_before.json": {"params": _default_params()},
        "params_candidate.json": {"params": _default_params()},
        "params_applied.json": {"params": result["params_applied"]},
        "plan_fingerprint.json": {
            "plan_candidate_sha256": result["plan_fingerprint_sha256"],
            "plan_changed": False,
        },
        "round_summary.json": {
            "schema_version": 1, "round": 0, "point_status": result["status"],
            "point_role": "baseline",
        },
    }
    for name, payload in baseline_artifacts.items():
        target = path / name
        if not target.exists():
            atomic_write_json(target, payload, replace=False)
    inner = path / "inner_iterations.jsonl"
    if not inner.exists():
        atomic_write_bytes(inner, b"", replace=False)
    fsync_artifact_tree(path)
    write_checksum_manifest(path)
    validate_checksum_manifest(path)
    live = LivePoint(**result["live_point"])
    pressure = PressureEvidence(**result["pressure_decision_input"])
    gate = evaluate_default_baseline(
        SHARED_FROZEN / "measurement_protocol.json",
        live,
        pressure,
        scenario=spec.scenario,
        xp=spec.benchmark == "hybench_xp",
    )
    state["baseline_point"] = {
        "path": str(path),
        "params": result["params_applied"],
        "live_point": asdict(live),
        "plan_fingerprint_sha256": result["plan_fingerprint_sha256"],
    }
    state["baseline_gate"] = asdict(gate)
    state["correctness_signature"] = result["correctness_signature"]
    if not gate.passed:
        _block(run_root, state, "default_baseline_gate_failed", asdict(gate))
    state["stage"] = "candidates"
    _save_state(run_root, state)


def _new_engine() -> FrozenRecostProposalEngine:
    settings, parameters = load_frozen_recost_contract(
        SHARED_FROZEN / "method_protocols.json",
        SHARED_FROZEN / "search_spaces.json",
    )
    return FrozenRecostProposalEngine(
        settings, parameters, estimator_version=settings.estimator_version
    )


def _replay_from_record(record: Mapping[str, Any]) -> ReplayResult:
    point = record.get("live_point")
    return ReplayResult(
        status=str(record["point_status"]),
        params={name: float(value) for name, value in record["params_replayed"].items()},
        measurement=LivePoint(**point) if isinstance(point, Mapping) else None,
        plan_fingerprints=(
            {"target_sql": str(record["plan_fingerprint_sha256"])}
            if record.get("plan_fingerprint_sha256") else {}
        ),
        failure_reason=record.get("failure_reason"),
        artifact_relative_path=str(record["path"]),
    )


def _causal_state(state: Mapping[str, Any], spec: RecostRunSpec) -> tuple[
    FrozenRecostProposalEngine, ReplayResult, Any
]:
    engine = _new_engine()
    baseline = state["baseline_point"]
    incumbent = ReplayResult(
        status="complete",
        params={name: float(value) for name, value in baseline["params"].items()},
        measurement=LivePoint(**baseline["live_point"]),
        plan_fingerprints={"target_sql": baseline["plan_fingerprint_sha256"]},
        artifact_relative_path=baseline["path"],
    )
    factory = build_frozen_estimator_factory(spec.case_id)
    estimator = factory(engine, incumbent, spec.scenario)
    for record in state["candidates"]:
        accepted = bool(record["outer_candidate_accepted"])
        engine.record_outer_result(outer_candidate_accepted=accepted)
        replay = _replay_from_record(record)
        if accepted:
            if not replay.successful:
                raise SessionError("accepted durable candidate is not a successful replay")
            incumbent = replay
            estimator = factory(engine, incumbent, spec.scenario)
        else:
            estimator.observe_outer(
                params=record["params_proposed"],
                observed_utility_improvement=float(record["observed_utility_improvement"]),
                outer_candidate_accepted=False,
            )
    return engine, incumbent, estimator


def _record_candidate_artifacts(
    point_path: Path,
    inner_path: Path,
    decision: Mapping[str, Any],
) -> None:
    def ensure_json(name: str, payload: Mapping[str, Any]) -> None:
        target = point_path / name
        if target.exists():
            if load_json(target) != dict(payload):
                raise SessionError(f"durable candidate artifact differs on resume: {name}")
        else:
            atomic_write_json(target, payload, replace=False)

    inner_target = point_path / "inner_iterations.jsonl"
    inner_bytes = inner_path.read_bytes()
    if inner_target.exists():
        if inner_target.is_symlink() or inner_target.read_bytes() != inner_bytes:
            raise SessionError("durable candidate Inner log differs on resume")
    else:
        atomic_write_bytes(inner_target, inner_bytes, replace=False)
    ensure_json("decision.json", decision)
    ensure_json("params_before.json", {"params": decision["params_before"]})
    ensure_json("params_candidate.json", {"params": decision["params_candidate"]})
    ensure_json("params_applied.json", {"params": decision["params_applied"]})
    ensure_json("plan_fingerprint.json", {
        "plan_before_sha256": decision["plan_before_sha256"],
        "plan_candidate_sha256": decision["plan_candidate_sha256"],
        "plan_changed": (
            decision["plan_before_sha256"] != decision["plan_candidate_sha256"]
        ),
    })
    ensure_json("round_manifest.json", {
        "schema_version": 1,
        "method": METHOD,
        "round": decision["candidate_index"],
        "inner_loop_applicable": True,
        "outer_requires_live_replay": True,
    })
    ensure_json("round_summary.json", {
        "schema_version": 1,
        "round": decision["candidate_index"],
        "point_status": decision["point_status"],
        "outer_candidate_accepted": decision["outer_candidate_accepted"],
        "observed_utility_improvement": decision["observed_utility_improvement"],
        "decision_path": "decision.json",
        "inner_iterations_path": "inner_iterations.jsonl",
    })
    fsync_artifact_tree(point_path)
    write_checksum_manifest(point_path)
    validate_checksum_manifest(point_path)


def _search_candidates(
    run_root: Path, state: dict[str, Any], spec: RecostRunSpec
) -> None:
    while True:
        engine, incumbent, estimator = _causal_state(state, spec)
        if engine.real_candidate_count >= engine.settings.outer_real_candidate_limit:
            stop_reason = "outer_real_candidate_limit"
            break
        if (
            engine.real_candidate_count >= engine.settings.minimum_real_candidates_before_stop
            and engine.consecutive_outer_rejections
            >= engine.settings.consecutive_outer_rejections
        ):
            stop_reason = "consecutive_outer_rejections_after_minimum_candidates"
            break
        index = engine.real_candidate_count + 1
        pending = state.get("pending_candidate")
        if pending is None:
            inner_path = run_root / "inner" / f"candidate_{index}.jsonl"
            inner_path.parent.mkdir(parents=True, exist_ok=True)
            if inner_path.exists() or inner_path.is_symlink():
                archive = run_root / "inner" / "interrupted_before_pending_state"
                archive.mkdir(parents=True, exist_ok=True)
                attempt = 1
                while (archive / f"candidate_{index}_attempt_{attempt:02d}.jsonl").exists():
                    attempt += 1
                os.replace(inner_path, archive / f"candidate_{index}_attempt_{attempt:02d}.jsonl")
            with inner_path.open("x", encoding="utf-8") as handle:
                proposal = engine.propose(incumbent.params, estimator, inner_log=handle)
                handle.flush()
                os.fsync(handle.fileno())
            pending = {
                "candidate_index": index,
                "params_before": dict(incumbent.params),
                "proposal": asdict(proposal),
                "estimator_state_before": estimator.state(),
                "inner_iterations_path": str(inner_path),
                "created_at_utc": utc_now(),
            }
            state["pending_candidate"] = pending
            _save_state(run_root, state)
        elif int(pending["candidate_index"]) != index:
            raise SessionError("pending candidate index differs from causal Outer state")
        proposal = pending["proposal"]
        params = {name: float(value) for name, value in proposal["params"].items()}
        result, point_path = _execute_point(
            run_root,
            state,
            spec,
            f"candidate_{index}",
            params,
            state["correctness_signature"],
        )
        decision = None
        accepted = False
        observed_utility = -1.0
        live_point = result.get("live_point") if result["status"] == "complete" else None
        plan_sha = result.get("plan_fingerprint_sha256")
        params_replayed = result.get("params_applied") or params
        if live_point is not None:
            outer = decide_outer_candidate(
                SHARED_FROZEN / "acceptance_policy.json",
                incumbent.measurement,
                LivePoint(**live_point),
                bottleneck=spec.scenario,
                xp=spec.benchmark == "hybench_xp",
                plan_changed=(
                    {"target_sql": str(plan_sha)} != dict(incumbent.plan_fingerprints)
                ),
            )
            decision = asdict(outer)
            accepted = outer.accepted
            observed_utility = float(outer.utility_improvement)
        early_stop = engine.record_outer_result(outer_candidate_accepted=accepted)
        if accepted:
            accepted_replay = ReplayResult(
                status="complete",
                params={name: float(value) for name, value in params_replayed.items()},
                measurement=LivePoint(**live_point),
                plan_fingerprints={"target_sql": str(plan_sha)},
                artifact_relative_path=str(point_path),
            )
            estimator_after = build_frozen_estimator_factory(spec.case_id)(
                engine, accepted_replay, spec.scenario
            )
        else:
            estimator.observe_outer(
                params=params,
                observed_utility_improvement=observed_utility,
                outer_candidate_accepted=False,
            )
            estimator_after = estimator
        existing_decision_path = point_path / "decision.json"
        existing_decision = (
            load_json(existing_decision_path) if existing_decision_path.is_file() else None
        )
        decided_at = (
            existing_decision.get("decided_at_utc")
            if isinstance(existing_decision, Mapping)
            else utc_now()
        )
        decision_artifact = {
            "schema_version": 1,
            "label": "outer_candidate_accepted",
            "candidate_index": index,
            "outer_candidate_accepted": accepted,
            "budget_consumed": True,
            "inner_estimator_made_outer_decision": False,
            "acceptance_policy_path": str(SHARED_FROZEN / "acceptance_policy.json"),
            "acceptance_policy_sha256": sha256_file(SHARED_FROZEN / "acceptance_policy.json"),
            "params_before": pending["params_before"],
            "params_candidate": params,
            "params_applied": params_replayed,
            "plan_before_sha256": next(iter(incumbent.plan_fingerprints.values())),
            "plan_candidate_sha256": plan_sha,
            "point_status": result["status"],
            "decision": decision,
            "observed_utility_improvement": observed_utility,
            "early_stop_after_candidate": early_stop,
            "decided_at_utc": decided_at,
        }
        _record_candidate_artifacts(
            point_path, Path(pending["inner_iterations_path"]), decision_artifact
        )
        record = {
            "candidate_index": index,
            "params_before": pending["params_before"],
            "params_proposed": params,
            "params_replayed": params_replayed,
            "proposal": proposal,
            "estimator_state_before": pending["estimator_state_before"],
            "estimator_state_after": estimator_after.state(),
            "inner_iterations_path": pending["inner_iterations_path"],
            "path": str(point_path),
            "point_status": result["status"],
            "live_point": live_point,
            "plan_fingerprint_sha256": plan_sha,
            "decision": decision,
            "outer_candidate_accepted": accepted,
            "observed_utility_improvement": observed_utility,
            "early_stop_after_candidate": early_stop,
            "failure_reason": None if live_point is not None else result.get("rejection_kind", result["status"]),
            "bad_configuration": (
                result["status"] in {"failed", "timeout", "oom"}
                or result.get("rejection_kind") == "correctness_rejected"
            ),
        }
        state["candidates"] = [*state["candidates"], record]
        state["pending_candidate"] = None
        _save_state(run_root, state)
    accepted_points = [
        EligibleFinalPoint(
            f"candidate_{row['candidate_index']}",
            int(row["candidate_index"]),
            LivePoint(**row["live_point"]),
        )
        for row in state["candidates"]
        if row["outer_candidate_accepted"]
    ]
    selection = select_final_point(
        SHARED_FROZEN / "acceptance_policy.json",
        LivePoint(**state["baseline_point"]["live_point"]),
        accepted_points,
        bottleneck=spec.scenario,
        xp=spec.benchmark == "hybench_xp",
    )
    if selection.candidate_index == 0:
        final_params = _default_params()
    else:
        match = next(
            row for row in state["candidates"]
            if int(row["candidate_index"]) == selection.candidate_index
        )
        final_params = match["params_replayed"]
    state["final_selection"] = {**asdict(selection), "outer_stop_reason": stop_reason}
    state["final_params"] = final_params
    state["stage"] = "default_validation"
    _save_state(run_root, state)


def _validation_replays(
    run_root: Path,
    state: dict[str, Any],
    spec: RecostRunSpec,
    *,
    final: bool,
) -> None:
    key = "final_validations" if final else "default_validations"
    prefix = "final_validation" if final else "default_validation"
    params = state["final_params"] if final else _default_params()
    completed = list(state[key])
    for replay in range(len(completed) + 1, 4):
        result, path = _execute_point(
            run_root,
            state,
            spec,
            f"{prefix}_{replay}",
            params,
            state["correctness_signature"],
        )
        if result["status"] != "complete":
            _block(run_root, state, f"{prefix}_failed", {
                "replay": replay, "path": str(path), "status": result["status"]
            })
        completed.append({"replay": replay, "path": str(path), "live_point": result["live_point"]})
        state[key] = completed
        _save_state(run_root, state)
    state["stage"] = "final_validation" if not final else "finalizing"
    _save_state(run_root, state)


def _metric_statistics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return rw._metric_statistics(rows)


def _finalize(run_root: Path, state: dict[str, Any], spec: RecostRunSpec) -> Path:
    default_rows = [item["live_point"] for item in state["default_validations"]]
    final_rows = [item["live_point"] for item in state["final_validations"]]
    point_results = [
        *run_root.glob("round_*/attempt_*/point_result.json"),
        *run_root.glob("validation/*/attempt_*/point_result.json"),
    ]
    cleanup_failures = [
        str(path) for path in point_results
        if load_json(path).get("cleanup_complete") is not True
    ]
    if cleanup_failures:
        raise RunBlocked(f"point cleanup incomplete: {cleanup_failures}")
    candidates = state["candidates"]
    inner_total = sum(int(row["proposal"]["inner_iterations"]) for row in candidates)
    inner_accepted = sum(int(row["proposal"]["inner_accepted_iterations"]) for row in candidates)
    plan_changed = sum(
        1 for row in candidates
        if isinstance(row.get("decision"), Mapping)
        and row["decision"].get("plan_changed") is True
    )
    bad = sum(bool(row["bad_configuration"]) for row in candidates)
    summary = {
        "schema_version": 1,
        "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID,
        "engine": ENGINE,
        "method": METHOD,
        "session_id": SESSION_ID,
        "status": "complete",
        "schedule": spec.as_dict(),
        "attempt_id": state["attempt_id"],
        "completed_at_utc": utc_now(),
        "baseline_gate": state["baseline_gate"],
        "recost_contract": state["static_validation"]["recost_settings"],
        "method_manifest_sha256": sha256_file(run_root / "method_manifest.json"),
        "estimator_training_scope": "current_engine_case_run_repeat_only",
        "estimator_implementation_sha256": state["static_validation"]["recost_settings"]["estimator_implementation_sha256"],
        "candidates": candidates,
        "inner_loop_summary": {
            "total_iterations": inner_total,
            "accepted_iterations": inner_accepted,
            "iteration_logs": [row["inner_iterations_path"] for row in candidates],
        },
        "final_selection": state["final_selection"],
        "final_params": state["final_params"],
        "default_validation_statistics": _metric_statistics(default_rows),
        "final_validation_statistics": _metric_statistics(final_rows),
        "default_validation_points": state["default_validations"],
        "final_validation_points": state["final_validations"],
        "consumed_candidate_budget": len(candidates),
        "real_candidate_replay_count": len(candidates),
        "accepted_candidate_count": sum(bool(row["outer_candidate_accepted"]) for row in candidates),
        "bad_configuration_count": bad,
        "bad_configuration_rate": bad / max(1, len(candidates)),
        "plan_changed_candidate_count": plan_changed,
        "plan_change_rate": plan_changed / max(1, len(candidates)),
        "real_replay_count": len(point_results),
        "point_wall_time_seconds": sum(
            float(load_json(path).get("wall_time_seconds", 0.0)) for path in point_results
        ),
        "negative_and_failed_results": [
            row for row in candidates if not row["outer_candidate_accepted"]
        ],
        "bootstrap_resamples": 10_000,
    }
    atomic_write_json(run_root / "run_summary.json", summary, replace=False)
    state["status"] = "complete"
    state["stage"] = "complete"
    state["completed_at_utc"] = utc_now()
    _save_state(run_root, state)
    fsync_artifact_tree(run_root)
    checksum = write_checksum_manifest(run_root)
    manifest = {
        "schema_version": 1,
        "status": "complete",
        "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID,
        "engine": ENGINE,
        "method": METHOD,
        "session_id": SESSION_ID,
        "run_id": spec.run_id,
        "repeat_id": spec.repeat_id,
        "point_schedule_sha256": sha256_file(SHARED_FROZEN / "point_schedule.json"),
        "attempt_id": state["attempt_id"],
        "cleanup_complete": True,
        "checksums_complete": checksum["validation"]["status"] == "passed",
        "run_summary_relative_path": "run_summary.json",
        "run_summary_sha256": sha256_file(run_root / "run_summary.json"),
        "checksums_relative_path": "checksums.sha256",
        "checksums_sha256": sha256_file(run_root / "checksums.sha256"),
        "completed_at_utc": utc_now(),
    }
    result = run_root / "result_manifest.json"
    atomic_write_json(result, manifest, replace=False)
    fsync_artifact_tree(run_root)
    validate_checksum_manifest(run_root)
    return result


def execute_claimed_run(
    spec: RecostRunSpec, attempt_id: str, validation: Mapping[str, Any]
) -> Path:
    run_root = _run_root(spec)
    state = (
        _load_state(run_root, spec, attempt_id)
        if _state_path(run_root).is_file()
        else _new_state(run_root, spec, attempt_id, validation)
    )
    if not (run_root / "run_environment.json").is_file():
        rw._environment_snapshot(run_root, spec)
    os.environ["FORMAL_MYSQL_RECOST_LOCK_HELD"] = attempt_id
    try:
        _initialize_baseline(run_root, state, spec)
        if state["stage"] == "candidates":
            _search_candidates(run_root, state, spec)
        if state["stage"] == "default_validation":
            _validation_replays(run_root, state, spec, final=False)
        if state["stage"] == "final_validation":
            _validation_replays(run_root, state, spec, final=True)
        if state["stage"] != "finalizing":
            raise SessionError(f"unexpected durable stage: {state['stage']}")
        return _finalize(run_root, state, spec)
    except RunBlocked as error:
        current = load_json(_state_path(run_root))
        if isinstance(current, dict) and current.get("status") == "running":
            current["status"] = "blocked"
            current["blocker"] = {
                "reason": "run_blocked_exception",
                "evidence": str(error),
                "recorded_at_utc": utc_now(),
            }
            _save_state(run_root, current)
        raise
    finally:
        os.environ.pop("FORMAL_MYSQL_RECOST_LOCK_HELD", None)


def _attempt_id(spec: RecostRunSpec) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"mysql_recost_{spec.run_id}_r{spec.repeat_id}_{stamp}_{secrets.token_hex(6)}"


def run_next() -> dict[str, Any]:
    static = static_validation()
    attempt = f"mysql_recost_preclaim_{secrets.token_hex(8)}"
    try:
        with RecostScheduleLease(attempt) as lease:
            if lease.spec is None:
                raise SessionError("scheduler lease has no frozen Recost spec")
            scheduler_attempt = lease.scheduler_attempt_id
            if not lease.resumed:
                # The scheduler claim has already persisted the preclaim id. It
                # is the immutable whole-Run attempt id from this point onward.
                scheduler_attempt = attempt
            validation = {"runner_source_sha256": static["runner_source_sha256"], **static}
            manifest = execute_claimed_run(lease.spec, scheduler_attempt, validation)
            lease.complete(manifest)
            return {
                "status": "complete",
                "spec": lease.spec.as_dict(),
                "attempt_id": scheduler_attempt,
                "result_manifest": str(manifest),
            }
    except SchedulerNotOurTurn:
        raise


def status() -> dict[str, Any]:
    static = static_validation()
    all_ready_present = all(
        (SHARED_ROOT / name).is_file()
        for name in ("SHARED_READY.json", "PG_ENGINE_READY.json", "MYSQL_ENGINE_READY.json")
    )
    if all_ready_present:
        state = validate_scheduler_state()
    else:
        state = load_json(SHARED_ROOT / "scheduler_state.json")
        expected = {
            "schema_version", "freeze_id", "point_schedule_sha256", "next_ordinal",
            "active", "completed_entry_count", "completed_entries",
        }
        if not isinstance(state, dict) or set(state) != expected:
            raise SessionError("pre-READY scheduler state has invalid top-level shape")
        if state["freeze_id"] != FREEZE_ID:
            raise SessionError("pre-READY scheduler freeze id mismatch")
        if state["point_schedule_sha256"] != sha256_file(SHARED_FROZEN / "point_schedule.json"):
            raise SessionError("pre-READY scheduler schedule SHA mismatch")
    completed = [
        row for row in state["completed_entries"] if row["session_id"] == SESSION_ID
    ]
    ready_files = {
        name: (SHARED_ROOT / name).is_file()
        for name in ("SHARED_READY.json", "PG_ENGINE_READY.json", "MYSQL_ENGINE_READY.json")
    }
    return {
        "status": "complete" if len(completed) == 14 else "waiting",
        "static_validation": static,
        "ready_files": ready_files,
        "scheduler_next_ordinal": state["next_ordinal"],
        "scheduler_active": state["active"],
        "session_completed_count": len(completed),
        "session_target_count": 14,
    }


def _worker_event(event: str, **details: Any) -> None:
    METHOD_ROOT.mkdir(parents=True, exist_ok=True)
    append_jsonl(
        METHOD_ROOT / "worker_events.jsonl",
        {"schema_version": 1, "event": event, "at_utc": utc_now(), **details},
    )


def worker_loop(poll_seconds: int = 30) -> int:
    """Persistently consume only this Session's frozen scheduler entries."""

    if poll_seconds < 5 or poll_seconds > 300:
        raise SessionError("worker poll interval must be within 5..300 seconds")
    METHOD_ROOT.mkdir(parents=True, exist_ok=True)
    lock_path = METHOD_ROOT / "worker_process.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SessionError("another MySQL Recost worker-loop already owns the session lock") from error
        startup_sources = _source_hashes()
        atomic_write_json(
            METHOD_ROOT / "worker_process.json",
            {
                "schema_version": 1,
                "status": "running",
                "pid": os.getpid(),
                "session_id": SESSION_ID,
                "method": METHOD,
                "poll_seconds": poll_seconds,
                "started_at_utc": utc_now(),
                "runner_source_sha256": startup_sources,
            },
        )
        _worker_event("worker_started", pid=os.getpid(), poll_seconds=poll_seconds)
        last_wait: tuple[Any, ...] | None = None
        while True:
            if _source_hashes() != startup_sources:
                raise RunBlocked("worker implementation changed after worker-loop startup")
            static_validation()
            ready_names = ("SHARED_READY.json", "PG_ENGINE_READY.json", "MYSQL_ENGINE_READY.json")
            present = tuple((SHARED_ROOT / name).is_file() for name in ready_names)
            if not all(present):
                marker = ("waiting_ready", present)
                if marker != last_wait:
                    _worker_event(
                        "waiting_ready",
                        ready_files={name: value for name, value in zip(ready_names, present)},
                    )
                    last_wait = marker
                time.sleep(poll_seconds)
                continue
            ready_validation()
            scheduler = validate_scheduler_state()
            completed = [
                row for row in scheduler["completed_entries"]
                if row["session_id"] == SESSION_ID
            ]
            if len(completed) == 14:
                _worker_event("worker_complete", completed_count=14)
                atomic_write_json(
                    METHOD_ROOT / "worker_process.json",
                    {
                        "schema_version": 1,
                        "status": "complete",
                        "pid": os.getpid(),
                        "session_id": SESSION_ID,
                        "method": METHOD,
                        "completed_count": 14,
                        "completed_at_utc": utc_now(),
                        "runner_source_sha256": startup_sources,
                    },
                )
                return 0
            active = scheduler["active"]
            if active is not None:
                expected_session = active["session_id"]
                ordinal = active["ordinal"]
            elif int(scheduler["next_ordinal"]) <= 84:
                schedule = _config("point_schedule.json")
                entry = schedule["entries"][int(scheduler["next_ordinal"]) - 1]
                expected_session = entry["session_id"]
                ordinal = entry["ordinal"]
            else:
                raise RunBlocked(
                    f"global schedule ended with only {len(completed)}/14 MySQL Recost Runs"
                )
            if expected_session != SESSION_ID:
                marker = ("waiting_ordinal", ordinal, expected_session, len(completed))
                if marker != last_wait:
                    _worker_event(
                        "waiting_ordinal",
                        next_ordinal=ordinal,
                        expected_session_id=expected_session,
                        completed_count=len(completed),
                    )
                    last_wait = marker
                time.sleep(poll_seconds)
                continue
            _worker_event(
                "starting_scheduled_run",
                ordinal=ordinal,
                resumed=active is not None,
                completed_count=len(completed),
            )
            try:
                result = run_next()
            except SchedulerNotOurTurn:
                time.sleep(poll_seconds)
                continue
            _worker_event(
                "scheduled_run_complete",
                ordinal=ordinal,
                result_manifest=result["result_manifest"],
            )
            last_wait = None
    except BaseException as error:
        _worker_event(
            "worker_stopped_on_error",
            error_type=type(error).__name__,
            error=str(error),
        )
        atomic_write_json(
            METHOD_ROOT / "worker_process.json",
            {
                "schema_version": 1,
                "status": "blocked" if isinstance(error, RunBlocked) else "error",
                "pid": os.getpid(),
                "session_id": SESSION_ID,
                "method": METHOD,
                "error_type": type(error).__name__,
                "error": str(error),
                "stopped_at_utc": utc_now(),
            },
        )
        return 2 if isinstance(error, RunBlocked) else 1
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "validate-ready", "run-next", "worker-loop"))
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()
    try:
        if args.command == "status":
            value = status()
        elif args.command == "validate-ready":
            value = {"static": static_validation(), "ready": ready_validation()}
        elif args.command == "run-next":
            value = run_next()
        else:
            return worker_loop(args.poll_seconds)
    except SchedulerNotOurTurn as error:
        print(json.dumps({"status": "not_our_turn", "error": str(error)}, indent=2))
        return 75
    except RunBlocked as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, indent=2))
        return 2
    except BaseException as error:
        print(json.dumps({
            "status": "error",
            "error_type": type(error).__name__,
            "error": str(error),
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
