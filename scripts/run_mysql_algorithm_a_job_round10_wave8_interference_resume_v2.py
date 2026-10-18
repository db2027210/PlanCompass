#!/usr/bin/env python3
"""Resume JOB round 10 at wave 8 after the protected BFS interruption."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v2 as enforced_gate
from scripts import mysql_job_buffer_pool_preconditioned_measurement_v10 as job_v10
from scripts import preflight_mysql_four_scene_v5 as preflight_v5
from scripts import run_mysql_algorithm_a_job_dynamic_resume_v3 as dynamic
from scripts import run_mysql_algorithm_a_partial_round_codefix_resume_v2 as partial


CONFIG = (
    ROOT / "config/formal"
    / "frozen_mysql_algorithm_a_job_round10_wave8_interference_resume_20260825_v3"
)
CONTRACT = CONFIG / "resume_contract.json"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def validate_contract(predecessor: Path) -> dict[str, Any]:
    contract = load(CONTRACT)
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_job_wave8_interference_resume_contract_v3"
        or contract.get("release_version") != "v3"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("scene") != "job_memory"
        or contract.get("cached_valid_waves") != [1, 2, 3, 4, 5, 6, 7]
        or contract.get("cached_valid_ranks") != list(range(1, 15))
        or contract.get("first_new_physical_wave") != 8
        or contract.get("first_new_physical_rank") != 15
        or contract.get("external_workload_gate_enforced") is not True
        or contract.get("dynamic_non_interference_gate_required") is not True
        or contract.get("baseline_replayed") is not False
        or contract.get("incumbent_replayed") is not False
        or predecessor.resolve() != Path(str(contract["checkpoint_path"])).resolve()
        or partial.sha(predecessor) != contract.get("checkpoint_sha256")
    ):
        raise RuntimeError("JOB wave-7 interference resume contract differs")
    for source in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(source.get("path") or "")
        if path.is_symlink() or not path.is_file() or partial.sha(path) != source.get("sha256"):
            raise RuntimeError(f"JOB interference resume binding differs: {path}")
    return contract


def main() -> int:
    parser = partial.argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=("job_memory",), required=True)
    parser.add_argument("--predecessor-state", type=partial.regular_file, required=True)
    parser.add_argument("--runtime-preflight", type=partial.regular_file, required=True)
    parser.add_argument("--prepared-binding", type=partial.regular_file, required=True)
    parser.add_argument("--benchmark-accounts", type=partial.regular_file, required=True)
    parser.add_argument("--fresh-r0", type=partial.regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    contract = validate_contract(args.predecessor_state)
    authority_path = Path(str(contract["authority_path"])).resolve()
    freeze_path = Path(str(contract["resource_freeze_path"])).resolve()
    authority = load(authority_path)
    freeze = load(freeze_path)
    all_workers = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in authority.get("workers") or []
    }
    workers = {worker_id: all_workers[worker_id] for worker_id in ("job_w1", "job_w2")}
    if (
        partial.sha(authority_path) != contract.get("authority_sha256")
        or partial.sha(freeze_path) != contract.get("resource_freeze_sha256")
        or set(all_workers) != {"job_w1", "job_w2", "job_w3", "job_w4"}
    ):
        raise RuntimeError("JOB interference resume runtime authority differs")

    dynamic.adapter.configure_runtime(
        freeze_path=freeze_path, workers=workers, scene="job_memory",
    )
    partial.base.REPLAY_PARALLELISM = 2
    partial.base.runtime.alg.measure_batch = job_v10.measure_batch
    partial.base.alg.r0mod.PREFLIGHT_SOURCE = Path(preflight_v5.__file__).resolve()
    partial.base.runtime.coordination.rolling_quiet_gate = enforced_gate.rolling_quiet_gate
    context = {
        "coordination_contract": dynamic.current.c49.validate_v65_wave_coordination_contract(),
        "resource_freeze_path": str(freeze_path),
        "resource_freeze_sha256": partial.sha(freeze_path),
        "interference_resume_contract_path": str(CONTRACT),
        "interference_resume_contract_sha256": partial.sha(CONTRACT),
        "external_workload_gate_enforced": True,
        "external_process_control_or_stop_prohibited": True,
        "cached_valid_waves": [1, 2, 3, 4, 5, 6, 7],
        "first_new_physical_wave": 8,
    }
    partial.base.validate_fresh_authority = lambda **_kwargs: (
        freeze, copy.deepcopy(workers), copy.deepcopy(context)
    )

    original_validate_cached = partial.validate_cached_round
    source_state = Path(str(contract["source_evidence_root"])) / "algorithm_a_state.json"

    def validate_cached_from_source(
        predecessor: Mapping[str, Any], _predecessor_path: Path,
        interrupted_round: int,
    ):
        return original_validate_cached(predecessor, source_state, interrupted_round)

    partial.validate_cached_round = validate_cached_from_source
    try:
        return partial.run(args)
    finally:
        partial.validate_cached_round = original_validate_cached


if __name__ == "__main__":
    raise SystemExit(main())
