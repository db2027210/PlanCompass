#!/usr/bin/env python3
"""JOB v63: resume one sealed repeat at the exact zero-call boundary."""

from __future__ import annotations

import hashlib
from pathlib import Path
import time
from typing import Any, Mapping

import job_fresh10_target10_autonomous_v62 as v62


implementation = v62.implementation
EXPECTED_V62_SHA256 = "748ba44a28c446a8510ca1463750b1db3c7a9674f46cd9d4d0876c3c2c021a2a"
EXPECTED_FAILED_CONTROLLER_STATE_SHA256 = (
    "819845b7689665bb4d7e4d73ea2f149a590667fad0b4beff4323b527260bb841"
)
EXPECTED_REPEAT_SELECTION_SHA256 = (
    "9ef43934cb9d01047690f1ccc0414f5eb8a1f09a3807c264712450da9d4b15a1"
)
EXPECTED_AUTHORITY_BRIDGE_SHA256 = (
    "b61f0102cde225af27bd9b9fc308282673f0c35e88441979b2de2a93fac1e637"
)
EXPECTED_FORMAL_STATE_SHA256 = (
    "8ee73ee31883f0362353ec0e03dc665ef113f91510269a07b6f28109cfda62a7"
)

if hashlib.sha256(Path(v62.__file__).read_bytes()).hexdigest() != EXPECTED_V62_SHA256:
    raise implementation.v49.FailClosed("v63 sealed v62 controller SHA mismatch")

CONTROLLER_VERSION = "v63"
PREWORKLOAD_RESUME_CONTRACT = "job_v63_exact_repeat_preworkload_resume_once_v1"
CONTROLLER_RECOVERY_CONTRACT = "job_v63_zero_call_partial_repeat_resume_v1"

_REQUIRE_V62_HASHES = implementation.require_v50_hashes
_RESUME_V60 = v62._RESUME_V60

implementation.CONTROLLER_VERSION = CONTROLLER_VERSION


def _repeat_root() -> Path:
    return implementation.v49.ROOT / "cycle_002/repeat_01"


def _exact_authority() -> dict[str, Any]:
    authority = v62._authority_pin()
    if (
        authority["formal_state_sha256"] != EXPECTED_FORMAL_STATE_SHA256
        or int(authority["accepted_count"]) != 1
        or authority["counters"]
        != {"global": 429, "formal_replay": 211, "accepted": 1}
    ):
        raise implementation.v49.FailClosed("v63 live authority is not the sealed 429/211/1 state")
    return authority


