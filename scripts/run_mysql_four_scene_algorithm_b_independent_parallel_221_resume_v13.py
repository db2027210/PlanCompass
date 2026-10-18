#!/usr/bin/env python3
"""Resume Algorithm B outer 3 rank 3 from the recovered XP authority."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v12
    as predecessor,
)


base = predecessor.base
alg = predecessor.alg
RECOVERY_ROOT = (
    base.HISTORY / "v13_b_partial_xp_reset_recovery_20260818_attempt006"
)
RECOVERY = RECOVERY_ROOT / "recovered_xp_binding.json"
PREPARED = RECOVERY_ROOT / "prepared_binding.json"
ACCOUNTS = RECOVERY_ROOT / "benchmark_accounts_binding.json"
V12_FAILURE = (
    base.HISTORY
    / "v12_formal_parallel221_outer3_rank3_reset_resume_20260818_attempt004"
    / "run_state.json"
)
SCHEDULER_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v13"
    / "algorithm_b_parallel_221_contract.json"
)


def _regular(path: Path) -> dict[str, Any]:
    value = predecessor._regular_json(path)
    return dict(value)


def validate_recovery() -> dict[str, Any]:
    recovery = _regular(RECOVERY)
    prepared = _regular(PREPARED)
    accounts = _regular(ACCOUNTS)
    v12_state = _regular(V12_FAILURE)
    recovery_workers = {
        str(row.get("worker_id")): dict(row)
        for row in recovery.get("workers") or []
    }
    prepared_workers = {
        str(row.get("worker_id")): dict(row)
        for row in prepared.get("workers") or []
    }
    account_workers = {
        str(row.get("worker_id")): dict(row)
        for row in accounts.get("workers") or []
    }
    if (
        v12_state.get("status") != "FAILED_CLOSED"
        or v12_state.get("error")
        != "RuntimeError: xp_cpu_w2 live container identity differs"
        or int(v12_state.get("outer_attempt", -1)) != 0
        or recovery.get("status") != "PASS"
        or recovery.get("formal_scope") != "mysql_only"
        or recovery.get("cross_engine_comparison_prohibited") is not True
        or set(recovery_workers) != set(predecessor.base.alg.XP_IDS)
        or recovery.get("job_tpch_container_mutation") is not False
        or recovery.get("external_container_mutation") is not False
        or int(recovery.get("formal_state_credit_increment") or 0) != 0
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != alg.sha(base.FREEZE)
        or prepared.get("xp_reset_prepared_sha256") != alg.sha(RECOVERY)
        or len(prepared_workers) != 8
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256") != alg.sha(PREPARED)
        or accounts.get("freeze_sha256") != alg.sha(base.FREEZE)
        or set(account_workers) != set(prepared_workers)
        or any(
            account_workers[worker_id].get("container_id")
            != prepared_workers[worker_id].get("container_id")
            for worker_id in prepared_workers
        )
    ):
        raise RuntimeError("v13 recovered XP authority differs")
    return recovery


def state_document_v13(run_id: str) -> dict[str, Any]:
    original_contract = predecessor.SCHEDULER_CONTRACT
    try:
        predecessor.SCHEDULER_CONTRACT = SCHEDULER_CONTRACT
        document = predecessor.state_document_v12(run_id)
    finally:
        predecessor.SCHEDULER_CONTRACT = original_contract
    document["technical_continuation"].update({
        "kind": "outer3_rank3_post_partial_xp_reset_recovery_resume_v13",
        "v12_prestart_failure_state_path": str(V12_FAILURE),
        "v12_prestart_failure_state_sha256": alg.sha(V12_FAILURE),
        "v12_prestart_failure_workload_started": False,
        "xp_recovery_binding_path": str(RECOVERY),
        "xp_recovery_binding_sha256": alg.sha(RECOVERY),
        "recovered_prepared_binding_path": str(PREPARED),
        "recovered_prepared_binding_sha256": alg.sha(PREPARED),
        "recovered_benchmark_accounts_path": str(ACCOUNTS),
        "recovered_benchmark_accounts_sha256": alg.sha(ACCOUNTS),
        "job_tpch_container_mutation": False,
        "formal_state_credit_increment_during_recovery": 0,
    })
    document["prepared_binding_path"] = str(PREPARED)
    document["prepared_binding_sha256"] = alg.sha(PREPARED)
    document["benchmark_accounts_path"] = str(ACCOUNTS)
    document["benchmark_accounts_sha256"] = alg.sha(ACCOUNTS)
    document["controller_source_path"] = str(Path(__file__).resolve())
    document["controller_source_sha256"] = alg.sha(Path(__file__).resolve())
    return document


def validate_contract() -> dict[str, Any]:
    if not SCHEDULER_CONTRACT.is_file() or SCHEDULER_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed v13 scheduler contract absent: {SCHEDULER_CONTRACT}")
    contract = alg.load(SCHEDULER_CONTRACT)
    if (
        contract.get("release_version") != "v13"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or int(contract.get("candidate_parallelism") or 0) != 2
        or (contract.get("continuation") or {}).get("xp_authority_recovered")
        is not True
    ):
        raise RuntimeError("parallel v13 continuation contract differs")
    return contract


def install() -> None:
    predecessor.install()
    base.PREPARED = PREPARED
    base.ACCOUNTS = ACCOUNTS
    base.state_document = state_document_v13


def main() -> int:
    validate_contract()
    predecessor.validate_source()
    validate_recovery()
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
