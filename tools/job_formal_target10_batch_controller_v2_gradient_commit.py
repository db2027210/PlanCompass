#!/usr/bin/env python3
"""JOB v58 formal controller adapter committing the complete gradient state."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

import job_formal_target10_batch_controller_v1 as v1


GRADIENT_FIELD = "gradient_observation_count"
GRADIENT_CONTRACT = "job_v58_complete_gradient_state_transition_v1"
TRANSACTION_CONTRACT = "job_v58_gradient_state_commit_transaction_v1"
v1.PARAMETER_FIELDS = (*v1.PARAMETER_FIELDS, GRADIENT_FIELD)
_ORIGINAL_ATOMIC_JSON = v1.atomic_json
_GRADIENT_EVIDENCE_CACHE: dict[str, Any] | None = None


def _argument_path(name: str) -> Path:
    try:
        index = sys.argv.index(name)
        value = sys.argv[index + 1]
    except (ValueError, IndexError) as error:
        raise RuntimeError(f"v58 controller argument is missing: {name}") from error
    return Path(value).resolve()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"v58 expected one JSON object: {path}")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gradient_evidence() -> dict[str, Any]:
    global _GRADIENT_EVIDENCE_CACHE
    if _GRADIENT_EVIDENCE_CACHE is not None:
        return copy.deepcopy(_GRADIENT_EVIDENCE_CACHE)
    state_path = _argument_path("--state")
    replay_root = _argument_path("--replay-root")
    summary_path = replay_root / "summary.json"
    if not state_path.is_file() or not summary_path.is_file():
        raise RuntimeError("v58 gradient commit evidence is incomplete")
    state = _load(state_path)
    summary = _load(summary_path)
    final_state = summary.get("final_state")
    if (
        summary.get("complete_gradient_state_transition_guard") != "passed"
        or summary.get("gradient_state_contract") != GRADIENT_CONTRACT
        or not isinstance(final_state, Mapping)
        or GRADIENT_FIELD not in state
        or GRADIENT_FIELD not in final_state
    ):
        raise RuntimeError("v58 complete-gradient runner guard is absent")
    before = int(state[GRADIENT_FIELD])
    after = int(final_state[GRADIENT_FIELD])
    accepted_before = int(state["accepted_count"])
    accepted_after = int(final_state["accepted_count"])
    if before < 0 or after < before:
        raise RuntimeError("v58 gradient count transition is invalid")
    if after - before != accepted_after - accepted_before:
        raise RuntimeError("v58 gradient delta differs from accepted delta")
    evidence = {
        "before": before,
        "after": after,
        "delta": after - before,
        "state_sha256_before": _sha(state_path),
        "summary_path": str(summary_path),
        "summary_sha256": _sha(summary_path),
    }
    _GRADIENT_EVIDENCE_CACHE = copy.deepcopy(evidence)
    return evidence


def _guarded_atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    replay_root = _argument_path("--replay-root")
    transaction_path = replay_root / "formal_state_commit_transaction.json"
    state_path = _argument_path("--state")
    payload = copy.deepcopy(dict(value))
    evidence = _gradient_evidence()
    if path.resolve() == state_path:
        if int(payload.get(GRADIENT_FIELD, -1)) != int(evidence["after"]):
            raise RuntimeError("v58 state commit omits the proven gradient count")
    if path.resolve() == transaction_path:
        payload.update(
            {
                "gradient_observation_count_before": evidence["before"],
                "gradient_observation_count_after": evidence["after"],
                "gradient_observation_count_delta": evidence["delta"],
                "gradient_summary_sha256": evidence["summary_sha256"],
                "gradient_state_contract": GRADIENT_CONTRACT,
                "gradient_transaction_contract": TRANSACTION_CONTRACT,
                "state_sha256_before": evidence["state_sha256_before"],
            }
        )
        if payload.get("status") == "committed":
            state = _load(state_path)
            if int(state.get(GRADIENT_FIELD, -1)) != int(evidence["after"]):
                raise RuntimeError("v58 committed state has the wrong gradient count")
            payload["gradient_state_sha256_after"] = _sha(state_path)
    _ORIGINAL_ATOMIC_JSON(path, payload)


def _validate_after_commit() -> None:
    state_path = _argument_path("--state")
    replay_root = _argument_path("--replay-root")
    accepted_counter = _argument_path("--accepted-counter")
    transaction_path = replay_root / "formal_state_commit_transaction.json"
    state = _load(state_path)
    transaction = _load(transaction_path)
    evidence = _gradient_evidence()
    accepted_raw = accepted_counter.read_text(encoding="utf-8").strip()
    if (
        transaction.get("status") != "committed"
        or transaction.get("gradient_transaction_contract") != TRANSACTION_CONTRACT
        or transaction.get("gradient_state_contract") != GRADIENT_CONTRACT
        or int(transaction.get("gradient_observation_count_before", -1))
        != int(evidence["before"])
        or int(transaction.get("gradient_observation_count_after", -1))
        != int(evidence["after"])
        or int(transaction.get("gradient_observation_count_delta", -1))
        != int(evidence["delta"])
        or int(state.get(GRADIENT_FIELD, -1)) != int(evidence["after"])
        or transaction.get("state_sha256_before")
        != evidence["state_sha256_before"]
        or transaction.get("state_sha256_after") != _sha(state_path)
        or transaction.get("gradient_state_sha256_after") != _sha(state_path)
        or not accepted_raw.isdigit()
        or int(accepted_raw) != int(state.get("accepted_count", -1))
    ):
        raise RuntimeError("v58 after-commit gradient verification failed")


def main() -> int:
    global _GRADIENT_EVIDENCE_CACHE
    _GRADIENT_EVIDENCE_CACHE = None
    v1.atomic_json = _guarded_atomic_json
    try:
        result = int(v1.main())
    finally:
        v1.atomic_json = _ORIGINAL_ATOMIC_JSON
    if result != 0:
        raise RuntimeError(f"v58 base formal controller returned {result}")
    _validate_after_commit()
    print(
        json.dumps(
            {
                "status": "v58_gradient_commit_verified",
                "new_workload_calls": 0,
                "gradient_transaction_contract": TRANSACTION_CONTRACT,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