def authorize_repeat_preworkload_resume_v63() -> dict[str, Any]:
    """Seal the only legal no-summary phase as a zero-physical-call boundary."""
    cycle_root = implementation.v49.ROOT / "cycle_002"
    phase = _repeat_root()
    bridge_path = cycle_root / "v62_v61_clean5_authority_bridge.json"
    selection = phase / "top5.json"
    second = phase / "top5.second.json"
    counters_path = phase / "counters_before.json"
    sequence_path = phase / "v50_batch_sequence.json"
    summary_path = phase / "replay/summary.json"
    audit_path = phase / "diagnostic_audit.json"
    replay_path = phase / "replay"
    authorization_path = phase / "v63_preworkload_resume_authorization.json"

    required = (bridge_path, selection, second, counters_path, sequence_path)
    if not all(path.is_file() for path in required):
        raise implementation.v49.FailClosed("v63 sealed repeat preworkload evidence is incomplete")
    if implementation.v49.sha256_file(bridge_path) != EXPECTED_AUTHORITY_BRIDGE_SHA256:
        raise implementation.v49.FailClosed("v63 clean5 authority bridge SHA mismatch")
    if implementation.v49.sha256_file(selection) != EXPECTED_REPEAT_SELECTION_SHA256:
        raise implementation.v49.FailClosed("v63 repeat_01 Top5 SHA mismatch")
    if selection.read_bytes() != second.read_bytes():
        raise implementation.v49.FailClosed("v63 repeat_01 deterministic Top5 pair changed")
    if summary_path.exists() or audit_path.exists() or replay_path.exists():
        raise implementation.v49.FailClosed(
            "v63 repeat_01 is no longer at the exact preworkload boundary"
        )

    bridge = implementation.v49.read_json(bridge_path)
    counters = implementation.v49.read_json(counters_path)
    sequence = implementation.v49.read_json(sequence_path)
    authority = _exact_authority()
    expected_error = (
        "partial diagnostic has no sealed summary; refusing replay: "
        f"{phase.resolve()}"
    )
    if (
        bridge.get("contract") != v62.AUTHORITY_BRIDGE_CONTRACT
        or bridge.get("status") != "sealed_state_neutral_transition"
        or bridge.get("repeat_selection_path") != str(selection.resolve())
        or bridge.get("repeat_selection_sha256") != EXPECTED_REPEAT_SELECTION_SHA256
        or bridge.get("authority_after") != authority
        or int(bridge.get("physical_transition", -1)) != 5
        or int(bridge.get("accepted_delta", -1)) != 0
        or int(bridge.get("formal_state_updates", -1)) != 0
        or int(bridge.get("new_workload_calls", -1)) != 0
        or counters != authority["counters"]
        or int(sequence.get("sequence", -1)) <= 0
        or Path(str(sequence.get("phase_root") or "")).resolve() != phase.resolve()
    ):
        raise implementation.v49.FailClosed("v63 repeat_01 authority binding is invalid")

    runner_log = phase / "runner.log"
    value = {
        "schema_version": "job_v63_preworkload_resume_authorization_v1",
        "status": "sealed_zero_physical_ready_for_one_runner_boundary",
        "contract": PREWORKLOAD_RESUME_CONTRACT,
        "phase_root": str(phase.resolve()),
        "selection_path": str(selection.resolve()),
        "selection_sha256": EXPECTED_REPEAT_SELECTION_SHA256,
        "deterministic_second_sha256": implementation.v49.sha256_file(second),
        "authority_bridge_path": str(bridge_path.resolve()),
        "authority_bridge_sha256": EXPECTED_AUTHORITY_BRIDGE_SHA256,
        "counters_before_path": str(counters_path.resolve()),
        "counters_before_sha256": implementation.v49.sha256_file(counters_path),
        "batch_sequence_path": str(sequence_path.resolve()),
        "batch_sequence_sha256": implementation.v49.sha256_file(sequence_path),
        "authority": authority,
        "physical_calls_before_resume": 0,
        "physical_call_derivation": "live_global_equals_sealed_counters_before_global",
        "summary_absent": True,
        "diagnostic_audit_absent": True,
        "replay_directory_absent": True,
        "runner_log_path": str(runner_log.resolve()) if runner_log.exists() else None,
        "runner_log_sha256": (
            implementation.v49.sha256_file(runner_log) if runner_log.is_file() else None
        ),
        "same_selection_reuse_only": True,
        "runner_launch_permitted_once": True,
        "retry_after_replay_directory_creation": False,
        "controller_failure_expected_error": expected_error,
        "new_workload_calls": 0,
        "formal_state_updates": 0,
        "counter_updates": 0,
        "selection_rebuilt": False,
        "selection_quarantined": False,
        "discovery_repeated": False,
    }
    if authorization_path.is_file():
        if implementation.v49.read_json(authorization_path) != value:
            raise implementation.v49.FailClosed("v63 repeat resume authorization changed")
    else:
        implementation.v49.atomic_json(authorization_path, value)
    return value


