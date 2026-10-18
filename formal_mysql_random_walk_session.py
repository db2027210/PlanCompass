#!/usr/bin/env python3
"""Durable whole-Run orchestrator for formal MySQL random walk Session 5."""

from __future__ import annotations

from dataclasses import asdict
import argparse
import datetime as dt
import fcntl
import importlib.util
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

from formal_acceptance_policy import LivePoint
from formal_baseline_gates import PressureEvidence, evaluate_default_baseline
from formal_mysql_random_walk_point import run_point
from formal_mysql_random_walk_spec import (
    ENGINE,
    METHOD,
    SESSION_ID,
    SHARED_FROZEN,
    SessionRunSpec,
    load_session_schedule,
    validate_against_frozen_matrices,
    validate_authoritative_files,
)
from formal_random_walk import RandomWalkState
from formal_random_walk_artifacts import (
    append_jsonl,
    atomic_write_json,
    fsync_artifact_tree,
    sha256_file,
    validate_checksum_manifest,
    write_checksum_manifest,
)
from formal_random_walk_controller import FrozenRandomWalkController
from formal_statistics import summarize_replays
from formal_session_runtime_20260721 import (
    FREEZE_ID,
    GLOBAL_CAMPAIGN_ID,
    GLOBAL_ROOT,
    SHARED_ROOT,
    load_json,
    utc_now,
    validate_all_ready,
    validate_shared_bundle,
)


METHOD_ROOT = GLOBAL_ROOT / "mysql" / METHOD
PG_RUNTIME_PATH = Path(
    "/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated/formal_campaign_runtime.py"
)
RUNNER_SOURCES = (
    Path(__file__).resolve(),
    Path(__file__).resolve().parent / "formal_mysql_random_walk_point.py",
    Path(__file__).resolve().parent / "formal_mysql_random_walk_metrics.py",
    Path(__file__).resolve().parent / "formal_random_walk.py",
    Path(__file__).resolve().parent / "formal_random_walk_controller.py",
    Path(__file__).resolve().parent / "formal_acceptance_policy.py",
    Path(__file__).resolve().parent / "formal_stability.py",
    Path(__file__).resolve().parent / "formal_statistics.py",
    Path(__file__).resolve().parent / "formal_baseline_gates.py",
    Path(__file__).resolve().parent / "formal_random_walk_artifacts.py",
    Path(__file__).resolve().parent / "formal_mysql_campaign_20260721.py",
    Path(__file__).resolve().parent / "formal_mysql_engine_gate_20260721.py",
    Path(__file__).resolve().parent / "formal_resource_sampler_20260721.py",
    Path(__file__).resolve().parent / "formal_clients_20260721/com/formal/FormalSQLWorkload.java",
    Path(__file__).resolve().parent / "formal_clients_20260721/com/formal/TpchSeedAgent.java",
    Path(__file__).resolve().parent / "formal_clients_20260721/com/hybench/HyBenchFormalMain.java",
    Path(__file__).resolve().parent / "formal_clients_20260721/classes/com/formal/FormalSQLWorkload.class",
    Path(__file__).resolve().parent / "formal_clients_20260721/classes/com/formal/TpchSeedAgent.class",
    Path(__file__).resolve().parent / "formal_clients_20260721/classes/com/formal/TpchSeedAgent$Transformer.class",
    Path(__file__).resolve().parent / "formal_clients_20260721/classes/com/hybench/HyBenchFormalMain.class",
    Path(__file__).resolve().parent / "formal_clients_20260721/tpch-seed-agent.jar",
    Path(__file__).resolve().parent / "formal_random_walk_clients_20260721/formal/mysql/AttributingMysqlDriver.java",
    Path(__file__).resolve().parent / "formal_random_walk_clients_20260721/classes/formal/mysql/AttributingMysqlDriver.class",
)


class SessionError(RuntimeError):
    """Formal session invariant failed before a scheduler advance."""


class NotOurTurn(SessionError):
    """The frozen next ordinal belongs to another Session."""


