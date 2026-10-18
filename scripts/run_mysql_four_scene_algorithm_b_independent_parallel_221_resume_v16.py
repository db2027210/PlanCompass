#!/usr/bin/env python3
"""Resume B outer 5 rank 5 with write-specific disk2 settle evidence."""

from __future__ import annotations

import copy
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v32 as c32
from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v15
    as predecessor,
)


v12 = predecessor.v12
core = predecessor.core
base = predecessor.base
alg = predecessor.alg
runtime = predecessor.runtime
SOURCE = (
    base.HISTORY
    / "v15_formal_parallel221_outer5_rank3_settle_resume_20260818_attempt009"
)
SCHEDULER_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v16"
    / "algorithm_b_parallel_221_contract.json"
)
SETTLE_ERROR = "RuntimeError: disk2 post-reset writeback did not settle in 120s"
FAILED_SETTLE = (
    SOURCE
    / "xp_resets/algorithm_b_outer_005_rank_5/post_reset_storage_settle.json"
)
FAILED_RESET_BINDING = (
    SOURCE / "xp_resets/algorithm_b_outer_005_rank_5/reset_binding.json"
)
REUSED_BATCHES = {
    **predecessor.REUSED_BATCHES,
    **{
        (5, rank): SOURCE / f"outer_005/top5_batch_{rank}/batch_result.json"
        for rank in (3, 4)
    },
}
_ORIGINAL_ATOMIC = predecessor._ORIGINAL_ATOMIC
DISK2_DEVICE_STAT = Path("/sys/class/block/nvme2n1/stat")
DISK2_DEVICE_INFLIGHT = Path("/sys/class/block/nvme2n1/inflight")
QUIET_REQUIRED_SECONDS = 15
QUIET_MAXIMUM_SECONDS = 120
MAX_WRITE_BYTES_PER_SECOND = 8 * 1024 * 1024
MAX_WRITE_IN_FLIGHT_IOS = 1


def _regular(path: Path) -> dict[str, Any]:
    return dict(v12._regular_json(path))


def _disk2_write_state() -> tuple[int, int, int]:
    stat_fields = DISK2_DEVICE_STAT.read_text(encoding="ascii").split()
    inflight_fields = DISK2_DEVICE_INFLIGHT.read_text(encoding="ascii").split()
    if len(stat_fields) < 11 or len(inflight_fields) != 2:
        raise RuntimeError("disk2 block-device write state is incomplete")
    return int(stat_fields[6]), int(inflight_fields[0]), int(inflight_fields[1])


def reset_xp_write_precise(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    output: Path, token: str, *, worker_ids: set[str] | None = None,
) -> None:
    c32.ORIGINAL_RESET_XP(
        freeze, workers, output, token, worker_ids=worker_ids,
    )
    runtime_root = Path(str(freeze["storage"]["runtime_root"])).resolve()
    if (
        runtime_root != Path("/disk2/resource_adaptive_mysql_algorithm_c_disk2_v4")
        or not runtime_root.is_dir()
        or not DISK2_DEVICE_STAT.is_file()
        or not DISK2_DEVICE_INFLIGHT.is_file()
    ):
        raise RuntimeError("write-specific post-reset disk2 settle authority differs")
    stage = output / "xp_resets" / token
    evidence_path = stage / "post_reset_storage_settle.json"
    if evidence_path.exists() or evidence_path.is_symlink():
        raise RuntimeError("fresh write-specific post-reset settle evidence required")

    sync_started = time.monotonic()
    synced = subprocess.run(
        ["sync", "-f", str(runtime_root)],
        text=True, capture_output=True, check=False, timeout=300,
    )
    sync_seconds = time.monotonic() - sync_started
    if synced.returncode != 0:
        raise RuntimeError(
            "disk2 syncfs failed after XP reset: " + synced.stderr[-500:]
        )

    quiet_started = time.monotonic()
    previous_sectors, _, _ = _disk2_write_state()
    consecutive = 0
    samples: list[dict[str, Any]] = []
    while time.monotonic() - quiet_started < QUIET_MAXIMUM_SECONDS:
        sample_started = time.monotonic()
        time.sleep(1.0)
        sectors, read_inflight, write_inflight = _disk2_write_state()
        elapsed = max(1e-9, time.monotonic() - sample_started)
        write_bps = max(0, sectors - previous_sectors) * 512.0 / elapsed
        previous_sectors = sectors
        passed = (
            write_bps <= MAX_WRITE_BYTES_PER_SECOND
            and write_inflight <= MAX_WRITE_IN_FLIGHT_IOS
        )
        consecutive = consecutive + 1 if passed else 0
        samples.append({
            "elapsed_seconds": time.monotonic() - quiet_started,
            "write_bytes_per_second": write_bps,
            "read_in_flight_ios_diagnostic_only": read_inflight,
            "write_in_flight_ios": write_inflight,
            "sample_passed": passed,
            "consecutive_quiet_seconds": consecutive,
        })
        if consecutive >= QUIET_REQUIRED_SECONDS:
            break

    passed = consecutive >= QUIET_REQUIRED_SECONDS
    document = {
        "schema_version": "mysql_algorithm_b_post_reset_storage_settle_v2",
        "status": "PASS" if passed else "FAILED_CLOSED",
        "created_at": runtime.alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "runtime_root": str(runtime_root),
        "block_device": "nvme2n1",
        "write_inflight_authority": str(DISK2_DEVICE_INFLIGHT),
        "read_inflight_is_diagnostic_only": True,
        "syncfs_command": ["sync", "-f", str(runtime_root)],
        "syncfs_returncode": synced.returncode,
        "syncfs_seconds": sync_seconds,
        "quiet_required_seconds": QUIET_REQUIRED_SECONDS,
        "quiet_maximum_seconds": QUIET_MAXIMUM_SECONDS,
        "maximum_write_bytes_per_second": MAX_WRITE_BYTES_PER_SECOND,
        "maximum_write_in_flight_ios": MAX_WRITE_IN_FLIGHT_IOS,
        "quiet_elapsed_seconds": time.monotonic() - quiet_started,
        "samples": samples,
        "workload_started_before_gate": False,
        "accepted_state_credit_mutated": False,
        "workload_or_resource_envelope_changed": False,
        "numeric_writeback_thresholds_changed": False,
    }
    runtime.alg.atomic(evidence_path, document)
    if not passed:
        raise RuntimeError(
            "disk2 write-specific post-reset writeback did not settle in 120s"
        )