def validate_pending_diagnostic_counters_v63(
    latest_formal: Path,
    first_cycle: int,
) -> None:
    """Retain v50 ordering, adding only the exact v63 zero-call boundary."""
    v49 = implementation.v49
    _state_sha, state = v49.state_pin()
    running_global = int(state.get("global_ordinal", -1))
    formal_replay = int(state.get("formal_replay_count", -1))
    accepted = int(state.get("accepted_count", -1))
    base = implementation.HISTORICAL_INITIAL_FORMAL
    latest_cycle = (
        0 if latest_formal == base else int(latest_formal.parent.name.rsplit("_", 1)[-1])
    )
    roots = [
        v49.ROOT / f"cycle_{ordinal:03d}"
        for ordinal in range(latest_cycle + 1, first_cycle + 1)
        if (v49.ROOT / f"cycle_{ordinal:03d}").is_dir()
    ]
    pending = []
    zero_boundary_count = 0
    for root in roots:
        for before_path in root.rglob("counters_before.json"):
            phase = before_path.parent
            before = v49.read_json(before_path)
            summary_path = phase / "replay/summary.json"
            audit_path = phase / "diagnostic_audit.json"
            if summary_path.is_file():
                summary = v49.read_json(summary_path)
                physical = int(summary.get("physical_invocation_count", -1))
                if not (0 <= physical <= 5):
                    raise v49.FailClosed(f"diagnostic physical count is invalid: {phase}")
                if audit_path.is_file():
                    sealed = v49.read_json(audit_path)
                    if sealed.get("summary_sha256") != v49.sha256_file(summary_path):
                        raise v49.FailClosed(f"diagnostic summary SHA mismatch: {phase}")
            elif phase.resolve() == _repeat_root().resolve():
                authorization = authorize_repeat_preworkload_resume_v63()
                if authorization["counters_before_sha256"] != v49.sha256_file(before_path):
                    raise v49.FailClosed("v63 repeat counters binding changed")
                physical = 0
                zero_boundary_count += 1
            else:
                raise v49.FailClosed(
                    f"partial diagnostic has no sealed summary; refusing replay: {phase}"
                )
            sequence_path = phase / "v50_batch_sequence.json"
            sequence = (
                int(v49.read_json(sequence_path).get("sequence", -1))
                if sequence_path.is_file()
                else None
            )
            pending.append((phase, before, physical, sequence))
    if zero_boundary_count != 1:
        raise v49.FailClosed("v63 requires exactly one sealed zero-call repeat boundary")

    unused = list(pending)
    sequenced = [item for item in pending if item[3] is not None]
    if sequenced:
        if len(sequenced) != len(pending):
            legacy = sorted(
                (item for item in pending if item[3] is None),
                key=lambda item: int(item[1].get("global", -1)),
            )
            unused = [*legacy, *sorted(sequenced, key=lambda item: int(item[3]))]
        else:
            unused = sorted(sequenced, key=lambda item: int(item[3]))
        sequences = [int(item[3]) for item in sequenced]
        if len(set(sequences)) != len(sequences):
            raise v49.FailClosed("duplicate v50 diagnostic batch sequence")
        for phase, before, physical, _sequence in unused:
            if (
                int(before.get("global", -1)) != running_global
                or int(before.get("formal_replay", -1)) != formal_replay
                or int(before.get("accepted", -1)) != accepted
            ):
                raise v49.FailClosed(f"sequenced diagnostic counter chain mismatch: {phase}")
            running_global += int(physical)
        unused = []
    while unused:
        matches = [
            item
            for item in unused
            if int(item[1].get("global", -1)) == running_global
            and int(item[1].get("formal_replay", -1)) == formal_replay
            and int(item[1].get("accepted", -1)) == accepted
        ]
        if len(matches) != 1:
            raise v49.FailClosed("interleaved diagnostic counter chain is ambiguous or broken")
        selected = matches[0]
        unused.remove(selected)
        running_global += int(selected[2])
    if (
        running_global != v49.counter(v49.GLOBAL_COUNTER)
        or formal_replay != v49.counter(v49.FORMAL_REPLAY_COUNTER)
        or accepted != v49.counter(v49.ACCEPTED_COUNTER)
    ):
        raise v49.FailClosed("pending diagnostic/global counter accounting mismatch")