class RunBlocked(SessionError):
    """A claimed whole Run reached a frozen blocking condition."""


def _pg_runtime() -> Any:
    spec = importlib.util.spec_from_file_location("formal_pg_campaign_runtime_v2", PG_RUNTIME_PATH)
    if spec is None or spec.loader is None:
        raise SessionError("cannot load the shared scheduler implementation")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(name: str) -> dict[str, Any]:
    value = load_json(SHARED_FROZEN / name)
    if not isinstance(value, dict):
        raise SessionError(f"frozen config must be an object: {name}")
    return value


def static_validation() -> dict[str, Any]:
    shared = validate_shared_bundle()
    authority = validate_authoritative_files()
    specs = load_session_schedule()
    validate_against_frozen_matrices(specs)
    sources = {str(path): sha256_file(path) for path in RUNNER_SOURCES}
    if len(sources) != len(RUNNER_SOURCES):
        raise SessionError("runner source set contains duplicates")
    return {
        "status": "passed",
        "shared": shared,
        "authority": authority,
        "target_count": len(specs),
        "target_ordinals": [spec.ordinal for spec in specs],
        "runner_source_sha256": sources,
        "pg_scheduler_runtime_sha256": sha256_file(PG_RUNTIME_PATH),
    }


def ready_validation() -> dict[str, Any]:
    local = validate_all_ready()
    pg = _pg_runtime().validate_measurement_preconditions()
    return {"status": "passed", "mysql_runtime": local, "scheduler_runtime": pg}


def _scheduler_validation() -> dict[str, Any]:
    """Validate mutable state without weakening the three-READY start gate."""

    return _pg_runtime().validate_scheduler_state()


def _spec_for_active(active: Mapping[str, Any]) -> SessionRunSpec:
    matches = [
        spec
        for spec in load_session_schedule()
        if spec.ordinal == int(active["ordinal"])
        and spec.run_id == active["run_id"]
        and spec.repeat_id == int(active["repeat_id"])
    ]
    if len(matches) != 1 or active["session_id"] != SESSION_ID:
        raise SessionError("active scheduler entry does not map to this frozen Session")
    return matches[0]


def _next_spec() -> SessionRunSpec:
    state = _scheduler_validation()["state"]
    active = state["active"]
    if active is not None:
        if active["session_id"] != SESSION_ID:
            raise NotOurTurn(
                f"ordinal {active['ordinal']} is active for {active['session_id']}"
            )
        return _spec_for_active(active)
    if int(state["next_ordinal"]) > 84:
        raise NotOurTurn("global frozen schedule is complete")
    matches = [spec for spec in load_session_schedule() if spec.ordinal == int(state["next_ordinal"])]
    if not matches:
        schedule = _config("point_schedule.json")
        entry = next(
            item for item in schedule["entries"] if int(item["ordinal"]) == int(state["next_ordinal"])
        )
        raise NotOurTurn(
            f"ordinal {entry['ordinal']} belongs to {entry['session_id']} ({entry['run_id']} r{entry['repeat_id']})"
        )
    return matches[0]


def _attempt_id(spec: SessionRunSpec) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"mysql_random_walk_{spec.run_id}_r{spec.repeat_id}_{stamp}_{secrets.token_hex(6)}"


def _run_root(spec: SessionRunSpec) -> Path:
    path = GLOBAL_ROOT / "mysql" / spec.output_relative_dir
    resolved_base = (GLOBAL_ROOT / "mysql" / METHOD).resolve(strict=False)
    resolved = path.resolve(strict=False)
    if resolved_base != resolved.parent:
        raise SessionError(f"unsafe Run output root: {path}")
    return path


def _state_path(run_root: Path) -> Path:
    return run_root / "run_state.json"


def _save_state(run_root: Path, state: Mapping[str, Any]) -> None:
    atomic_write_json(_state_path(run_root), state)


