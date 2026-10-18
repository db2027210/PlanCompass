#!/usr/bin/env python3
"""Resume JOB Algorithm A after an invalid recovery-only incumbent replay.

The accepted search state and the same-worker baseline come from the sealed
pre-interruption boundary snapshot.  A later recovery replay that completed
all 113 JOB statements but exceeded the formal 900 second wall-time limit is
retained as technical evidence only: it receives neither acceptance nor
rejection credit and does not replace the last accepted reference.  The
interrupted outer round is therefore restarted from rank one without another
incumbent replay.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_dynamic_slots_v2 as slots
from scripts import run_mysql_algorithm_a_async_scene_v3 as base
from scripts import run_mysql_algorithm_a_same_cohort_codefix_resume_v1 as resume


CONTRACT_NAME = "mysql_algorithm_a_job_recovery_replay_excluded_resume_v1"
CONTRACT_PATH = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job_recovery_replay_excluded_20260823_v1"
    / "transition_contract.json"
)
MAXIMUM_PRESTART_WAIT_SECONDS = 600.0
PRESTART_RETRY_SECONDS = 0.5
BLOCKED_PREFIX = "fresh R0 point non-interference gate blocked:"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise argparse.ArgumentTypeError(f"regular file required: {path}")
    return path


def validate_contract(
    *, predecessor_path: Path, excluded_state_path: Path,
    excluded_batch_path: Path,
) -> dict[str, Any]:
    contract = load(CONTRACT_PATH)
    if (
        CONTRACT_PATH.is_symlink()
        or contract.get("schema_version") != CONTRACT_NAME
        or contract.get("status") != "FROZEN"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("resource_envelope_changed") is not False
        or contract.get("candidate_set_changed") is not False
        or contract.get("accepted_credit_for_excluded_replay") is not False
        or contract.get("rejected_credit_for_excluded_replay") is not False
        or contract.get("resume_interrupted_round_from_rank_one") is not True
        or contract.get("incumbent_reference_refresh_trigger")
        != "two_consecutive_semantic_rejections"
        or float(contract.get("maximum_prestart_wait_seconds") or 0)
        != MAXIMUM_PRESTART_WAIT_SECONDS
    ):
        raise RuntimeError("JOB recovery-replay exclusion contract differs")
    bindings = contract.get("source_bindings") or {}
    expected = {
        "predecessor_state": predecessor_path,
        "excluded_recovery_state": excluded_state_path,
        "excluded_recovery_batch": excluded_batch_path,
        "controller": Path(__file__).resolve(),
        "same_cohort_resume": (
            ROOT / "scripts/run_mysql_algorithm_a_same_cohort_codefix_resume_v1.py"
        ),
        "algorithm_a_core": ROOT / "scripts/run_mysql_algorithm_a_async_scene_v3.py",
    }
    if set(bindings) != set(expected):
        raise RuntimeError("JOB recovery-replay source bindings differ")
    for name, path in expected.items():
        binding = bindings[name]
        if (
            path.is_symlink()
            or not path.is_file()
            or Path(str(binding.get("path") or "")).resolve() != path
            or binding.get("sha256") != sha(path)
        ):
            raise RuntimeError(f"JOB recovery-replay source differs: {name}")
    return contract


def validate_excluded_recovery(
    *, predecessor: Mapping[str, Any], state_path: Path, batch_path: Path,
) -> dict[str, Any]:
    state = load(state_path)
    batch = load(batch_path)
    measurements_value = batch.get("measurements") or {}
    measurements = (
        list(measurements_value.values())
        if isinstance(measurements_value, Mapping)
        else list(measurements_value)
    )
    if (
        state.get("algorithm") != "A"
        or state.get("async_scene") != "job_memory"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or int(state.get("accepted") or 0) != int(predecessor.get("accepted") or 0)
        or int(state.get("rejected_rounds") or 0)
        != int(predecessor.get("rejected_rounds") or 0)
        or batch.get("status") != "FAILED_CLOSED"
        or float(batch.get("elapsed_seconds") or 0) <= 900.0
        or batch.get("failures") != []
        or len(measurements) != base.REPLAY_PARALLELISM
        or not all(row.get("coverage_complete") is True for row in measurements)
        or not all(row.get("hard_gate_passed") is False for row in measurements)
    ):
        raise RuntimeError("excluded JOB recovery replay evidence differs")
    return {
        "state_path": str(state_path),
        "state_sha256": sha(state_path),
        "batch_path": str(batch_path),
        "batch_sha256": sha(batch_path),
        "batch_status": batch["status"],
        "elapsed_seconds": float(batch["elapsed_seconds"]),
        "coverage_complete_for_every_worker": True,
        "formal_wall_time_limit_seconds": 900,
        "technical_failure_only": True,
        "accepted_credit": False,
        "rejected_credit": False,
        "reference_replacement": False,
    }


def validate_boundary_predecessor(
    predecessor: Mapping[str, Any], scene: str,
) -> tuple[dict[str, Any], int, int]:
    if (
        scene != "job_memory"
        or predecessor.get("algorithm") != "A"
        or predecessor.get("async_scene") != scene
        or predecessor.get("formal_scope") != "mysql_only"
        or predecessor.get("cross_engine_comparison_prohibited") is not True
        or predecessor.get("status") != "REBOOT_BOUNDARY_SNAPSHOT"
        or predecessor.get("stage")
        != "EXTERNAL_SSSP_MEMORY_INTERRUPTED_PARTIAL_ROUND_ARCHIVED"
        or predecessor.get("round_checkpoint") is not None
        or int(predecessor.get("accepted") or 0) >= base.TARGET_ACCEPTED
    ):
        raise RuntimeError("invalid JOB recovery boundary predecessor")
    interrupted_round = int(predecessor.get("round_index") or 0)
    completed_round = interrupted_round - 1
    if interrupted_round < 2 or completed_round < 1:
        raise RuntimeError("JOB recovery boundary round identity differs")
    preserved = slots.search_state_payload(predecessor)
    baseline_rows = predecessor.get("baseline_by_worker")
    if (
        not isinstance(baseline_rows, Mapping)
        or len(baseline_rows) != base.REPLAY_PARALLELISM
        or not all(base.pg_outer.physical_valid(row) for row in baseline_rows.values())
    ):
        raise RuntimeError("JOB recovery boundary baseline is invalid")
    preserved["baseline_by_worker"] = copy.deepcopy(baseline_rows)
    preserved["rejected_history"] = copy.deepcopy(
        predecessor.get("rejected_history") or []
    )
    return preserved, completed_round, interrupted_round


def run(args: argparse.Namespace) -> int:
    contract = validate_contract(
        predecessor_path=args.predecessor_state,
        excluded_state_path=args.excluded_recovery_state,
        excluded_batch_path=args.excluded_recovery_batch,
    )
    predecessor = load(args.predecessor_state)
    excluded = validate_excluded_recovery(
        predecessor=predecessor,
        state_path=args.excluded_recovery_state,
        batch_path=args.excluded_recovery_batch,
    )

    original_validator = resume.validate_predecessor
    original_resume_contract = resume.RESUME_CONTRACT
    original_authority_validator = base.validate_fresh_authority
    original_atomic = base.atomic
    state_path = args.output_dir.resolve() / "algorithm_a_state.json"

    def bounded_authority_validator(**kwargs: Any):
        started = time.monotonic()
        attempts = 0
        first_blocker = None
        while True:
            attempts += 1
            try:
                result = original_authority_validator(**kwargs)
                base.atomic(
                    args.output_dir.resolve() / "prestart_read_only_retry.json",
                    {
                        "schema_version": "mysql_algorithm_a_prestart_read_only_retry_v1",
                        "status": "PASS",
                        "formal_scope": "mysql_only",
                        "cross_engine_comparison_prohibited": True,
                        "attempts": attempts,
                        "wait_seconds": time.monotonic() - started,
                        "retry_interval_seconds": PRESTART_RETRY_SECONDS,
                        "maximum_wait_seconds": MAXIMUM_PRESTART_WAIT_SECONDS,
                        "first_blocker": first_blocker,
                        "external_process_mutation": False,
                        "optimizer_or_measurement_state_mutated_while_waiting": False,
                    },
                )
                return result
            except RuntimeError as error:
                message = str(error)
                if not message.startswith(BLOCKED_PREFIX):
                    raise
                if first_blocker is None:
                    first_blocker = message
                if time.monotonic() - started >= MAXIMUM_PRESTART_WAIT_SECONDS:
                    raise TimeoutError(
                        "JOB recovery continuation prestart retry timed out"
                    ) from error
                time.sleep(PRESTART_RETRY_SECONDS)

    def tagged_atomic(path: Path, value: Mapping[str, Any]) -> None:
        document = copy.deepcopy(dict(value))
        if path.resolve() == state_path:
            document["recovery_incumbent_replay_exclusion"] = copy.deepcopy(excluded)
            document["recovery_incumbent_replay_exclusion"].update({
                "contract": CONTRACT_NAME,
                "contract_path": str(CONTRACT_PATH),
                "contract_sha256": sha(CONTRACT_PATH),
                "preserved_predecessor_state_path": str(args.predecessor_state),
                "preserved_predecessor_state_sha256": sha(args.predecessor_state),
                "resume_round_index": int(predecessor["round_index"]),
                "resume_from_rank_one": True,
                "resource_envelope_changed": False,
                "candidate_set_changed": False,
                "algorithm_a_outer_flow_changed": False,
            })
            document["recovery_or_worker_transition_reference"] = (
                "preserved_last_accepted_reference_without_replay"
            )
            document["incumbent_reference_refresh_after_consecutive_rejections"] = 2
        original_atomic(path, document)

    resume.validate_predecessor = validate_boundary_predecessor
    resume.RESUME_CONTRACT = CONTRACT_NAME
    base.validate_fresh_authority = bounded_authority_validator
    base.atomic = tagged_atomic
    try:
        return resume.run(args)
    finally:
        resume.validate_predecessor = original_validator
        resume.RESUME_CONTRACT = original_resume_contract
        base.validate_fresh_authority = original_authority_validator
        base.atomic = original_atomic


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=("job_memory",), required=True)
    parser.add_argument("--predecessor-state", type=regular_file, required=True)
    parser.add_argument("--excluded-recovery-state", type=regular_file, required=True)
    parser.add_argument("--excluded-recovery-batch", type=regular_file, required=True)
    parser.add_argument("--runtime-preflight", type=regular_file, required=True)
    parser.add_argument("--prepared-binding", type=regular_file, required=True)
    parser.add_argument("--benchmark-accounts", type=regular_file, required=True)
    parser.add_argument("--fresh-r0", type=regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