def recover_controller_state_v63() -> dict[str, Any]:
    authorization = authorize_repeat_preworkload_resume_v63()
    state_path = implementation.v49.ROOT / "controller_state.json"
    current = implementation.v49.read_json(state_path)
    if (
        current.get("schema_version") == "job_fresh10_target10_autonomous_v63"
        and current.get("status") == "ready_resume"
    ):
        if current.get("resume_authorization_sha256") != implementation.v49.sha256_file(
            _repeat_root() / "v63_preworkload_resume_authorization.json"
        ):
            raise implementation.v49.FailClosed("v63 recovered controller state changed")
        return current

    original = state_path.read_bytes()
    original_sha = hashlib.sha256(original).hexdigest()
    if (
        original_sha != EXPECTED_FAILED_CONTROLLER_STATE_SHA256
        or current.get("schema_version") != "job_fresh10_target10_autonomous_v62"
        or current.get("status") != "fail_closed"
        or current.get("error") != authorization["controller_failure_expected_error"]
    ):
        raise implementation.v49.FailClosed("v63 controller state is not the exact v62 failure")
    backup = state_path.with_name(
        f"controller_state.json.pre_v63.{int(time.time())}.{original_sha[:12]}.bak"
    )
    if backup.exists():
        raise implementation.v49.FailClosed("v63 controller state backup collision")
    backup.write_bytes(original)
    if hashlib.sha256(backup.read_bytes()).hexdigest() != original_sha:
        raise implementation.v49.FailClosed("v63 controller state backup SHA mismatch")

    authority = _exact_authority()
    authorization_path = _repeat_root() / "v63_preworkload_resume_authorization.json"
    recovered = {
        "schema_version": "job_fresh10_target10_autonomous_v63",
        "status": "ready_resume",
        "target_accepted": implementation.v49.TARGET_ACCEPTED,
        "accepted_count": 1,
        "cycle": 2,
        "phase": "repeat_gate",
        "formal_state_sha256": authority["formal_state_sha256"],
        "controller_recovery_contract": CONTROLLER_RECOVERY_CONTRACT,
        "resume_authorization_path": str(authorization_path.resolve()),
        "resume_authorization_sha256": implementation.v49.sha256_file(authorization_path),
        "reused_repeat_selection_path": authorization["selection_path"],
        "reused_repeat_selection_sha256": authorization["selection_sha256"],
        "source_fail_closed_state_sha256": original_sha,
        "source_fail_closed_state_backup": str(backup.resolve()),
        "new_workload_calls": 0,
        "formal_state_updates": 0,
        "counter_updates": 0,
        "selection_rebuilt": False,
        "selection_quarantined": False,
        "discovery_repeated": False,
        "qps_used_for_inner_scoring": False,
        "manual_candidate_injection": False,
    }
    implementation.v49.atomic_json(state_path, recovered)
    return recovered


def resume_position_v63() -> tuple[Path, int]:
    v62.install_live_network_preflight(
        implementation.v49.ROOT / "preflights/v63_controller_resume"
    )
    recover_controller_state_v63()
    return _RESUME_V60()


def require_v63_hashes() -> None:
    v62.install_live_network_preflight(
        implementation.v49.ROOT / "preflights/v63_controller_require"
    )
    _REQUIRE_V62_HASHES()


implementation.validate_pending_diagnostic_counters_v50 = (
    validate_pending_diagnostic_counters_v63
)
implementation.v49.run_logged = v62.run_logged_v62
implementation.resume_position_v50 = resume_position_v63
implementation.require_v50_hashes = require_v63_hashes


if __name__ == "__main__":
    try:
        raise SystemExit(implementation.main())
    except implementation.v49.FailClosed as error:
        try:
            implementation.write_controller_state(status="fail_closed", error=str(error))
        except Exception:
            pass
        print(f"JOB target10 v63 fail closed: {error}", flush=True)
        raise SystemExit(2)