def _assert_source_pin(run_root: Path) -> None:
    state = load_json(_state_path(run_root))
    try:
        expected = state["static_validation"]["static"]["runner_source_sha256"]
    except (KeyError, TypeError) as error:
        raise SessionError("Run has no durable pre-result runner source pin") from error
    observed = {str(path): sha256_file(path) for path in RUNNER_SOURCES}
    if observed != expected:
        changed = {
            path: {"expected": expected.get(path), "observed": observed.get(path)}
            for path in set(expected) | set(observed)
            if expected.get(path) != observed.get(path)
        }
        raise RunBlocked(f"runner implementation changed after Run claim: {changed}")


def _new_state(
    run_root: Path,
    spec: SessionRunSpec,
    attempt_id: str,
    validations: Mapping[str, Any],
) -> dict[str, Any]:
    if run_root.exists() or run_root.is_symlink():
        raise SessionError(f"new claimed Run output already exists: {run_root}")
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
        "static_validation": validations,
        "baseline_point": None,
        "baseline_gate": None,
        "correctness_signature": None,
        "incumbent_plan_fingerprint": None,
        "controller": None,
        "final_selection": None,
        "final_params": None,
        "default_validations": [],
        "final_validations": [],
        "blocker": None,
    }
    _save_state(run_root, state)
    return state


def _load_state(run_root: Path, spec: SessionRunSpec, attempt_id: str) -> dict[str, Any]:
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
    for key, expected in fixed.items():
        if state.get(key) != expected:
            raise SessionError(f"resumed run_state.{key} mismatch")
    if state.get("schedule") != spec.as_dict():
        raise SessionError("resumed Run schedule mapping differs")
    if state.get("status") not in {"running", "blocked"}:
        raise SessionError(f"active scheduler entry has terminal Run state {state.get('status')}")
    if state.get("status") == "blocked":
        raise RunBlocked(str(state.get("blocker")))
    return state


def _cpu_disk_counters(device: str) -> dict[str, int]:
    cpu = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()[1:]
    values = [int(value) for value in cpu]
    total = sum(values)
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    major, minor = (int(value) for value in device.split(":"))
    io_time = None
    for line in Path("/proc/diskstats").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if int(fields[0]) == major and int(fields[1]) == minor:
            io_time = int(fields[12])
            break
    if io_time is None:
        raise SessionError(f"resolved database device is absent from diskstats: {device}")
    return {"cpu_total_ticks": total, "cpu_idle_ticks": idle, "device_io_time_ms": io_time}


def _resolved_mysql_device() -> str:
    import formal_mysql_campaign_20260721 as common

    machine = _config("machine_and_resources.json")
    device = common._resolve_mount_device(Path(machine["engines"]["mysql"]["mount_root"]))
    if not re_full_device(device):
        raise SessionError(f"invalid dynamically resolved MySQL device: {device}")
    return device


def re_full_device(value: str) -> bool:
    pieces = value.split(":")
    return len(pieces) == 2 and all(piece.isdigit() for piece in pieces)


def wait_host_admission(path: Path) -> dict[str, Any]:
    """Require two consecutive frozen 15-second CPU/disk-idle samples."""

    device = _resolved_mysql_device()
    started = time.monotonic()
    consecutive = 0
    sample_index = 0
    while time.monotonic() - started < 1800:
        before = _cpu_disk_counters(device)
        interval_started = time.monotonic()
        time.sleep(15)
        after = _cpu_disk_counters(device)
        seconds = time.monotonic() - interval_started
        total_delta = after["cpu_total_ticks"] - before["cpu_total_ticks"]
        idle_delta = after["cpu_idle_ticks"] - before["cpu_idle_ticks"]
        io_delta = after["device_io_time_ms"] - before["device_io_time_ms"]
        if total_delta <= 0 or idle_delta < 0 or io_delta < 0:
            raise SessionError("host admission counters regressed")
        cpu_busy = 1.0 - idle_delta / total_delta
        disk_util = io_delta / (seconds * 1000.0)
        passed = cpu_busy < 0.70 and disk_util < 0.70
        consecutive = consecutive + 1 if passed else 0
        sample_index += 1
        append_jsonl(
            path,
            {
                "sample_index": sample_index,
                "sample_interval_seconds": seconds,
                "requested_interval_seconds": 15,
                "device": device,
                "before": before,
                "after": after,
                "cpu_busy_ratio": cpu_busy,
                "disk_utilization_ratio": disk_util,
                "cpu_busy_limit_exclusive": 0.70,
                "disk_utilization_limit_exclusive": 0.70,
                "passed": passed,
                "consecutive_passes": consecutive,
                "at_utc": utc_now(),
            },
        )
        if consecutive >= 2:
            return {
                "status": "passed",
                "sample_count": sample_index,
                "consecutive_passes": consecutive,
                "device": device,
                "wall_seconds": time.monotonic() - started,
                "evidence_path": str(path),
            }
    raise RunBlocked("host admission did not pass twice within frozen 1800 seconds")


