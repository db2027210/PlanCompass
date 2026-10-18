#!/usr/bin/env python3
"""Keep completed Algorithm C lanes in the four-lane replay execution domain.

The formal controllers synchronize every physical wave.  A lane that reaches
accepted=7 first must therefore keep contributing workload-only controls until
all four lanes have produced authority manifests.  These controls are written
to a separate audit tree and never mutate accepted, incumbent, ranges, gradient
state, or the completed lane's authoritative history.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import (  # noqa: E402
    apply_pg_params,
    pg_conn_from_worker,
    restore_worker_from_hybench_template,
    run_real_validation_safe,
    validation_workers,
    worker_with_prepared_restore,
)
from shell.run_pg_algorithm_c_scene import (  # noqa: E402
    apply_ssd_wave_qualification,
    canonical_sha256,
    physical_valid,
    worker_key,
)


SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
STATE_FILE_NAME = "algorithm_c_state.json"
HISTORY_FILE_NAME = "algorithm_c.jsonl"
CONTROLLER_SCRIPT_NAME = "run_pg_algorithm_c_scene.py"
DEFAULT_BARRIER_TIMEOUT_S = 1800.0
DEFAULT_TERMINAL_WAIT_TIMEOUT_S = 86400.0


def validate_authoritative_completion_manifest(
    manifest: Mapping[str, Any],
    scene: str,
) -> Dict[str, Any]:
    """Validate a completed lane against the convergence contract it records."""
    accepted = int(manifest.get("accepted") or 0)
    accepted_target = int(manifest.get("accepted_target") or 0)
    normalized_width = float(manifest.get("normalized_width") or 1.0)
    normalized_width_threshold = float(
        manifest.get("normalized_width_threshold") or 0.01
    )
    consecutive_stable_rounds = int(
        manifest.get("consecutive_stable_rounds") or 0
    )
    required_consecutive_stable_rounds = int(
        manifest.get("required_consecutive_stable_rounds") or 0
    )
    post_convergence_stability_required = bool(
        manifest.get("post_convergence_stability_required", False)
    )
    repeatability = manifest.get("repeatability")
    repeatability = repeatability if isinstance(repeatability, Mapping) else {}
    errors: List[str] = []
    if accepted_target <= 0 or accepted != accepted_target:
        errors.append("accepted_target_not_reached")
    if normalized_width > normalized_width_threshold:
        errors.append("normalized_width_above_threshold")
    if consecutive_stable_rounds < required_consecutive_stable_rounds:
        errors.append("required_consecutive_stable_rounds_not_reached")
    if post_convergence_stability_required and not bool(repeatability.get("passed")):
        errors.append("required_repeatability_not_passed")
    if bool(manifest.get("manual_mutation_after_formal_start", False)):
        errors.append("manual_mutation_after_formal_start")
    receipt = {
        "contract": "algorithm_c_manifest_declared_convergence_v1",
        "scene": scene,
        "accepted": accepted,
        "accepted_target": accepted_target,
        "normalized_width": normalized_width,
        "normalized_width_threshold": normalized_width_threshold,
        "consecutive_stable_rounds": consecutive_stable_rounds,
        "required_consecutive_stable_rounds": required_consecutive_stable_rounds,
        "post_convergence_stability_required": (
            post_convergence_stability_required
        ),
        "repeatability_required": bool(repeatability.get("required", False)),
        "repeatability_passed": repeatability.get("passed"),
        "errors": errors,
        "passed": not errors,
    }
    if errors:
        raise RuntimeError(
            f"lane manifest is not authoritative completion: {scene}: {errors}"
        )
    return receipt


def support_workload_environment(
    scene: str,
    campaign_root: Path,
) -> Dict[str, str]:
    """Return a lane-local workload environment without mutating os.environ."""
    common = {"GENERIC_RUN_SECONDS": "0"}
    if scene == "job_memory":
        return {
            **common,
            "GENERIC_WORKLOAD": "job",
            "JOB_WORKERS": "2",
            "JOB_MAX_CONCURRENT_LONG_QUERIES": "1",
            "JOB_MAX_CONCURRENT_LONG_QUERIES_PER_FAMILY": "1",
            "JOB_TARGET_WALL_TIME_S": "900",
            "JOB_FIXED_SCHEDULE_SHA256": (
                "950fde0959033c8da695fae91aedf56481008ddf5de4c2e781fb00b30fb36513"
            ),
        }
    if scene == "tpch_memory":
        return {
            **common,
            "GENERIC_WORKLOAD": "tpch",
            "BENCHBASE_SOURCE_ROOT": "/home/anonymous/benchbase-postgres",
            "BENCHBASE_RUNTIME_ROOT": str(campaign_root / "benchbase_runtime"),
            "BENCHBASE_SCALEFACTOR": "5",
            "BENCHBASE_TPCH_WORKERS": "1",
            "BENCHBASE_JAVA_XMX": "-Xmx2g",
            "TPCH_TARGET_WALL_TIME_S": "900",
        }
    if scene in {"xp_cpu", "xp_io"}:
        return {**common, "GENERIC_WORKLOAD": "hybenchxp"}
    raise ValueError(f"unknown support scene: {scene}")


def configure_shared_ssd_execution_domain(campaign_root: Path) -> Dict[str, Any]:
    """Make support workloads inherit the campaign's frozen SSD policy."""
    rows: List[Dict[str, Any]] = []
    for scene in SCENES:
        cfg_path = campaign_root / scene / "optimizer_config.json"
        cfg = read_json(cfg_path)
        algorithm_cfg = cfg.get("algorithm_c")
        algorithm_cfg = algorithm_cfg if isinstance(algorithm_cfg, Mapping) else {}
        workload_contract = algorithm_cfg.get("workload_contract")
        workload_contract = (
            workload_contract if isinstance(workload_contract, Mapping) else {}
        )
        policy = workload_contract.get("shared_ssd_coordination")
        policy = policy if isinstance(policy, Mapping) else {}
        no_mutex = bool(
            policy.get("enabled") is False
            and str(policy.get("physical_workload_mode") or "").lower() == "none"
            and str(policy.get("contract") or "")
            == "pg_algorithm_c_user_authorized_no_mutex_v1"
        )
        rows.append(
            {
                "scene": scene,
                "config": str(cfg_path),
                "contract": policy.get("contract"),
                "enabled": policy.get("enabled"),
                "physical_workload_mode": policy.get("physical_workload_mode"),
                "no_mutex": no_mutex,
            }
        )
    modes = {bool(row["no_mutex"]) for row in rows}
    if len(modes) != 1:
        raise RuntimeError(f"mixed shared SSD execution domains: {rows}")
    no_mutex = modes == {True}
    if no_mutex:
        os.environ["BONECT_SHARED_SSD_COORDINATION_ENABLED"] = "0"
        os.environ["BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE"] = "none"
    receipt = {
        "contract": "algorithm_c_support_shared_ssd_execution_domain_v1",
        "passed": True,
        "no_mutex": no_mutex,
        "coordination_enabled_env": os.environ.get(
            "BONECT_SHARED_SSD_COORDINATION_ENABLED"
        ),
        "workload_lock_mode_env": os.environ.get(
            "BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE"
        ),
        "scenes": rows,
    }
    if no_mutex and not (
        receipt["coordination_enabled_env"] == "0"
        and receipt["workload_lock_mode_env"] == "none"
    ):
        raise RuntimeError(f"failed to configure support no-mutex mode: {receipt}")
    return receipt


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def participant_key(scene: str, wave_sequence: int) -> str:
    return canonical_sha256({"scene": scene, "wave_sequence": int(wave_sequence)})


