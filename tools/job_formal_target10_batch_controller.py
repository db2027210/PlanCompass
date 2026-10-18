#!/usr/bin/env python3
"""Run and commit one fail-closed JOB formal Top-5 continuation batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Mapping

import run_job_round8_strict_serial as strict_runner


PARAMETER_FIELDS = (
    "accepted_count",
    "incumbent_score",
    "incumbent_params",
    "range_by_parameter",
)

BATCH_PHYSICAL_INVOCATION_CAP = 5
FORMAL_REPLAY_CAP_ENVIRONMENT = (
    "RA_PG_FORMAL_SEARCH_MAX_REPLAYS",
    "RA_PG_FORMAL_SEARCH_ATTEMPT_SAFETY_CAP",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} must contain one JSON object")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_counter(path: Path, value: int) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(f"{value}\n", encoding="utf-8")
    os.replace(temporary, path)


def counter(path: Path) -> int:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw.isdigit():
        raise RuntimeError(f"invalid counter {path}: {raw!r}")
    return int(raw)


def require_gate(gate_path: Path) -> Dict[str, Any]:
    gate = read_json(gate_path)
    checks = gate.get("checks")
    if gate.get("passed") is not True or not isinstance(checks, dict):
        raise RuntimeError("JOB entry gate is not passed")
    if not checks or any(value is not True for value in checks.values()):
        raise RuntimeError("JOB entry gate has a failed condition")
    pins = gate.get("formal_counter_pins_before_start")
    if not isinstance(pins, dict):
        raise RuntimeError("JOB entry gate counter pins are missing")
    if gate.get("formal_accepted_total") != pins.get(
        "formal_search_accepted_count"
    ):
        raise RuntimeError("JOB gate accepted baseline/pin mismatch")
    return gate


def configure_formal_replay_cap(formal_replay_count: int) -> int:
    """Bind the frozen workload's cumulative cap to this sealed batch.

    The workload's historical fallback is 130, which is no longer valid after
    continuation batches.  Derive the cap exclusively from the entry-gate
    counter pin and the fixed per-batch physical-call cap.  Refuse conflicting
    caller-provided values so an ambient environment cannot widen the budget.
    """
    if formal_replay_count < 0:
        raise RuntimeError("formal replay count cannot be negative")
    maximum_replay_count = (
        formal_replay_count + BATCH_PHYSICAL_INVOCATION_CAP
    )
    expected = str(maximum_replay_count)
    for name in FORMAL_REPLAY_CAP_ENVIRONMENT:
        observed = os.environ.get(name)
        if observed is not None and observed != expected:
            raise RuntimeError(
                f"conflicting {name}: expected={expected} observed={observed}"
            )
    for name in FORMAL_REPLAY_CAP_ENVIRONMENT:
        os.environ[name] = expected
    return maximum_replay_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--selection-sha256", required=True)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--gate-sha256", required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--state-sha256", required=True)
    parser.add_argument("--authority-root", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--exclusion", type=Path, required=True)
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--global-counter", type=Path, required=True)
    parser.add_argument("--formal-replay-counter", type=Path, required=True)
    parser.add_argument("--accepted-counter", type=Path, required=True)
    args = parser.parse_args()

    for path, expected, label in (
        (args.selection, args.selection_sha256, "selection"),
        (args.gate, args.gate_sha256, "gate"),
        (args.state, args.state_sha256, "state"),
    ):
        if not path.is_file() or sha256_file(path) != expected:
            raise RuntimeError(f"{label} SHA mismatch")
    gate = require_gate(args.gate)
    state_before = read_json(args.state)
    pins = gate["formal_counter_pins_before_start"]
    observed_before = {
        "global_ordinal": counter(args.global_counter),
        "formal_search_replay_count": counter(args.formal_replay_counter),
        "formal_search_accepted_count": counter(args.accepted_counter),
    }
    for name, observed in observed_before.items():
        if observed != int(pins[name]):
            raise RuntimeError(
                f"counter pin mismatch: {name} expected={pins[name]} "
                f"observed={observed}"
            )
    if int(state_before.get("accepted_count", -1)) != observed_before[
        "formal_search_accepted_count"
    ]:
        raise RuntimeError("state/accepted counter mismatch before batch")
    if args.replay_root.exists():
        raise RuntimeError("replay root already exists")

    formal_replay_cap = configure_formal_replay_cap(
        observed_before["formal_search_replay_count"]
    )

    result = strict_runner.run(
        SimpleNamespace(
            selection=args.selection,
            replay_root=args.replay_root,
            authority_root=args.authority_root,
            observations=args.observations,
            exclusion=args.exclusion,
            state=args.state,
            accepted_target=10,
            diagnostic_only=False,
        )
    )
    if result.get("baseline_params_restored") is not True:
        raise RuntimeError("strict runner did not restore baseline")
    physical = int(result.get("physical_invocation_count", -1))
    successful_count = int(result.get("successful_real_replay_count", -1))
    technical_count = int(result.get("technical_failure_count", -1))
    budget_attempts = int(
        result.get(
            "batch_budget_attempt_count",
            successful_count + technical_count,
        )
    )
    if (
        physical < 0
        or physical > BATCH_PHYSICAL_INVOCATION_CAP
        or successful_count < 0
        or technical_count < 0
        or budget_attempts != successful_count + technical_count
        or budget_attempts > BATCH_PHYSICAL_INVOCATION_CAP
        or successful_count > physical
        or physical > budget_attempts
    ):
        raise RuntimeError("formal batch physical-call cap violation")
    final_subset = result.get("final_state")
    if not isinstance(final_subset, dict):
        raise RuntimeError("strict runner final state missing")
    accepted_after = int(final_subset.get("accepted_count", -1))
    accepted_before = int(state_before["accepted_count"])
    if accepted_after < accepted_before or accepted_after > 10:
        raise RuntimeError("invalid accepted transition")
    accepted_delta = accepted_after - accepted_before

    global_after = counter(args.global_counter)
    formal_replay_after = counter(args.formal_replay_counter)
    accepted_counter_before_commit = counter(args.accepted_counter)
    if global_after - observed_before["global_ordinal"] != physical:
        raise RuntimeError("global ordinal did not advance by physical calls")
    observed_ordinals = [
        int(item["measurement_protocol"]["replay_ordinal"])
        for item in result.get("successful") or []
        if isinstance(item, dict)
        and isinstance(item.get("measurement_protocol"), dict)
        and item["measurement_protocol"].get("replay_ordinal") is not None
    ]
    if (
        len(observed_ordinals) != len(set(observed_ordinals))
        or any(
            ordinal <= observed_before["global_ordinal"]
            or ordinal > global_after
            for ordinal in observed_ordinals
        )
    ):
        raise RuntimeError("workload replay ordinal audit is inconsistent")
    if formal_replay_after - observed_before["formal_search_replay_count"] != physical:
        raise RuntimeError("formal replay counter did not advance by physical calls")
    if accepted_counter_before_commit != accepted_before:
        raise RuntimeError("accepted counter changed outside controller")

    committed = dict(state_before)
    for field in PARAMETER_FIELDS:
        committed[field] = final_subset[field]
    committed["global_ordinal"] = global_after
    committed["formal_replay_count"] = formal_replay_after
    committed["formal_valid_replay_count"] = (
        int(state_before.get("formal_valid_replay_count", 0))
        + successful_count
    )
    committed["snapshot_only"] = False
    committed["last_formal_batch"] = {
        "accepted_before": accepted_before,
        "accepted_after": accepted_after,
        "accepted_delta": accepted_delta,
        "physical_calls": physical,
        "valid_replays": successful_count,
        "technical_failures": technical_count,
        "batch_budget_attempts": budget_attempts,
        "pre_invocation_technical_failures": int(
            result.get("pre_invocation_technical_failure_count", 0)
        ),
        "replay_root": str(args.replay_root.resolve()),
        "selection_sha256": args.selection_sha256,
    }

    transaction = args.replay_root / "formal_state_commit_transaction.json"
    atomic_json(
        transaction,
        {
            "status": "prepared",
            "accepted_before": accepted_before,
            "accepted_after": accepted_after,
            "state_sha256_before": args.state_sha256,
            "global_ordinal_after": global_after,
            "formal_replay_count_after": formal_replay_after,
            "formal_replay_cap": formal_replay_cap,
        },
    )
    atomic_counter(args.accepted_counter, accepted_after)
    atomic_json(args.state, committed)
    atomic_json(
        transaction,
        {
            "status": "committed",
            "accepted_before": accepted_before,
            "accepted_after": accepted_after,
            "accepted_counter_sha256_after": sha256_file(args.accepted_counter),
            "state_sha256_after": sha256_file(args.state),
            "global_ordinal_after": global_after,
            "formal_replay_count_after": formal_replay_after,
            "formal_replay_cap": formal_replay_cap,
        },
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "accepted_before": accepted_before,
                "accepted_after": accepted_after,
                "accepted_delta": accepted_delta,
                "physical_calls": physical,
                "valid_replays": result.get("successful_real_replay_count"),
                "technical_failures": result.get("technical_failure_count"),
                "baseline_restored": True,
                "formal_replay_cap": formal_replay_cap,
                "qps_used_for_inner_scoring": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
