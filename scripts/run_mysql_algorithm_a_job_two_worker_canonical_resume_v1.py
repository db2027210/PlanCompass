#!/usr/bin/env python3
"""Resume canonicalized JOB Algorithm A state on the original two workers."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_external_workload_diagnostic_gate_v1 as diagnostic_gate
from scripts import mysql_job_buffer_pool_preconditioned_measurement_v10 as job_v10
from scripts import run_mysql_algorithm_a_job_dynamic_resume_v3 as dynamic
from scripts import run_mysql_algorithm_a_same_cohort_codefix_resume_v1 as resume


CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_job_two_worker_canonical_resume_20260824_v1"
CONTRACT_PATH = CONFIG / "resume_contract.json"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def validate_contract(predecessor_path: Path) -> dict[str, Any]:
    contract = load(CONTRACT_PATH)
    if (
        contract.get("schema_version") != "mysql_algorithm_a_job_two_worker_canonical_resume_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("physical_replay_parallelism") != 2
        or contract.get("accepted_before") != 2
        or contract.get("accepted_after") != 2
        or contract.get("accepted_round_labels") != [1, 3]
        or contract.get("rejected_round_labels") != [2, 4, 5]
        or contract.get("next_outer_round") != 6
        or contract.get("four_worker_failed_wave_valid_candidates") != 0
        or contract.get("baseline_replayed") is not False
        or contract.get("incumbent_replayed") is not False
        or predecessor_path.resolve() != Path(contract["canonical_snapshot_path"]).resolve()
        or resume.sha(predecessor_path) != contract.get("canonical_snapshot_sha256")
    ):
        raise RuntimeError("JOB two-worker canonical resume contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or resume.sha(path) != binding.get("sha256"):
            raise RuntimeError(f"JOB two-worker source binding differs: {path}")
    return contract


def main() -> int:
    parser = resume.argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=("job_memory",), required=True)
    parser.add_argument("--predecessor-state", type=resume.regular_file, required=True)
    parser.add_argument("--runtime-preflight", type=resume.regular_file, required=True)
    parser.add_argument("--prepared-binding", type=resume.regular_file, required=True)
    parser.add_argument("--benchmark-accounts", type=resume.regular_file, required=True)
    parser.add_argument("--fresh-r0", type=resume.regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    contract = validate_contract(args.predecessor_state)
    authority_path = Path(contract["authority_path"])
    freeze_path = Path(contract["resource_freeze_path"])
    authority = load(authority_path)
    freeze = load(freeze_path)
    all_workers = {
        str(row["worker_id"]): copy.deepcopy(row)
        for row in authority.get("workers") or []
    }
    workers = {worker_id: all_workers[worker_id] for worker_id in ("job_w1", "job_w2")}
    if (
        resume.sha(authority_path) != contract.get("authority_sha256")
        or resume.sha(freeze_path) != contract.get("resource_freeze_sha256")
        or set(all_workers) != {"job_w1", "job_w2", "job_w3", "job_w4"}
        or any(worker.get("scene") != "job_memory" for worker in workers.values())
    ):
        raise RuntimeError("JOB two-worker runtime authority differs")

    dynamic.adapter.configure_runtime(
        freeze_path=freeze_path, workers=workers, scene="job_memory",
    )
    resume.base.REPLAY_PARALLELISM = 2
    resume.base.runtime.alg.measure_batch = job_v10.measure_batch
    resume.base.runtime.coordination.rolling_quiet_gate = diagnostic_gate.rolling_quiet_gate
    context = {
        "coordination_contract": dynamic.current.c49.validate_v65_wave_coordination_contract(),
        "resource_freeze_path": str(freeze_path),
        "resource_freeze_sha256": resume.sha(freeze_path),
        "two_worker_canonical_resume_contract_path": str(CONTRACT_PATH),
        "two_worker_canonical_resume_contract_sha256": resume.sha(CONTRACT_PATH),
        "external_workloads_diagnostic_only": True,
        "external_workload_wait_required": False,
        "external_process_control_or_stop_prohibited": True,
    }
    resume.base.validate_fresh_authority = lambda **_kwargs: (
        freeze, copy.deepcopy(workers), copy.deepcopy(context)
    )
    return resume.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