def barrier_dir(cohort_id: str, wave_sequence: int) -> Path:
    stage_key = canonical_sha256(
        {"cohort_id": cohort_id, "wave_sequence": int(wave_sequence)}
    )
    return Path("/tmp") / f"resource_adaptive_algorithm_c_candidate_cohort_{stage_key}"


CLEAN_TERMINAL_PHASES = frozenset(
    {
        "complete",
        "converged_on_incumbent_clean",
        "candidate_space_exhausted_without_accepted_target_clean",
        "nonconvergence_max_rounds_clean",
        "outer_pruning_candidate_space_exhausted_clean",
        "qps_progress_candidate_space_exhausted_clean",
        "all_champions_semantic_stability_failed_clean",
    }
)


def lane_clean_terminal(campaign_root: Path, scene: str) -> bool:
    state_path = campaign_root / scene / STATE_FILE_NAME
    if not state_path.is_file():
        return False
    try:
        state = read_json(state_path)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
        return False
    return str(state.get("phase") or "") in CLEAN_TERMINAL_PHASES


def campaign_clean_terminal(campaign_root: Path) -> bool:
    return all(lane_clean_terminal(campaign_root, scene) for scene in SCENES)


def wait_for_terminal_manifest(
    lane_root: Path,
    *,
    timeout_s: float,
) -> Dict[str, Any]:
    """Wait through the complete-state/authority-manifest commit interval."""
    manifest_path = lane_root / "authority_manifest.json"
    state_path = lane_root / STATE_FILE_NAME
    started = time.monotonic()
    while True:
        state = read_json(state_path) if state_path.is_file() else {}
        phase = str(state.get("phase") or "")
        if phase == "complete" and manifest_path.is_file():
            return state
        if phase in CLEAN_TERMINAL_PHASES and phase != "complete":
            # Incumbent convergence used to publish its clean phase before
            # the final baseline restore/readback completed.  A support lane
            # could therefore seize the same workers during that restore.
            # Treat this terminal as committed only when the controller has
            # durably attached the successful restore receipt.
            if phase == "converged_on_incumbent_clean":
                convergence = state.get("incumbent_convergence") or {}
                receipt = (
                    convergence.get("clean_restore_receipt")
                    if isinstance(convergence, Mapping)
                    else None
                )
                if not isinstance(receipt, Mapping) or receipt.get("passed") is not True:
                    if time.monotonic() - started >= timeout_s:
                        raise RuntimeError(
                            "support wait for incumbent convergence restore commit "
                            f"timed out: lane={lane_root.name} phase={phase}"
                        )
                    time.sleep(0.2)
                    continue
            return state
        if time.monotonic() - started >= timeout_s:
            raise RuntimeError(
                "support wait for terminal authority timed out: "
                f"lane={lane_root.name} phase={phase}"
            )
        time.sleep(0.2)


