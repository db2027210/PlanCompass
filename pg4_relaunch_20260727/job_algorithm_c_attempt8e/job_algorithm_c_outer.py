#!/usr/bin/env python3
"""Run the sealed JOB/Memory Algorithm C outer protocol."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

import formal_cgroup_v1_sampler
import job_fresh_container
import job_session_local_runner


PARAMETER_APPLICATION_SEMANTICS = (
    "SET LOCAL per_candidate per_worker immediate_readback "
    "readback_before_workload rollback_after_candidate rollback_after_query"
)
NON_TARGET_LIMIT = 0.80


class OuterError(RuntimeError):
    """The sealed Algorithm C outer protocol cannot continue."""


def load_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OuterError(f"cannot load bound JSON: {path}") from exc
    if not isinstance(value, dict):
        raise OuterError(f"JSON root must be an object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise OuterError(f"cannot hash bound artifact: {path}") from exc
    return digest.hexdigest()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    content = (json.dumps(dict(value), indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))


def _canonical_candidate(params: Mapping[str, float]) -> str:
    return hashlib.sha256(
        json.dumps(dict(params), sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def _r0_incumbent(config: Mapping[str, Any]) -> Dict[str, Any]:
    protocol = config["formal_outer_protocol"]
    summary = load_json(Path(protocol["r0_summary_path"]))
    profiles = summary.get("profiles")
    if not isinstance(profiles, list) or len(profiles) != 3:
        raise OuterError("strict R0 must contain exactly three profiles")
    return {
        "params": copy.deepcopy(config["initial_params"]),
        "qps": statistics.fmean(float(row["db_qps"]) for row in profiles),
        "resources": {
            name: statistics.fmean(
                float(row["resource_utilization"][name]) for row in profiles
            )
            for name in ("cpu", "memory", "io")
        },
        "source": "strict_r0_three_repeats_mean",
    }


def validate_runtime(config: Mapping[str, Any]) -> Dict[str, Any]:
    protocol = config.get("formal_outer_protocol")
    binding = config.get("_job_attempt8e_binding")
    algorithm = config.get("_algorithm_c_ratio_resource_only")
    semantics = config.get("parameter_application_contract")
    if not all(isinstance(value, dict) for value in (protocol, binding, algorithm, semantics)):
        raise OuterError("sealed outer contracts are missing")
    if semantics.get("method") != "SET_LOCAL":
        raise OuterError("formal parameter application method drift")
    if (
        semantics.get("immediate_readback_required") is not True
        or semantics.get("alter_system_prohibited") is not True
        or semantics.get("readback_scope") != "every_parameter_every_candidate_every_worker"
    ):
        raise OuterError("formal session-local readback contract drift")
    legacy_keys = {
        "dataset_path", "dataset_sha256", "ranker_path",
        "ranker_sha256", "ranker_required",
    }
    if legacy_keys.intersection(protocol):
        raise OuterError("legacy dataset/ranker formal dependency is prohibited")
    expected = {
        "r0_summary_path": "r0_summary_sha256",
        "experience_table_path": "experience_table_sha256",
    }
    for path_key, sha_key in expected.items():
        path = Path(str(protocol.get(path_key) or "")).resolve()
        if sha256_file(path) != protocol.get(sha_key):
            raise OuterError(f"bound artifact SHA drift: {path_key}")
        if binding.get(sha_key) != protocol.get(sha_key):
            raise OuterError(f"attempt binding drift: {sha_key}")
    if algorithm.get("resource_only_inner_ranking") is not True:
        raise OuterError("resource-only inner ranking is not enabled")
    if algorithm.get("inner_qps_ranking_prohibited") is not True:
        raise OuterError("inner QPS ranking prohibition drift")
    if int(algorithm.get("real_replay_max_per_outer_round") or 0) != 5:
        raise OuterError("formal outer round must be capped at five real points")
    if (
        int(algorithm.get("accepted_count_start") or 0) != 0
        or int(algorithm.get("accepted_count_target") or 0) != 10
    ):
        raise OuterError("formal outer accepted-count contract must be 0 to 10")
    ranges = config.get("param_ranges")
    params = config.get("initial_params")
    if not isinstance(ranges, dict) or not isinstance(params, dict) or len(ranges) != 7:
        raise OuterError("seven-parameter contract is invalid")
    optimizer = config.get("optimizer")
    active_params = (
        optimizer.get("active_params")
        if isinstance(optimizer, dict)
        else list(params)
    )
    if (
        not isinstance(active_params, list)
        or len(active_params) != 7
        or set(active_params) != set(ranges)
    ):
        raise OuterError("canonical active parameter order is invalid")
    job_session_local_runner.validate_params(params, ranges)
    experience_table = load_json(Path(protocol["experience_table_path"]))
    validate_experience_table(experience_table, active_params)
    return experience_table


def validate_experience_table(
    artifact: Mapping[str, Any],
    active_params: Sequence[str],
) -> None:
    if int(artifact.get("schema_version") or 0) != 2:
        raise OuterError("experience table schema mismatch")
    if artifact.get("benchmark") != "job":
        raise OuterError("experience table benchmark is not job")
    if artifact.get("target_resource") != "memory":
        raise OuterError("experience table target is not memory")
    if list(artifact.get("active_params") or []) != list(active_params):
        raise OuterError("experience table active_params mismatch")
    threshold = float(artifact.get("low_confidence_freeze_threshold"))
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise OuterError("experience table freeze threshold is invalid")
    table = artifact.get("table")
    if not isinstance(table, dict) or set(table) != set(active_params):
        raise OuterError("experience table seven-parameter coverage mismatch")
    for name, row in table.items():
        if not isinstance(row, dict):
            raise OuterError(f"experience table row is invalid: {name}")
        memory = float(row.get("memory"))
        confidence = float(row.get("confidence"))
        if not math.isfinite(memory) or not math.isfinite(confidence):
            raise OuterError(f"experience table direction evidence is non-finite: {name}")
        if not 0.0 <= confidence <= 1.0:
            raise OuterError(f"experience table confidence out of range: {name}")
    canonical = json.dumps(
        table, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != artifact.get("table_sha256"):
        raise OuterError("experience table canonical SHA mismatch")


def _direction_prior(
    experience_table: Mapping[str, Any],
    name: str,
    direction: str,
) -> Dict[str, Any]:
    row = experience_table["table"][name]
    confidence = float(row["confidence"])
    threshold = float(experience_table["low_confidence_freeze_threshold"])
    frozen = confidence < threshold
    increasing_memory_effect = float(row["memory"])
    signed_effect = increasing_memory_effect if direction == "up" else -increasing_memory_effect
    return {
        "prior_active": not frozen,
        "memory_direction_effect": 0.0 if frozen else signed_effect,
        "confidence": 0.0 if frozen else confidence,
        "raw_confidence": confidence,
        "freeze_threshold": threshold,
    }


def generate_candidates(
    incumbent: Mapping[str, Any],
    ranges: Mapping[str, Any],
    experience_table: Mapping[str, Any],
    evaluated: Sequence[str],
) -> List[Dict[str, Any]]:
    seen = set(evaluated)
    candidates: List[Dict[str, Any]] = []
    current = incumbent["params"]
    for name, spec in ranges.items():
        lower = float(spec["min_value"])
        upper = float(spec["max_value"])
        value = float(current[name])
        transform = str(spec.get("transform") or "linear")
        for direction, boundary in (("down", lower), ("up", upper)):
            if math.isclose(value, boundary, rel_tol=1e-12, abs_tol=1e-15):
                continue
            proposed = (
                math.sqrt(value * boundary)
                if transform == "log"
                else (value + boundary) / 2.0
            )
            params = {key: float(raw) for key, raw in current.items()}
            params[name] = proposed
            candidate_id = _canonical_candidate(params)
            if candidate_id in seen:
                continue
            prior = _direction_prior(experience_table, name, direction)
            candidates.append({
                "candidate_id": candidate_id,
                "params": params,
                "changed_parameter": name,
                "direction": direction,
                "resource_prior": prior,
            })
    candidates.sort(key=lambda row: (
        not row["resource_prior"]["prior_active"],
        float(row["resource_prior"]["memory_direction_effect"]),
        -float(row["resource_prior"]["confidence"]),
        list(ranges).index(row["changed_parameter"]),
        row["direction"],
    ))
    return candidates


def strict_outer_gate(result: Mapping[str, Any], incumbent: Mapping[str, Any]) -> Dict[str, Any]:
    measurement = result.get("measurement") or {}
    resources = measurement.get("resource_mean_utilization") or {}
    checks = {
        "point_complete": result.get("status") == "complete",
        "correctness": measurement.get("correctness_passed") is True,
        "stability": (measurement.get("resource_stability") or {}).get("passed") is True,
        "cleanup": (result.get("cleanup") or {}).get("cleanup_complete") is True,
        "global_defaults_unchanged": (
            (result.get("readback") or {}).get("global_defaults_unchanged") is True
        ),
        "qps_strictly_up": float(measurement.get("db_qps") or 0) > float(incumbent["qps"]),
        "memory_strictly_down": float(resources.get("memory") or math.inf)
        < float(incumbent["resources"]["memory"]),
        "cpu_below_80_pct": float(resources.get("cpu") or math.inf) < NON_TARGET_LIMIT,
        "io_below_80_pct": float(resources.get("io") or math.inf) < NON_TARGET_LIMIT,
    }
    return {"accepted": all(checks.values()), "checks": checks}


def _shrink(
    ranges: Mapping[str, Any],
    incumbent_params: Mapping[str, float],
    candidate: Mapping[str, Any],
) -> Dict[str, Any]:
    result = copy.deepcopy(dict(ranges))
    name = str(candidate["changed_parameter"])
    old = float(incumbent_params[name])
    if candidate["direction"] == "up":
        result[name]["min_value"] = old
    else:
        result[name]["max_value"] = old
    return result


def evaluate_real_candidate(
    config: Mapping[str, Any],
    candidate: Mapping[str, Any],
    point_dir: Path,
) -> Dict[str, Any]:
    candidate_config = copy.deepcopy(dict(config))
    candidate_config["initial_params"] = copy.deepcopy(candidate["params"])
    candidate_config["p_real"] = copy.deepcopy(candidate["params"])
    candidate_config["p_candidate"] = copy.deepcopy(candidate["params"])
    runner_config = candidate_config["job_session_runner"]
    runner_config["output_dir"] = str(point_dir)
    resource_path = point_dir / "measurement_resource_samples.jsonl"
    runner_config["resource_samples_path"] = str(resource_path)
    atomic_json(point_dir / "candidate_live_config.json", candidate_config)
    result: Optional[Dict[str, Any]] = None
    workload_error: Optional[BaseException] = None
    with job_fresh_container.fresh_container(point_dir) as lifecycle:
        sampler = formal_cgroup_v1_sampler.CgroupV1Sampler(
            candidate_config["_job_attempt8e_binding"]["profile"],
            runner_config["resource_sample_interval_seconds"],
        )
        sampler.start(resource_path)
        try:
            result = job_session_local_runner.run(candidate_config)
        except BaseException as exc:
            workload_error = exc
        try:
            sampler.stop()
        except BaseException as exc:
            raise OuterError(f"fresh cgroup collection failed: {exc}") from exc
        if result is not None:
            result["fresh_container"] = lifecycle
    fresh_cleanup = load_json(point_dir / "fresh_cleanup.json")
    if fresh_cleanup.get("cleanup_complete") is not True:
        raise OuterError("fresh candidate container cleanup did not complete")
    if result is not None:
        result["fresh_container"]["cleanup"] = fresh_cleanup
    if workload_error is not None:
        raise OuterError(f"session-local workload failed: {workload_error}") from workload_error
    if result is None:
        raise OuterError("session-local workload returned no result")
    return result


def run_outer(
    config: Mapping[str, Any],
    *,
    evaluator: Callable[[Mapping[str, Any], Mapping[str, Any], Path], Dict[str, Any]]
    = evaluate_real_candidate,
    max_real_points: Optional[int] = None,
) -> Dict[str, Any]:
    experience_table = validate_runtime(config)
    protocol = config["formal_outer_protocol"]
    output_root = Path(protocol["output_root"]).resolve() / "algorithm_c_outer"
    if output_root.exists():
        raise OuterError(f"refusing ambiguous outer resume: {output_root}")
    output_root.mkdir(parents=True)
    incumbent = _r0_incumbent(config)
    ranges = copy.deepcopy(config["param_ranges"])
    accepted_target = int(
        config["_algorithm_c_ratio_resource_only"]["accepted_count_target"]
    )
    max_per_round = int(
        config["_algorithm_c_ratio_resource_only"]["real_replay_max_per_outer_round"]
    )
    state: Dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "termination_reason": None,
        "accepted_count": 0,
        "accepted_target": accepted_target,
        "real_point_count": 0,
        "incumbent": incumbent,
        "ranges": ranges,
        "evaluated_candidate_ids": [],
        "rounds": [],
    }
    atomic_json(output_root / "outer_state.json", state)
    while state["accepted_count"] < accepted_target:
        candidates = generate_candidates(
            state["incumbent"],
            state["ranges"],
            experience_table,
            state["evaluated_candidate_ids"],
        )
        if not candidates:
            state["status"] = "basin_exhausted"
            state["termination_reason"] = "no_unevaluated_single_coordinate_candidates"
            break
        round_record: Dict[str, Any] = {
            "round_index": len(state["rounds"]) + 1,
            "incumbent_before": copy.deepcopy(state["incumbent"]),
            "ranges_before": copy.deepcopy(state["ranges"]),
            "candidates": [],
            "accepted_candidate_id": None,
        }
        for candidate in candidates[:max_per_round]:
            if max_real_points is not None and state["real_point_count"] >= max_real_points:
                state["status"] = "test_limit_reached"
                state["termination_reason"] = "injected_real_point_limit"
                break
            point_index = state["real_point_count"] + 1
            point_dir = output_root / f"point_{point_index:03d}_{candidate['candidate_id'][:12]}"
            point_dir.mkdir()
            atomic_json(point_dir / "candidate.json", candidate)
            try:
                result = evaluator(config, candidate, point_dir)
                gate = strict_outer_gate(result, state["incumbent"])
                error = None
            except BaseException as exc:
                error = f"{type(exc).__name__}: {exc}"
                atomic_json(point_dir / "evaluation_error.json", {
                    "status": "failed",
                    "error": error,
                })
                state["status"] = "failed"
                state["termination_reason"] = "candidate_evaluation_error"
                atomic_json(output_root / "outer_state.json", state)
                raise OuterError(f"candidate evaluation failed closed: {error}") from exc
            state["real_point_count"] += 1
            state["evaluated_candidate_ids"].append(candidate["candidate_id"])
            record = {
                **candidate,
                "point_dir": str(point_dir),
                "result": result,
                "gate": gate,
                "error": error,
            }
            round_record["candidates"].append(record)
            if gate["accepted"]:
                previous_params = state["incumbent"]["params"]
                measurement = result["measurement"]
                state["ranges"] = _shrink(state["ranges"], previous_params, candidate)
                state["incumbent"] = {
                    "params": copy.deepcopy(candidate["params"]),
                    "qps": float(measurement["db_qps"]),
                    "resources": copy.deepcopy(measurement["resource_mean_utilization"]),
                    "source": str(point_dir),
                }
                state["accepted_count"] += 1
                round_record["accepted_candidate_id"] = candidate["candidate_id"]
                break
            atomic_json(output_root / "outer_state.json", state)
        state["rounds"].append(round_record)
        atomic_json(output_root / "outer_state.json", state)
        if state["status"] == "test_limit_reached":
            break
        if round_record["accepted_candidate_id"] is None:
            remaining = generate_candidates(
                state["incumbent"],
                state["ranges"],
                experience_table,
                state["evaluated_candidate_ids"],
            )
            if not remaining:
                state["status"] = "basin_exhausted"
                state["termination_reason"] = "all_basin_candidates_rejected"
                break
    if state["accepted_count"] == accepted_target:
        state["status"] = "complete"
        state["termination_reason"] = "accepted_target_reached"
    atomic_json(output_root / "outer_state.json", state)
    atomic_json(output_root / "outer_result.json", state)
    return state


def main() -> int:
    config_path = Path(os.environ.get("OPTIMIZER_CONFIG", "")).resolve()
    if not config_path.is_file():
        print("FAIL_CLOSED: OPTIMIZER_CONFIG is missing", flush=True)
        return 42
    try:
        result = run_outer(load_json(config_path))
    except (OuterError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"FAIL_CLOSED: {exc}", flush=True)
        return 42
    print(json.dumps({
        "status": result["status"],
        "accepted_count": result["accepted_count"],
        "real_point_count": result["real_point_count"],
    }, sort_keys=True), flush=True)
    return 0 if result["status"] in ("complete", "basin_exhausted") else 42


if __name__ == "__main__":
    raise SystemExit(main())