def _point_dir(run_root: Path, label: str, attempt: int) -> Path:
    return run_root / "points" / label / f"attempt_{attempt:02d}"


def _terminal_point(path: Path) -> dict[str, Any] | None:
    result = path / "point_result.json"
    checksums = path / "checksums.sha256"
    if result.is_file() and checksums.is_file():
        validate_checksum_manifest(path)
        value = load_json(result)
        if isinstance(value, dict) and value.get("status") in {
            "complete", "rejected", "failed", "timeout", "oom"
        }:
            return value
    return None


def execute_logical_point(
    run_root: Path,
    spec: SessionRunSpec,
    attempt_id: str,
    label: str,
    params: Mapping[str, float],
    correctness_signature: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    _assert_source_pin(run_root)
    parent = run_root / "points" / label
    attempt = 1
    while (parent / f"attempt_{attempt:02d}").exists():
        existing = _terminal_point(parent / f"attempt_{attempt:02d}")
        if existing is not None:
            return existing, str(parent / f"attempt_{attempt:02d}")
        interrupted = parent / f"attempt_{attempt:02d}"
        import formal_mysql_campaign_20260721 as common

        machine = _config("machine_and_resources.json")
        container_key = spec.container_key
        container = machine["engines"]["mysql"]["containers"][container_key]["name"]
        common._remove_container(container)
        working_root = Path(machine["engines"]["mysql"]["working_root"])
        working = working_root / "random_walk" / attempt_id / label / f"attempt_{attempt:02d}"
        common._cleanup_working(working, working_root)
        for ephemeral in (interrupted / "private", interrupted / "hybench_runtime"):
            if ephemeral.is_symlink():
                ephemeral.unlink()
            elif ephemeral.exists():
                shutil.rmtree(ephemeral)
        if spec.benchmark == "tpch":
            common._redact_benchbase_configs(interrupted)
        marker = interrupted / "interrupted_attempt.json"
        if not marker.exists():
            atomic_write_json(
                marker,
                {
                    "status": "interrupted",
                    "detected_at_utc": utc_now(),
                    "replacement_attempt": attempt + 1,
                    "candidate_budget_replacement": False,
                },
                replace=False,
            )
        fsync_artifact_tree(interrupted)
        write_checksum_manifest(interrupted)
        attempt += 1
    admission_path = run_root / "admission" / label / f"attempt_{attempt:02d}.jsonl"
    admission_path.parent.mkdir(parents=True, exist_ok=True)
    admission = wait_host_admission(admission_path)
    output = _point_dir(run_root, label, attempt)
    result = run_point(
        run_attempt_id=attempt_id,
        point_id=label,
        point_attempt=attempt,
        case_id=spec.case_id,
        params=params,
        output=output,
        expected_correctness=correctness_signature,
    )
    atomic_write_json(
        run_root / "admission" / label / f"attempt_{attempt:02d}_summary.json",
        admission,
        replace=False,
    )
    validate_checksum_manifest(output)
    return result, str(output)


def _block(run_root: Path, state: dict[str, Any], reason: str, evidence: Any) -> None:
    state["status"] = "blocked"
    state["blocker"] = {
        "reason": reason,
        "evidence": evidence,
        "recorded_at_utc": utc_now(),
    }
    _save_state(run_root, state)
    raise RunBlocked(f"{reason}: {evidence}")


def _default_params() -> dict[str, float]:
    values = _config("search_spaces.json")["mysql"]["default"]
    return {name: float(value) for name, value in values.items()}


def _controller(state: Mapping[str, Any]) -> FrozenRandomWalkController:
    return FrozenRandomWalkController.restore_frozen(
        state["controller"],
        policy_path=SHARED_FROZEN / "acceptance_policy.json",
        search_space=_config("search_spaces.json"),
        method_protocols=_config("method_protocols.json"),
    )


def _initialize_baseline(
    run_root: Path,
    state: dict[str, Any],
    spec: SessionRunSpec,
    attempt_id: str,
) -> None:
    if state["baseline_point"] is not None:
        return
    result, path = execute_logical_point(
        run_root,
        spec,
        attempt_id,
        "baseline_search",
        _default_params(),
        None,
    )
    if result["status"] != "complete":
        _block(run_root, state, "default_search_point_failed", {"path": path, "status": result["status"]})
    live = LivePoint(**result["live_point"])
    pressure = PressureEvidence(**result["pressure_decision_input"])
    gate = evaluate_default_baseline(
        SHARED_FROZEN / "measurement_protocol.json",
        live,
        pressure,
        scenario=spec.scenario,
        xp=spec.benchmark == "hybench_xp",
    )
    state["baseline_point"] = {"path": path, "live_point": asdict(live)}
    state["baseline_gate"] = asdict(gate)
    state["correctness_signature"] = result["correctness_signature"]
    state["incumbent_plan_fingerprint"] = result["plan_fingerprint_sha256"]
    if not gate.passed:
        _block(run_root, state, "default_baseline_gate_failed", asdict(gate))
    walk = RandomWalkState.from_frozen(
        _config("search_spaces.json"), _config("method_protocols.json")
    )
    controller = FrozenRandomWalkController(
        walk,
        policy_path=SHARED_FROZEN / "acceptance_policy.json",
        scenario=spec.scenario,
        xp=spec.benchmark == "hybench_xp",
        default_params=_default_params(),
        baseline=live,
    )
    state["controller"] = controller.as_dict()
    state["stage"] = "candidates"
    _save_state(run_root, state)


def _search_candidates(
    run_root: Path,
    state: dict[str, Any],
    spec: SessionRunSpec,
    attempt_id: str,
) -> None:
    controller = _controller(state)
    while not controller.should_stop:
        payload = controller.as_dict()
        pending = payload["pending_params"]
        if pending is None:
            params = dict(controller.propose())
            state["controller"] = controller.as_dict()
            _save_state(run_root, state)  # proposal and RNG state precede any live result
        else:
            params = {name: float(value) for name, value in pending.items()}
        index = controller.state.consumed_budget
        if controller.state.pending_duplicate:
            controller.record_duplicate()
            state["controller"] = controller.as_dict()
            _save_state(run_root, state)
            continue
        result, path = execute_logical_point(
            run_root,
            spec,
            attempt_id,
            f"candidate_{index}",
            params,
            state["correctness_signature"],
        )
        if result["status"] == "complete":
            plan_changed = (
                result["plan_fingerprint_sha256"] != state["incumbent_plan_fingerprint"]
            )
            record, decision = controller.record_complete(
                params_applied=result["params_applied"],
                point=LivePoint(**result["live_point"]),
                plan_changed=plan_changed,
            )
            if decision is not None and decision.accepted:
                state["incumbent_plan_fingerprint"] = result["plan_fingerprint_sha256"]
        else:
            outcome = result.get("rejection_kind") if result["status"] == "rejected" else result["status"]
            if outcome not in {"failed", "timeout", "oom", "correctness_rejected", "unstable"}:
                outcome = "failed"
            record = controller.record_failed(
                str(outcome),
                params_applied=result.get("params_applied"),
                reason=f"live_point_{result['status']}:{path}",
                bad_configuration=outcome in {"failed", "timeout", "oom", "correctness_rejected"},
            )
        state["last_candidate"] = {
            "candidate_index": index,
            "path": path,
            "point_status": result["status"],
            "controller_record": asdict(record),
        }
        state["controller"] = controller.as_dict()
        _save_state(run_root, state)
    selection, params = controller.select_final()
    state["controller"] = controller.as_dict()
    state["final_selection"] = asdict(selection)
    state["final_params"] = dict(params)
    state["stage"] = "default_validation"
    _save_state(run_root, state)


def _validation_replays(
    run_root: Path,
    state: dict[str, Any],
    spec: SessionRunSpec,
    attempt_id: str,
    *,
    final: bool,
) -> None:
    key = "final_validations" if final else "default_validations"
    prefix = "final_validation" if final else "default_validation"
    params = state["final_params"] if final else _default_params()
    completed = list(state[key])
    for replay in range(len(completed) + 1, 4):
        result, path = execute_logical_point(
            run_root,
            spec,
            attempt_id,
            f"{prefix}_{replay}",
            params,
            state["correctness_signature"],
        )
        if result["status"] != "complete":
            _block(
                run_root,
                state,
                f"{prefix}_failed",
                {"replay": replay, "path": path, "status": result["status"]},
            )
        completed.append({"replay": replay, "path": path, "live_point": result["live_point"]})
        state[key] = completed
        _save_state(run_root, state)
    state["stage"] = "final_validation" if not final else "finalizing"
    _save_state(run_root, state)


def _metric_statistics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(rows) != 3:
        raise SessionError("validation statistics require exactly three replays")
    seed = int(_config("measurement_protocol.json")["seeds"]["bootstrap_seed"])
    fields = (
        "latency_p95_ms", "latency_p99_ms", "cpu_utilization",
        "memory_utilization", "io_utilization", "error_rate",
    )
    result = {
        name: summarize_replays([float(row[name]) for row in rows], seed=seed).as_dict()
        for name in fields
    }
    if rows[0]["qps"] is None:
        result["xap_qps"] = summarize_replays(
            [float(row["xap_qps"]) for row in rows], seed=seed
        ).as_dict()
        result["xtp_tps"] = summarize_replays(
            [float(row["xtp_tps"]) for row in rows], seed=seed
        ).as_dict()
        result["xp_geometric_throughput"] = summarize_replays(
            [math.sqrt(float(row["xap_qps"]) * float(row["xtp_tps"])) for row in rows],
            seed=seed,
        ).as_dict()
    else:
        result["qps"] = summarize_replays(
            [float(row["qps"]) for row in rows], seed=seed
        ).as_dict()
    return result


def _command(argv: Sequence[str]) -> dict[str, Any]:
    proc = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return {"argv": list(argv), "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def _environment_snapshot(run_root: Path, spec: SessionRunSpec) -> dict[str, Any]:
    ready = load_json(SHARED_ROOT / "MYSQL_ENGINE_READY.json")
    env_path = GLOBAL_ROOT / str(ready["environment_readback_relative_path"])
    plan_path = GLOBAL_ROOT / str(ready["plan_gate_manifest_relative_path"])
    import formal_mysql_campaign_20260721 as common

    benchmark_path = common.BENCHMARK_CONFIG_ROOT / spec.benchmark / "benchmark_config.json"
    payload = {
        "schema_version": 1,
        "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID,
        "engine": ENGINE,
        "run_id": spec.run_id,
        "repeat_id": spec.repeat_id,
        "captured_at_utc": utc_now(),
        "commands": {
            "lscpu": _command(["lscpu", "--json"]),
            "numactl": _command(["numactl", "--hardware"]),
            "free": _command(["free", "-b"]),
            "lsblk": _command(["lsblk", "-J", "-b", "-O"]),
            "findmnt": _command(["findmnt", "-J"]),
            "java": _command(["java", "-version"]),
            "docker_image": _command(["docker", "image", "inspect", "mysql:8.0"]),
        },
        "host_controls": {
            "governors": sorted(
                {
                    path.read_text(encoding="utf-8").strip()
                    for path in Path("/sys/devices/system/cpu").glob("cpu*/cpufreq/scaling_governor")
                }
            ),
            "boost": (
                Path("/sys/devices/system/cpu/cpufreq/boost").read_text(encoding="utf-8").strip()
                if Path("/sys/devices/system/cpu/cpufreq/boost").is_file()
                else None
            ),
            "transparent_hugepage": (
                Path("/sys/kernel/mm/transparent_hugepage/enabled").read_text(encoding="utf-8").strip()
            ),
        },
        "ready_evidence": {
            "mysql_engine_ready_path": str(SHARED_ROOT / "MYSQL_ENGINE_READY.json"),
            "mysql_engine_ready_sha256": sha256_file(SHARED_ROOT / "MYSQL_ENGINE_READY.json"),
            "environment_readback_path": str(env_path),
            "environment_readback_sha256": sha256_file(env_path),
            "plan_gate_path": str(plan_path),
            "plan_gate_sha256": sha256_file(plan_path),
        },
        "artifacts": {
            "benchmark_config_path": str(benchmark_path),
            "benchmark_config_sha256": sha256_file(benchmark_path),
            "mysql_connector_path": str(common.MYSQL_CONNECTOR),
            "mysql_connector_sha256": sha256_file(common.MYSQL_CONNECTOR),
            "runner_sources": {str(path): sha256_file(path) for path in RUNNER_SOURCES},
        },
    }
    path = run_root / "run_environment.json"
    atomic_write_json(path, payload, replace=False)
    return payload


def _finalize(run_root: Path, state: dict[str, Any], spec: SessionRunSpec) -> Path:
    controller = _controller(state)
    default_rows = [item["live_point"] for item in state["default_validations"]]
    final_rows = [item["live_point"] for item in state["final_validations"]]
    records = [asdict(record) for record in controller.records]
    bad = sum(1 for record in controller.records if record.bad_configuration)
    real_candidates = sum(1 for record in controller.records if record.outcome != "duplicate")
    changed = sum(
        1 for record in controller.records
        if isinstance(record.decision, Mapping) and record.decision.get("plan_changed") is True
    )
    point_results = list(run_root.glob("points/*/attempt_*/point_result.json"))
    cleanup_failures = [
        str(path)
        for path in point_results
        if load_json(path).get("cleanup_complete") is not True
    ]
    if cleanup_failures:
        raise RunBlocked(f"point cleanup is incomplete: {cleanup_failures}")
    point_wall = sum(float(load_json(path).get("wall_time_seconds", 0.0)) for path in point_results)
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
        "random_walk": controller.as_dict(),
        "final_selection": state["final_selection"],
        "final_params": state["final_params"],
        "default_validation_statistics": _metric_statistics(default_rows),
        "final_validation_statistics": _metric_statistics(final_rows),
        "default_validation_points": state["default_validations"],
        "final_validation_points": state["final_validations"],
        "bad_configuration_count": bad,
        "bad_configuration_rate": bad / max(1, controller.state.consumed_budget),
        "consumed_candidate_budget": controller.state.consumed_budget,
        "real_candidate_replay_count": real_candidates,
        "duplicate_candidate_count": controller.state.consumed_budget - real_candidates,
        "plan_changed_candidate_count": changed,
        "plan_change_rate": changed / max(1, real_candidates),
        "real_replay_count": len(point_results),
        "point_wall_time_seconds": point_wall,
        "negative_and_failed_results": [
            record for record in records if record["outcome"] != "outer_accepted"
        ],
        "bootstrap_resamples": 10_000,
        "bootstrap_resamples_registration": "implementation choice recorded before first formal Run",
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
    spec: SessionRunSpec,
    attempt_id: str,
    validations: Mapping[str, Any],
) -> Path:
    run_root = _run_root(spec)
    if run_root.exists():
        state = _load_state(run_root, spec, attempt_id)
    else:
        state = _new_state(run_root, spec, attempt_id, validations)
    if not (run_root / "run_environment.json").is_file():
        _environment_snapshot(run_root, spec)
    os.environ["FORMAL_MYSQL_RANDOM_WALK_LOCK_HELD"] = attempt_id
    try:
        _initialize_baseline(run_root, state, spec, attempt_id)
        if state["stage"] == "candidates":
            _search_candidates(run_root, state, spec, attempt_id)
        if state["stage"] == "default_validation":
            _validation_replays(run_root, state, spec, attempt_id, final=False)
        if state["stage"] == "final_validation":
            _validation_replays(run_root, state, spec, attempt_id, final=True)
        if state["stage"] != "finalizing":
            raise SessionError(f"unexpected durable Run stage: {state['stage']}")
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
        os.environ.pop("FORMAL_MYSQL_RANDOM_WALK_LOCK_HELD", None)


def run_next() -> dict[str, Any]:
    static = static_validation()
    ready = ready_validation()
    schedule = _config("point_schedule.json")
    lock_path = Path(schedule["host_measurement_lock"])
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
    pg = _pg_runtime()
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        # Revalidate after lock acquisition: publication and ordinal may have
        # changed while this Session was waiting.
        static = static_validation()
        ready = ready_validation()
        spec = _next_spec()
        scheduler = _scheduler_validation()["state"]
        if scheduler["active"] is None:
            attempt_id = _attempt_id(spec)
            claim = pg.claim_scheduler_entry(SESSION_ID, attempt_id)
        else:
            attempt_id = str(scheduler["active"]["attempt_id"])
            claim = pg.claim_scheduler_entry(SESSION_ID, attempt_id)
        if claim["active"]["ordinal"] != spec.ordinal:
            raise SessionError("claimed scheduler ordinal differs from frozen Session mapping")
        manifest = execute_claimed_run(
            spec,
            attempt_id,
            {"static": static, "ready": ready, "claim": claim},
        )
        relative = manifest.relative_to(GLOBAL_ROOT / "mysql").as_posix()
        completion = pg.complete_scheduler_entry(SESSION_ID, attempt_id, relative)
        return {
            "status": "complete",
            "spec": spec.as_dict(),
            "attempt_id": attempt_id,
            "result_manifest": str(manifest),
            "scheduler_completion": completion,
        }
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def status() -> dict[str, Any]:
    static = static_validation()
    scheduler = _scheduler_validation()["state"]
    ready_files = {
        name: (SHARED_ROOT / name).is_file()
        for name in ("SHARED_READY.json", "PG_ENGINE_READY.json", "MYSQL_ENGINE_READY.json")
    }
    completed = [
        entry for entry in scheduler["completed_entries"] if entry["session_id"] == SESSION_ID
    ]
    return {
        "status": "waiting" if len(completed) < 14 else "complete",
        "static_validation": static,
        "ready_files": ready_files,
        "scheduler_next_ordinal": scheduler["next_ordinal"],
        "scheduler_active": scheduler["active"],
        "session_completed_count": len(completed),
        "session_target_count": 14,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "validate-ready", "run-next"))
    args = parser.parse_args()
    try:
        if args.command == "status":
            value = status()
        elif args.command == "validate-ready":
            value = {"static": static_validation(), "ready": ready_validation()}
        else:
            value = run_next()
    except NotOurTurn as error:
        print(json.dumps({"status": "not_our_turn", "error": str(error)}, ensure_ascii=False, indent=2))
        return 75
    except RunBlocked as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False, indent=2))
        return 2
    except BaseException as error:
        print(
            json.dumps(
                {"status": "error", "error_type": type(error).__name__, "error": str(error)},
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