def validate_source() -> dict[str, Any]:
    state = _regular(SOURCE / "run_state.json")
    settle = _regular(FAILED_SETTLE)
    reset = _regular(FAILED_RESET_BINDING)
    expected_accepted = {
        "job_memory": 1,
        "tpch_sf4_memory": 0,
        "hybench_xp_cpu": 1,
        "hybench_xp_io": 0,
    }
    expected_rejected = {
        "job_memory": 3,
        "tpch_sf4_memory": 4,
        "hybench_xp_cpu": 3,
        "hybench_xp_io": 4,
    }
    if (
        state.get("algorithm") != "B"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != SETTLE_ERROR
        or int(state.get("outer_attempt") or 0) != 4
        or state.get("accepted_rounds") != expected_accepted
        or state.get("rejected_outer_attempts") != expected_rejected
        or any(bool(value) for value in (state.get("converged") or {}).values())
        or settle.get("status") != "FAILED_CLOSED"
        or int(settle.get("maximum_in_flight_ios") or 0) != 1
        or settle.get("workload_started_before_gate") is not False
        or settle.get("accepted_state_credit_mutated") is not False
        or max(int(row.get("in_flight_ios") or 0)
               for row in settle.get("samples") or [{}]) < 2
        or max(float(row.get("write_bytes_per_second") or 0)
               for row in settle.get("samples") or [{}])
        > MAX_WRITE_BYTES_PER_SECOND
        or reset.get("status") != "PASS"
        or len(reset.get("workers") or []) != 4
        or any(row.get("ready") is not True for row in reset.get("workers") or [])
        or (SOURCE / "outer_005/top5_batch_5").exists()
        or any((SOURCE / "outer_005").glob("*.decision.json"))
    ):
        raise RuntimeError("v16 outer-5 rank-5 settle-failure boundary differs")
    for outer in (1, 2, 3, 4):
        decisions = sorted((SOURCE / f"outer_{outer:03d}").glob("*.decision.json"))
        if len(decisions) != 4:
            raise RuntimeError(f"v16 source outer {outer} decision cardinality differs")
        for path in decisions:
            _regular(path)
    rankings = _regular(SOURCE / "outer_005/predicted_rankings.json")
    if (
        rankings.get("status") != "PASS"
        or int(rankings.get("outer_attempt") or 0) != 5
        or rankings.get("active_scenes") != list(base.SCENES)
        or any(len(rows) != 5 for rows in
               (rankings.get("selected_top_k") or {}).values())
    ):
        raise RuntimeError("v16 source outer-5 ranking differs")
    for key, path in REUSED_BATCHES.items():
        batch = _regular(path)
        rank = key[1]
        if (
            int(batch.get("candidate_rank") or rank) != rank
            or not (batch.get("measurements") or {})
            or any(int(row.get("rank") or 0) != rank
                   for row in (batch.get("measurements") or {}).values())
        ):
            raise RuntimeError(f"v16 reused batch identity differs: {key}")
    return state


