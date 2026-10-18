#!/usr/bin/env python3
"""JOB v58 continuation with complete gradient-state commits."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

import job_fresh10_target10_autonomous_v57 as v57


implementation = v57.implementation
EXPECTED_V57_SHA256 = "ebc884eefe3cc5663ec4ae5738d46ff564afe78e4b56c68ef4b440996198285b"
if hashlib.sha256(Path(v57.__file__).read_bytes()).hexdigest() != EXPECTED_V57_SHA256:
    raise implementation.v49.FailClosed("v58 sealed v57 controller SHA mismatch")

TOOLS_DIR = Path(__file__).resolve().parent
ROBUST_V11 = TOOLS_DIR / "run_job_round8_strict_serial_robust_v11_gradient_state_guard.py"
ROBUST_V10 = TOOLS_DIR / "run_job_round8_strict_serial_robust_v10_resource_median.py"
FORMAL_V2 = TOOLS_DIR / "job_formal_target10_batch_controller_v2_gradient_commit.py"
FORMAL_V1 = TOOLS_DIR / "job_formal_target10_batch_controller.py"
REPAIR_TOOL = TOOLS_DIR / "repair_job_v57_to_v58_gradient_state.py"
EXPECTED = {
    ROBUST_V11: "d8919ef67c0b9fe0b9666717e54eedb8cf197b835d4a1e1a918c18674e1b3043",
    ROBUST_V10: "e4738c9eb95e123a38e31410bb067bf817bb8b54e234acbb2f5bc14ed89f2b03",
    FORMAL_V2: "e5e4a090b3731add51005c00996cb2af7c450a3c491708321cc96d5d74cf294b",
    FORMAL_V1: "3c9246b67a7fbaf5e28115efc59e4ac68066fc96e6515b6524099ca804c69668",
    REPAIR_TOOL: "6a43da37c73a2601bd7f57c77798dc751316ecfd82c861e9b810d90b9a588399",
}
CAMPAIGN_CONTRACT = "job_v57_fresh0_resource_median_no_retry_v1"
GRADIENT_CONTRACT = "job_v58_complete_gradient_state_transition_v1"
TRANSACTION_CONTRACT = "job_v58_gradient_state_commit_transaction_v1"
REPAIR_CONTRACT = "job_v57_to_v58_zero_call_gradient_repair_v1"

_PREPARE_FORMAL_V57 = implementation.prepare_formal_v50
_RESUME_V57 = implementation.resume_position_v50

implementation.CONTROLLER_VERSION = "v58"
# Deliberately continue the same fresh0 authority; no accepted count is reset.
implementation.CAMPAIGN_CONTRACT = CAMPAIGN_CONTRACT
implementation.v49.ROBUST_RUNNER = ROBUST_V11
implementation.ROBUST_RUNNER_V9 = ROBUST_V11
implementation.EXPECTED_ROBUST_RUNNER_V9_SHA256 = EXPECTED[ROBUST_V11]
implementation.v49.FORMAL_CONTROLLER = FORMAL_V2


def _sealed_copy(source: Path, target: Path) -> None:
    expected = EXPECTED[source]
    if not source.is_file() or implementation.v49.sha256_file(source) != expected:
        raise implementation.v49.FailClosed(f"v58 dependency SHA mismatch: {source}")
    if not target.exists():
        shutil.copy2(source, target)
    if implementation.v49.sha256_file(target) != expected:
        raise implementation.v49.FailClosed(f"v58 packaged dependency mismatch: {target}")


def prepare_formal_v58(*args: Any, **kwargs: Any) -> Path:
    cycle_root = Path(args[0])
    package = cycle_root / "formal/package"
    package.mkdir(parents=True, exist_ok=True)
    # Import names used by the two v58 adapters must be frozen in-package.
    _sealed_copy(ROBUST_V10, package / ROBUST_V10.name)
    _sealed_copy(FORMAL_V1, package / "job_formal_target10_batch_controller_v1.py")
    formal = _PREPARE_FORMAL_V57(*args, **kwargs)
    if implementation.v49.sha256_file(
        package / "run_job_round8_strict_serial.py"
    ) != EXPECTED[ROBUST_V11]:
        raise implementation.v49.FailClosed("v58 packaged formal runner mismatch")
    if implementation.v49.sha256_file(
        package / "job_formal_target10_batch_controller.py"
    ) != EXPECTED[FORMAL_V2]:
        raise implementation.v49.FailClosed("v58 packaged formal controller mismatch")
    return formal


def _validate_gradient_transaction(path: Path) -> None:
    value = implementation.v49.read_json(path)
    before = int(value.get("gradient_observation_count_before", -1))
    after = int(value.get("gradient_observation_count_after", -1))
    accepted_before = int(value.get("accepted_before", -1))
    accepted_after = int(value.get("accepted_after", -1))
    if (
        value.get("status") != "committed"
        or value.get("gradient_transaction_contract") != TRANSACTION_CONTRACT
        or value.get("gradient_state_contract") != GRADIENT_CONTRACT
        or before < 0
        or after - before != accepted_after - accepted_before
    ):
        raise implementation.v49.FailClosed(
            f"v58 formal transaction lacks complete gradient evidence: {path}"
        )


def resume_position_v58() -> tuple[Path, int]:
    package = implementation.FRESH_ROOT / "package"
    journal_path = package / "v57_to_v58_gradient_repair_transaction.json"
    audit_path = package / "v57_to_v58_gradient_repair_audit.json"
    if not journal_path.is_file() or not audit_path.is_file():
        raise implementation.v49.FailClosed("v58 zero-call migration audit is missing")
    journal = implementation.v49.read_json(journal_path)
    audit = implementation.v49.read_json(audit_path)
    if (
        journal.get("status") != "committed"
        or journal.get("contract") != REPAIR_CONTRACT
        or audit.get("status") != "complete"
        or audit.get("contract") != REPAIR_CONTRACT
        or journal.get("audit_sha256") != implementation.v49.sha256_file(audit_path)
        or int(journal.get("new_workload_calls", -1)) != 0
        or int(journal.get("counter_updates", -1)) != 0
    ):
        raise implementation.v49.FailClosed("v58 zero-call migration audit is invalid")
    latest, cycle = _RESUME_V57()
    transactions = sorted(
        implementation.FRESH_ROOT.glob(
            "cycle_[0-9][0-9][0-9]/formal/replay/formal_state_commit_transaction.json"
        )
    )
    if not transactions:
        raise implementation.v49.FailClosed("v58 has no repaired v57 formal transaction")
    for path in transactions:
        _validate_gradient_transaction(path)
    _state_sha, state = implementation.v49.state_pin()
    latest_transaction = implementation.v49.read_json(transactions[-1])
    if int(state.get("gradient_observation_count", -1)) != int(
        latest_transaction.get("gradient_observation_count_after", -2)
    ):
        raise implementation.v49.FailClosed("v58 latest gradient state/transaction mismatch")
    return latest, cycle


def require_v58_hashes() -> None:
    for path, expected in EXPECTED.items():
        if not path.is_file() or implementation.v49.sha256_file(path) != expected:
            raise implementation.v49.FailClosed(f"v58 sealed dependency SHA mismatch: {path}")


_REQUIRE_V57_HASHES = implementation.require_v50_hashes


def require_v58_and_v57_hashes() -> None:
    _REQUIRE_V57_HASHES()
    require_v58_hashes()


implementation.prepare_formal_v50 = prepare_formal_v58
implementation.resume_position_v50 = resume_position_v58
implementation.require_v50_hashes = require_v58_and_v57_hashes


if __name__ == "__main__":
    try:
        raise SystemExit(implementation.main())
    except implementation.v49.FailClosed as error:
        try:
            implementation.write_controller_state(status="fail_closed", error=str(error))
        except Exception:
            pass
        print(f"JOB target10 v58 fail closed: {error}", file=implementation.sys.stderr, flush=True)
        raise SystemExit(2)