def next_campaign_wave_sequence(campaign_root: Path, cohort_id: str) -> int:
    """Join an in-flight furthest wave, otherwise start its successor."""
    sequences: List[int] = []
    for candidate_scene in SCENES:
        state_path = campaign_root / candidate_scene / STATE_FILE_NAME
        if not state_path.is_file():
            continue
        state = read_json(state_path)
        sequences.append(int(state.get("formal_candidate_wave_sequence") or 0))
    furthest = max(sequences or [0])
    if furthest > 0:
        stage_dir = barrier_dir(cohort_id, furthest)
        ready_rows = load_ready_rows(stage_dir, require_live_pid=True)
        done_rows = load_done_rows(stage_dir)
        # A stale, fully completed barrier is immutable historical evidence,
        # not an in-flight wave that a restarted support process may rejoin.
        if ready_rows and len(done_rows) < len(SCENES):
            return furthest
    return furthest + 1


def ready_row_has_live_pid(row: Mapping[str, Any]) -> bool:
    """Return whether a cohort receipt still belongs to a live participant."""
    try:
        pid = int(row.get("pid") or 0)
        if pid <= 0:
            return False
        os.kill(pid, 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def load_ready_rows(
    path: Path,
    *,
    require_live_pid: bool = False,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for ready_path in sorted(path.glob("*.ready")):
        try:
            row = read_json(ready_path)
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
            continue
        if str(row.get("scene") or "") not in SCENES:
            continue
        if require_live_pid and not ready_row_has_live_pid(row):
            continue
        rows.append(row)
    return rows


def load_done_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for done_path in sorted(path.glob("*.done")):
        try:
            row = read_json(done_path)
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
            continue
        if str(row.get("scene") or "") not in SCENES:
            continue
        rows.append(row)
    return rows


def select_support_physical_peer(
    campaign_root: Path,
    ready_rows: Sequence[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Select a formal peer only after every active formal lane is ready.

    Completed-lane support rows can arrive before a slower formal controller.
    They must never be used to infer the physical width of the next wave.  In
    particular, a mixed wave may contain an early empty-wave control from one
    formal lane and a later physical batch from another formal lane.  Waiting
    for all active formal lanes removes that race while preserving all-empty
    waves as width-zero controls.
    """
    active_formal_scenes = {
        scene for scene in SCENES if not lane_clean_terminal(campaign_root, scene)
    }
    if not active_formal_scenes:
        return None
    formal_rows = [
        dict(row)
        for row in ready_rows
        if str(row.get("scene") or "") in active_formal_scenes
        and str(row.get("batch_kind") or "") != "cohort_support_control"
    ]
    ready_formal_scenes = {
        str(row.get("scene") or "") for row in formal_rows
    }
    if not active_formal_scenes.issubset(ready_formal_scenes):
        return None
    return next(
        (
            row
            for row in formal_rows
            if str(row.get("batch_kind") or "")
            != "base_top5_empty_wave_control"
        ),
        formal_rows[0],
    )


def physical_batch_width(
    campaign_root: Path,
    peer: Mapping[str, Any],
) -> int:
    scene = str(peer["scene"])
    round_index = int(peer["round_index"])
    batch_kind = str(peer["batch_kind"])
    wave_start = int(peer["wave_start"])
    history_path = campaign_root / scene / HISTORY_FILE_NAME
    candidate_count: Optional[int] = None
    with history_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if (
                row.get("event") == "algorithm_c_physical_batch_start"
                and int(row.get("round_index") or -1) == round_index
                and str(row.get("batch_kind") or "") == batch_kind
            ):
                candidate_count = int(row.get("candidate_count") or 0)
    if str(peer.get("batch_kind") or "") == "base_top5_empty_wave_control":
        return 0
    if candidate_count is None or candidate_count <= wave_start:
        raise RuntimeError(
            "cannot infer active physical wave width: "
            f"scene={scene} round={round_index} batch={batch_kind} start={wave_start}"
        )
    return min(2, candidate_count - wave_start)


def verified_controller_pids(
    campaign_root: Path,
    ready_rows: Sequence[Mapping[str, Any]],
) -> List[int]:
    verified: List[int] = []
    for row in ready_rows:
        try:
            pid = int(row.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 1:
            continue
        cmdline_path = Path("/proc") / str(pid) / "cmdline"
        try:
            cmdline = cmdline_path.read_bytes().replace(b"\x00", b" ").decode(
                "utf-8", errors="replace"
            )
        except OSError:
            continue
        if (
            CONTROLLER_SCRIPT_NAME in cmdline
            and str(campaign_root) in cmdline
        ):
            verified.append(pid)
    return sorted(set(verified))


def stop_active_controllers(
    campaign_root: Path,
    ready_rows: Sequence[Mapping[str, Any]],
    log_path: Path,
    reason: str,
) -> None:
    pids = verified_controller_pids(campaign_root, ready_rows)
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    append_jsonl(
        log_path,
        {
            "event": "algorithm_c_cohort_support_failure_stop_requested",
            "time": time.time(),
            "reason": reason,
            "controller_pids": pids,
        },
    )


def restore_support_workers(
    cfg: Dict[str, Any],
    workers: Sequence[Dict[str, Any]],
    label_prefix: str,
) -> List[Dict[str, Any]]:
    restores: List[Optional[Dict[str, Any]]] = [None] * len(workers)
    with ThreadPoolExecutor(max_workers=len(workers)) as executor:
        futures = {
            executor.submit(
                restore_worker_from_hybench_template,
                cfg,
                worker,
                label=f"{label_prefix}_{worker_key(worker)}",
            ): index
            for index, worker in enumerate(workers)
        }
        for future in as_completed(futures):
            index = futures[future]
            restore = future.result()
            if restore.get("status") != "ok":
                raise RuntimeError(f"support restore failed: {restore}")
            restores[index] = restore
    return [dict(item) for item in restores if isinstance(item, Mapping)]


def run_support_wave(
    *,
    cfg: Dict[str, Any],
    scene: str,
    target: str,
    workers: Sequence[Dict[str, Any]],
    params: Dict[str, float],
    campaign_root: Path,
    cohort_id: str,
    cohort_size: int,
    wave_sequence: int,
    timeout_s: float,
    log_path: Path,
) -> bool:
    """Run one support wave, or yield if this lane becomes formally active.

    A clean lane may be reopened for a finer search interval after its support
    thread has started.  In that case the formal controller owns the lane and
    its workers again.  Yield before publishing this participant's ready row;
    the caller will wait for the next committed terminal and then rejoin the
    furthest incomplete campaign wave.
    """
    stage_dir = barrier_dir(cohort_id, wave_sequence)
    started = time.monotonic()
    peer_rows: List[Dict[str, Any]] = []
    peer: Optional[Dict[str, Any]] = None
    while not campaign_clean_terminal(campaign_root):
        if not lane_clean_terminal(campaign_root, scene):
            return False
        peer_rows = [
            row
            for row in load_ready_rows(stage_dir, require_live_pid=True)
            if str(row.get("scene") or "") != scene
        ]
        peer = select_support_physical_peer(campaign_root, peer_rows)
        if peer is not None:
            break
        if time.monotonic() - started >= timeout_s:
            raise RuntimeError(
                "support wait for all active formal peers timed out: "
                f"sequence={wave_sequence} ready={len(peer_rows)}"
            )
        time.sleep(0.2)
    if campaign_clean_terminal(campaign_root):
        return True
    if peer is None:
        raise RuntimeError(
            f"support physical peer unavailable: sequence={wave_sequence}"
        )
    if not lane_clean_terminal(campaign_root, scene):
        return False
    width = physical_batch_width(campaign_root, peer)
    round_index = int(peer["round_index"])
    label_prefix = f"algorithm_c_cohort_support_s{wave_sequence:04d}"
    workload_environment = support_workload_environment(scene, campaign_root)
    restores = (
        restore_support_workers(cfg, workers[:width], label_prefix)
        if width > 0
        else []
    )

    stage_dir.mkdir(parents=True, exist_ok=True)
    ready_path = stage_dir / f"{participant_key(scene, wave_sequence)}.ready"
    ready_payload = {
        "scene": scene,
        "wave_sequence": wave_sequence,
        "round_index": round_index,
        "batch_kind": "cohort_support_control",
        "wave_start": int(peer["wave_start"]),
        "support_wave_width": width,
        "support_for_batch_kind": str(peer["batch_kind"]),
        "role": "completed_lane_execution_domain_support",
        "workload_environment": workload_environment,
        "pid": os.getpid(),
        "time": time.time(),
    }
    ready_path.write_text(
        json.dumps(ready_payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    while True:
        ready_rows = load_ready_rows(stage_dir, require_live_pid=True)
        if len(ready_rows) >= cohort_size:
            break
        if time.monotonic() - started >= timeout_s:
            raise RuntimeError(
                f"support cohort barrier timed out: ready={len(ready_rows)}/{cohort_size} "
                f"sequence={wave_sequence}"
            )
        time.sleep(0.2)

    barrier_receipt = {
        "contract": "algorithm_c_completed_lane_cohort_support_v1",
        "passed": True,
        "cohort_id_sha256": canonical_sha256({"cohort_id": cohort_id}),
        "cohort_size": cohort_size,
        "ready_count": len(ready_rows),
        "wave_sequence": wave_sequence,
        "round_index": round_index,
        "support_wave_width": width,
        "barrier_stage_sha256": stage_dir.name.rsplit("_", 1)[-1],
    }
    append_jsonl(
        log_path,
        {
            "event": "algorithm_c_cohort_support_barrier",
            "time": time.time(),
            "scene": scene,
            "barrier": barrier_receipt,
            "ready_rows": ready_rows,
            "formal_state_mutation": False,
        },
    )

    results: List[Optional[Dict[str, Any]]] = [None] * width
    if width > 0:
        with ThreadPoolExecutor(max_workers=width) as executor:
            futures = {}
            for index in range(width):
                worker = workers[index]
                label = f"{label_prefix}_{worker_key(worker)}"
                prepared_worker = worker_with_prepared_restore(worker)
                future = executor.submit(
                    run_real_validation_safe,
                    cfg,
                    params,
                    output_dir=(
                        campaign_root
                        / "cohort_support"
                        / scene
                        / f"wave_{wave_sequence:04d}"
                    ),
                    label=label,
                    worker=prepared_worker,
                    force_new_replay=True,
                    environment_overrides=workload_environment,
                )
                futures[future] = index
            for future in as_completed(futures):
                index = futures[future]
                row = future.result()
                row["worker_key"] = worker_key(workers[index])
                row["restore"] = restores[index]
                row["candidate_cohort_barrier"] = barrier_receipt
                results[index] = row

    rows = [dict(row) for row in results if isinstance(row, Mapping)]
    qualification = (
        apply_ssd_wave_qualification(
            rows,
            target=target,
            phase="formal_completed_lane_cohort_support",
            cfg=cfg,
        )
        if width > 0
        else {
            "passed": True,
            "phase": "formal_completed_lane_empty_cohort_control",
            "control_only": True,
            "physical_workload_invocations": 0,
            "technical_failures": 0,
        }
    )
    valid = bool(
        len(rows) == width
        and all(physical_valid(row) for row in rows)
        and qualification.get("passed") is True
    )
    done_payload = {
        "scene": scene,
        "wave_sequence": wave_sequence,
        "round_index": round_index,
        "batch_kind": "cohort_support_control",
        "wave_start": int(peer["wave_start"]),
        "pid": os.getpid(),
        "time": time.time(),
        "wave_valid": valid,
        "role": "completed_lane_execution_domain_support",
    }
    done_path = stage_dir / f"{participant_key(scene, wave_sequence)}.done"
    done_path.write_text(
        json.dumps(done_payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    completion_started = time.monotonic()
    done_rows: List[Dict[str, Any]] = []
    while True:
        done_rows = load_done_rows(stage_dir)
        if len(done_rows) >= cohort_size:
            break
        if time.monotonic() - completion_started >= timeout_s:
            raise RuntimeError(
                "support completion cohort timed out: "
                f"completed={len(done_rows)}/{cohort_size} "
                f"sequence={wave_sequence}"
            )
        time.sleep(0.2)
    completion_receipt = {
        "contract": "algorithm_c_four_lane_candidate_completion_cohort_v1",
        "passed": all(row.get("wave_valid") is True for row in done_rows),
        "cohort_size": cohort_size,
        "completed_count": len(done_rows),
        "wave_sequence": wave_sequence,
        "round_index": round_index,
        "barrier_stage_sha256": stage_dir.name.rsplit("_", 1)[-1],
        "next_restore_blocked_until_all_lanes_complete": True,
    }
    valid = bool(valid and completion_receipt["passed"] is True)
    append_jsonl(
        log_path,
        {
            "event": "algorithm_c_cohort_support_completion_barrier",
            "time": time.time(),
            "scene": scene,
            "completion_barrier": completion_receipt,
            "done_rows": done_rows,
            "formal_state_mutation": False,
        },
    )
    append_jsonl(
        log_path,
        {
            "event": "algorithm_c_cohort_support_wave_complete",
            "time": time.time(),
            "scene": scene,
            "wave_sequence": wave_sequence,
            "round_index": round_index,
            "support_wave_width": width,
            "valid": valid,
            "valid_replays": sum(physical_valid(row) for row in rows),
            "technical_failures": sum(not physical_valid(row) for row in rows),
            "qualification": qualification,
            "completion_barrier": completion_receipt,
            "rows": rows,
            "workload_environment": workload_environment,
            "formal_state_mutation": False,
        },
    )
    if not valid:
        stop_active_controllers(
            campaign_root,
            ready_rows,
            log_path,
            reason=f"completed lane support failed at wave {wave_sequence}",
        )
        raise RuntimeError(f"completed lane cohort support failed: wave={wave_sequence}")
    return True


def restore_clean_baseline(
    cfg: Dict[str, Any],
    target: str,
    workers: Sequence[Dict[str, Any]],
    initial_params: Dict[str, float],
    scene: str,
    log_path: Path,
) -> None:
    restores = restore_support_workers(cfg, workers, "algorithm_c_cohort_support_final")
    readbacks: List[Dict[str, str]] = []
    for worker in workers:
        readbacks.append(
            apply_pg_params(pg_conn_from_worker(cfg, worker), initial_params)
        )
    append_jsonl(
        log_path,
        {
            "event": "algorithm_c_cohort_support_final_baseline_restored",
            "time": time.time(),
            "scene": scene,
            "target": target,
            "restores": restores,
            "readbacks": readbacks,
            "formal_state_mutation": False,
        },
    )


def support_lane(
    campaign_root: Path,
    scene: str,
    cohort_id: str,
    cohort_size: int,
    barrier_timeout_s: float,
    terminal_wait_timeout_s: float,
) -> None:
    lane_root = campaign_root / scene
    manifest_path = lane_root / "authority_manifest.json"
    state = wait_for_terminal_manifest(
        lane_root,
        timeout_s=terminal_wait_timeout_s,
    )
    manifest: Dict[str, Any] = {}
    if state.get("phase") == "complete":
        manifest = read_json(manifest_path)
        validate_authoritative_completion_manifest(manifest, scene)
    elif str(state.get("phase") or "") not in CLEAN_TERMINAL_PHASES:
        raise RuntimeError(f"lane is not a clean support terminal: {scene}")

    cfg_path = lane_root / "optimizer_config.json"
    cfg = read_json(cfg_path)
    algorithm_cfg = cfg.get("algorithm_c") or {}
    workload_contract = algorithm_cfg.get("workload_contract") or {}
    target = str(
        algorithm_cfg.get("target_resource")
        or workload_contract.get("target_resource")
        or {"job_memory": "memory", "tpch_memory": "memory", "xp_cpu": "cpu", "xp_io": "io"}.get(scene)
        or ""
    )
    if target not in {"cpu", "memory", "io"}:
        raise RuntimeError(f"invalid target for support lane {scene}: {target}")
    workers = validation_workers(cfg, target)
    if len(workers) != 2:
        raise RuntimeError(f"support lane requires two workers: {scene}")
    final_champion = state.get("final_champion")
    support_params = (
        final_champion.get("params")
        if isinstance(final_champion, Mapping)
        and isinstance(final_champion.get("params"), Mapping)
        else state.get("incumbent_params")
    )
    params = {
        str(name): float(value)
        for name, value in (support_params or {}).items()
    }
    initial_params = {
        str(name): float(value)
        for name, value in (algorithm_cfg.get("initial_params") or {}).items()
    }
    next_sequence = next_campaign_wave_sequence(campaign_root, cohort_id)
    log_path = campaign_root / "cohort_support" / f"{scene}.jsonl"
    append_jsonl(
        log_path,
        {
            "event": "algorithm_c_cohort_support_started",
            "time": time.time(),
            "scene": scene,
            "target": target,
            "next_wave_sequence": next_sequence,
            "authority_manifest_sha256": (
                hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                if manifest_path.is_file()
                else ""
            ),
            "source_terminal_phase": state.get("phase"),
            "source_terminal_converged": state.get("phase") == "complete",
            "formal_state_mutation": False,
        },
    )
    while not campaign_clean_terminal(campaign_root):
        if not lane_clean_terminal(campaign_root, scene):
            append_jsonl(
                log_path,
                {
                    "event": "algorithm_c_cohort_support_suspended_for_active_lane",
                    "time": time.time(),
                    "scene": scene,
                    "wave_sequence": next_sequence,
                    "formal_state_mutation": False,
                },
            )
            state = wait_for_terminal_manifest(
                lane_root,
                timeout_s=terminal_wait_timeout_s,
            )
            if state.get("phase") == "complete":
                manifest = read_json(manifest_path)
                validate_authoritative_completion_manifest(manifest, scene)
            final_champion = state.get("final_champion")
            support_params = (
                final_champion.get("params")
                if isinstance(final_champion, Mapping)
                and isinstance(final_champion.get("params"), Mapping)
                else state.get("incumbent_params")
            )
            params = {
                str(name): float(value)
                for name, value in (support_params or {}).items()
            }
            next_sequence = next_campaign_wave_sequence(campaign_root, cohort_id)
            append_jsonl(
                log_path,
                {
                    "event": "algorithm_c_cohort_support_resumed_after_lane_terminal",
                    "time": time.time(),
                    "scene": scene,
                    "next_wave_sequence": next_sequence,
                    "source_terminal_phase": state.get("phase"),
                    "formal_state_mutation": False,
                },
            )
            continue
        wave_completed = run_support_wave(
            cfg=cfg,
            scene=scene,
            target=target,
            workers=workers,
            params=params,
            campaign_root=campaign_root,
            cohort_id=cohort_id,
            cohort_size=cohort_size,
            wave_sequence=next_sequence,
            timeout_s=barrier_timeout_s,
            log_path=log_path,
        )
        if wave_completed:
            next_sequence += 1
    restore_clean_baseline(
        cfg,
        target,
        workers,
        initial_params,
        scene,
        log_path,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--cohort-id", required=True)
    parser.add_argument("--cohort-size", type=int, default=4)
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_BARRIER_TIMEOUT_S,
        help="Timeout for one candidate cohort barrier.",
    )
    parser.add_argument(
        "--terminal-wait-timeout-s",
        type=float,
        default=DEFAULT_TERMINAL_WAIT_TIMEOUT_S,
        help="Timeout for waiting until a lane reaches a clean terminal.",
    )
    parser.add_argument("--state-file-name", default=STATE_FILE_NAME)
    parser.add_argument("--history-file-name", default=HISTORY_FILE_NAME)
    parser.add_argument("--controller-script-name", default=CONTROLLER_SCRIPT_NAME)
    return parser.parse_args()


def main() -> int:
    global STATE_FILE_NAME, HISTORY_FILE_NAME, CONTROLLER_SCRIPT_NAME
    args = parse_args()
    STATE_FILE_NAME = str(args.state_file_name)
    HISTORY_FILE_NAME = str(args.history_file_name)
    CONTROLLER_SCRIPT_NAME = str(args.controller_script_name)
    if args.cohort_size != len(SCENES):
        raise RuntimeError("the PostgreSQL four-lane support cohort must have size four")
    execution_domain = configure_shared_ssd_execution_domain(args.campaign_root)
    environment_log = (
        args.campaign_root
        / "cohort_support"
        / f"environment_{canonical_sha256({'cohort_id': args.cohort_id})[:16]}.jsonl"
    )
    append_jsonl(
        environment_log,
        {
            "event": "algorithm_c_cohort_support_execution_domain_configured",
            "time": time.time(),
            "cohort_id": args.cohort_id,
            "execution_domain": execution_domain,
        },
    )
    with ThreadPoolExecutor(max_workers=len(SCENES)) as executor:
        futures = {
            executor.submit(
                support_lane,
                args.campaign_root,
                scene,
                args.cohort_id,
                args.cohort_size,
                args.timeout_s,
                args.terminal_wait_timeout_s,
            ): scene
            for scene in SCENES
        }
        for future in as_completed(futures):
            scene = futures[future]
            try:
                future.result()
            except Exception as exc:
                failure_path = (
                    args.campaign_root
                    / "cohort_support"
                    / f"supervisor_failure_{canonical_sha256({'cohort_id': args.cohort_id})[:16]}.json"
                )
                failure_path.parent.mkdir(parents=True, exist_ok=True)
                failure_path.write_text(
                    json.dumps(
                        {
                            "scene": scene,
                            "error": str(exc),
                            "time": time.time(),
                            "barrier_timeout_s": args.timeout_s,
                            "terminal_wait_timeout_s": (
                                args.terminal_wait_timeout_s
                            ),
                        },
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                raise
    receipt_id = canonical_sha256({"cohort_id": args.cohort_id})[:16]
    failure_path = (
        args.campaign_root
        / "cohort_support"
        / f"supervisor_failure_{receipt_id}.json"
    )
    success_path = (
        args.campaign_root
        / "cohort_support"
        / f"supervisor_success_{receipt_id}.json"
    )
    success_path.write_text(
        json.dumps(
            {
                "cohort_id": args.cohort_id,
                "time": time.time(),
                "barrier_timeout_s": args.timeout_s,
                "terminal_wait_timeout_s": args.terminal_wait_timeout_s,
                "all_support_lanes_completed": True,
                "superseded_failure_receipt": (
                    str(failure_path) if failure_path.is_file() else None
                ),
                "formal_state_mutation": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