def measure_resume(**kwargs: Any):
    output = Path(kwargs["output"])
    match = re.search(r"outer_(\d+)/top5_batch_(\d+)$", str(output))
    if match is not None:
        key = (int(match.group(1)), int(match.group(2)))
        source = REUSED_BATCHES.get(key)
        if source is not None:
            destination = output / "batch_result.json"
            base._mirror_batch(source, destination)
            v12._mirror_supporting_wave_evidence(source, destination)
            document = alg.load(destination)
            state = kwargs["state"]
            state["stage"] = f"{kwargs['stage_prefix']}_RESTORED_VERIFIED"
            state["outer5_write_settle_continuation_source"] = str(SOURCE)
            state["outer5_write_settle_continuation_reused_batches"] = [
                [outer, rank] for outer, rank in sorted(REUSED_BATCHES)
                if (outer, rank) <= key
            ]
            alg.atomic(Path(kwargs["state_path"]), state)
            return copy.deepcopy(dict(document["measurements"])), state
    return core._measure_221(**kwargs)


def atomic_with_outer4_identity(path: Path, value: Mapping[str, Any]) -> None:
    if path.name.endswith(".decision.json") and path.parent.name in {
        "outer_002", "outer_003", "outer_004",
    }:
        source = _regular(SOURCE / path.parent.name / path.name)
        if v12._decision_semantics(value) != v12._decision_semantics(source):
            raise RuntimeError(
                f"{path.parent.name} reconstructed decision differs: {path.name}"
            )
    if path.name == "predicted_rankings.json" and path.parent.name == "outer_005":
        source = _regular(SOURCE / "outer_005/predicted_rankings.json")
        if (
            base.ranking_identity(value.get("selected_top_k") or {})
            != base.ranking_identity(source.get("selected_top_k") or {})
        ):
            raise RuntimeError("outer-5 recomputed Top-5 differs from source")
    _ORIGINAL_ATOMIC(path, value)


def state_document_v16(run_id: str) -> dict[str, Any]:
    original_contract = predecessor.SCHEDULER_CONTRACT
    try:
        predecessor.SCHEDULER_CONTRACT = SCHEDULER_CONTRACT
        document = predecessor.state_document_v15(run_id)
    finally:
        predecessor.SCHEDULER_CONTRACT = original_contract
    document["technical_continuation"].update({
        "kind": "outer5_rank5_write_specific_settle_resume_v16",
        "source_run_root": str(SOURCE),
        "source_failure_state_sha256": alg.sha(SOURCE / "run_state.json"),
        "completed_outer_decisions_reconstructed": [1, 2, 3, 4],
        "outer5_completed_candidate_ranks_reused": [1, 2, 3, 4],
        "outer5_ranking_recomputed_and_identity_checked": True,
        "failed_outer5_rank5_measurement_reused": False,
        "failed_outer5_rank5_workload_started": False,
        "fresh_outer5_rank5_reset_required": True,
        "write_inflight_authority": str(DISK2_DEVICE_INFLIGHT),
        "read_inflight_diagnostic_only": True,
        "numeric_writeback_thresholds_changed": False,
        "failed_settle_path": str(FAILED_SETTLE),
        "failed_settle_sha256": alg.sha(FAILED_SETTLE),
        "accepted_or_rejected_state_after_outer4_reconstructed": True,
    })
    document["post_reset_storage_settle_release"] = {
        "release_version": "v16_write_specific",
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": alg.sha(Path(__file__).resolve()),
        "disk2_syncfs_required": True,
        "write_inflight_authority": str(DISK2_DEVICE_INFLIGHT),
        "read_inflight_diagnostic_only": True,
        "quiet_required_seconds": QUIET_REQUIRED_SECONDS,
        "quiet_maximum_seconds": QUIET_MAXIMUM_SECONDS,
        "maximum_write_bytes_per_second": MAX_WRITE_BYTES_PER_SECOND,
        "maximum_write_in_flight_ios": MAX_WRITE_IN_FLIGHT_IOS,
        "measurement_timeout_unchanged_seconds": 900,
        "workload_contract_unchanged": True,
        "resource_envelope_unchanged": True,
    }
    document["controller_source_path"] = str(Path(__file__).resolve())
    document["controller_source_sha256"] = alg.sha(Path(__file__).resolve())
    return document


def validate_contract() -> dict[str, Any]:
    if not SCHEDULER_CONTRACT.is_file() or SCHEDULER_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed v16 scheduler contract absent: {SCHEDULER_CONTRACT}")
    contract = alg.load(SCHEDULER_CONTRACT)
    continuation = contract.get("continuation") or {}
    if (
        contract.get("release_version") != "v16"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or int(contract.get("candidate_parallelism") or 0) != 2
        or continuation.get("resume_boundary")
        != "outer_005_rank_5_post_reset_settle"
        or continuation.get("write_inflight_authority")
        != str(DISK2_DEVICE_INFLIGHT)
        or continuation.get("numeric_writeback_thresholds_changed") is not False
    ):
        raise RuntimeError("parallel v16 continuation contract differs")
    return contract


def install() -> None:
    predecessor.install()
    runtime.alg.reset_xp = reset_xp_write_precise
    runtime._measure_batch_with_fair_coordination = measure_resume
    base.state_document = state_document_v16
    alg.atomic = atomic_with_outer4_identity


def main() -> int:
    validate_contract()
    predecessor.validate_source()
    validate_source()
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
