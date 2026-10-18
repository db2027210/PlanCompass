#!/usr/bin/env python3
"""JOB v59 restart-safe network remediation after sealed v58 preclaim failures."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

import job_fresh10_target10_autonomous_v58 as v58


implementation = v58.implementation
EXPECTED_V58_SHA256 = "8ed24530017d866c1c9ca3ce31e5d4e69a1c7083b6237827c274c96093623a30"
if hashlib.sha256(Path(v58.__file__).read_bytes()).hexdigest() != EXPECTED_V58_SHA256:
    raise implementation.v49.FailClosed("v59 sealed v58 controller SHA mismatch")

NETWORK_NAME = "ra_job_v53_net"
BRIDGE_INTERFACE = "ra-job-v53"
NETWORK_SIGNATURE = "invalid_or_prohibited_RA_JOB_DOCKER_NETWORK"
NETWORK_QUARANTINE_CONTRACT = "job_v59_v58_network_preclaim_quarantine_v1"
NETWORK_PREFLIGHT_CONTRACT = "job_v59_dedicated_bridge_preflight_v1"
REPEATED_TECHNICAL_SIGNATURE_LIMIT = 3
HISTORICAL_NETWORK_ATTEMPTS = frozenset({1, 2, 3})

_RESUME_V58 = implementation.resume_position_v50
_GENERATE_V58 = implementation.generate_fresh_discovery
_RUN_DIAGNOSTIC_V58 = implementation.run_diagnostic_v50
_REQUIRE_V58_HASHES = implementation.require_v50_hashes

implementation.CONTROLLER_VERSION = "v59"


def _canonical_sha(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _network_preflight() -> dict[str, Any]:
    path_raw = os.environ.get("RA_JOB_NETWORK_PREFLIGHT_JSON")
    expected_sha = os.environ.get("RA_JOB_NETWORK_PREFLIGHT_SHA256")
    if (
        os.environ.get("RA_JOB_DOCKER_NETWORK") != NETWORK_NAME
        or not path_raw
        or not expected_sha
    ):
        raise implementation.v49.FailClosed("v59 dedicated network environment is absent")
    path = Path(path_raw).resolve()
    if not path.is_file() or implementation.v49.sha256_file(path) != expected_sha:
        raise implementation.v49.FailClosed("v59 network preflight SHA mismatch")
    value = implementation.v49.read_json(path)
    inspect_path_raw = value.get("network_inspect_path")
    inspect_sha = value.get("network_inspect_sha256")
    if not inspect_path_raw or not inspect_sha:
        raise implementation.v49.FailClosed("v59 network inspect binding is absent")
    inspect_path = Path(str(inspect_path_raw)).resolve()
    if (
        not inspect_path.is_file()
        or implementation.v49.sha256_file(inspect_path) != inspect_sha
    ):
        raise implementation.v49.FailClosed("v59 network inspect binding changed")
    inspect_value = json.loads(inspect_path.read_text(encoding="utf-8"))
    if not isinstance(inspect_value, list) or len(inspect_value) != 1:
        raise implementation.v49.FailClosed("v59 network inspect shape is invalid")
    network = inspect_value[0]
    if not isinstance(network, Mapping):
        raise implementation.v49.FailClosed("v59 network inspect entry is invalid")
    options = network.get("Options") or {}
    containers = network.get("Containers") or {}
    if (
        network.get("Name") != NETWORK_NAME
        or network.get("Driver") != "bridge"
        or network.get("Scope") != "local"
        or network.get("Internal") is not False
        or not isinstance(containers, Mapping)
        or len(containers) != 0
        or not isinstance(options, Mapping)
        or options.get("com.docker.network.bridge.name") != BRIDGE_INTERFACE
    ):
        raise implementation.v49.FailClosed("v59 raw network inspect contract failed")
    state_sha, state = implementation.v49.state_pin()
    if (
        value.get("contract") != NETWORK_PREFLIGHT_CONTRACT
        or value.get("network_name") != NETWORK_NAME
        or value.get("driver") != "bridge"
        or value.get("scope") != "local"
        or value.get("internal") is not False
        or value.get("bridge_interface") != BRIDGE_INTERFACE
        or int(value.get("attached_container_count", -1)) != 0
        or value.get("formal_state_sha256") != state_sha
        or int(value.get("accepted_count", -1)) != int(state["accepted_count"])
        or int(value.get("global_counter", -1))
        != implementation.v49.counter(implementation.v49.GLOBAL_COUNTER)
        or int(value.get("formal_replay_counter", -1))
        != implementation.v49.counter(implementation.v49.FORMAL_REPLAY_COUNTER)
        or int(value.get("accepted_counter", -1))
        != implementation.v49.counter(implementation.v49.ACCEPTED_COUNTER)
    ):
        raise implementation.v49.FailClosed("v59 dedicated network preflight contract failed")
    return value


def _signature(summary: Mapping[str, Any], phase_root: Path) -> str:
    text = json.dumps(summary, sort_keys=True)
    for path in sorted(phase_root.rglob("*.log")):
        if path.is_file():
            text += "\n" + path.read_text(encoding="utf-8", errors="replace")
    lowered = text.lower()
    if (
        "invalid or prohibited ra_job_docker_network" in lowered
        or "invalid or prohibited ra_job_docker_network" in lowered.replace("-", "_")
    ):
        return NETWORK_SIGNATURE
    failures = summary.get("technical_failures") or []
    compact = [
        {
            "error": item.get("error"),
            "failure": item.get("failure"),
            "workload_returncode": item.get("workload_returncode"),
            "preclaim_no_ordinal_claim": item.get("preclaim_no_ordinal_claim"),
        }
        for item in failures
        if isinstance(item, Mapping)
    ]
    return "technical-sha256:" + _canonical_sha(compact)


def _attempt(phase_root: Path) -> int:
    try:
        return int(phase_root.name.rsplit("_", 1)[-1])
    except (ValueError, IndexError) as error:
        raise implementation.v49.FailClosed(
            f"v59 diagnostic root has no attempt ordinal: {phase_root}"
        ) from error


def _network_quarantine_path(cycle_root: Path) -> Path:
    return cycle_root / "v59_v58_network_preclaim_quarantine.json"


def _validate_network_quarantine(value: Mapping[str, Any], cycle_root: Path) -> None:
    state_sha = str(value.get("formal_state_sha256") or "")
    fingerprints = value.get("quarantined_fingerprints") or []
    batches = value.get("batches") or []
    if (
        value.get("status") != "quarantined_resolved_by_v59_network_contract"
        or value.get("contract") != NETWORK_QUARANTINE_CONTRACT
        or value.get("technical_signature") != NETWORK_SIGNATURE
        or int(value.get("repeated_signature_count", -1)) != 3
        or int(value.get("physical_workload_calls", -1)) != 0
        or int(value.get("ordinal_claims", -1)) != 0
        or int(value.get("valid_replays", -1)) != 0
        or int(value.get("accepted_added", -1)) != 0
        or value.get("formal_state_mutated") is not False
        or value.get("retry_same_fingerprint_allowed") is not False
        or not state_sha
        or not isinstance(batches, list)
        or len(batches) != 3
        or not isinstance(fingerprints, list)
        or len(fingerprints) != 15
        or len(set(fingerprints)) != 15
    ):
        raise implementation.v49.FailClosed("v59 network quarantine contract is invalid")
    for item in batches:
        if not isinstance(item, Mapping):
            raise implementation.v49.FailClosed("v59 network quarantine batch is malformed")
        attempt = int(item.get("attempt", -1))
        root = cycle_root / f"discovery_replay_{attempt:02d}"
        selection = root / "top5.json"
        summary = root / "replay/summary.json"
        audit = root / "diagnostic_audit.json"
        if (
            attempt not in HISTORICAL_NETWORK_ATTEMPTS
            or item.get("selection_sha256")
            != implementation.v49.sha256_file(selection)
            or item.get("summary_sha256") != implementation.v49.sha256_file(summary)
            or item.get("diagnostic_audit_sha256")
            != implementation.v49.sha256_file(audit)
            or item.get("events_sha256")
            != implementation.v49.sha256_file(root / "replay/events.jsonl")
            or item.get("counters_before_sha256")
            != implementation.v49.sha256_file(root / "counters_before.json")
        ):
            raise implementation.v49.FailClosed("v59 network quarantine binding changed")


def ensure_network_quarantine(cycle_root: Path) -> dict[str, Any]:
    output = _network_quarantine_path(cycle_root)
    if output.is_file():
        value = implementation.v49.read_json(output)
        _validate_network_quarantine(value, cycle_root)
        return value
    state_sha, state = implementation.v49.state_pin()
    batches = []
    fingerprints: list[str] = []
    counter_pin = None
    for attempt in sorted(HISTORICAL_NETWORK_ATTEMPTS):
        root = cycle_root / f"discovery_replay_{attempt:02d}"
        selection_path = root / "top5.json"
        summary_path = root / "replay/summary.json"
        audit_path = root / "diagnostic_audit.json"
        counters_path = root / "counters_before.json"
        if not all(
            path.is_file()
            for path in (selection_path, summary_path, audit_path, counters_path)
        ):
            raise implementation.v49.FailClosed(
                f"v59 historical network failure is incomplete: {root}"
            )
        implementation.v49.validate_top5(
            selection_path,
            roles=("probe",) * 5,
            diagnostic_only=True,
        )
        summary = implementation.v49.read_json(summary_path)
        audit = implementation.v49.read_json(audit_path)
        counters = implementation.v49.read_json(counters_path)
        selected = sorted(implementation.v49.fingerprint_set(selection_path))
        if (
            len(selected) != 5
            or int(summary.get("physical_invocation_count", -1)) != 0
            or int(summary.get("successful_real_replay_count", -1)) != 0
            or int(summary.get("technical_failure_count", -1)) != 1
            or int(summary.get("pre_invocation_technical_failure_count", -1)) != 1
            or summary.get("diagnostic_only") is not True
            or summary.get("baseline_params_restored") is not True
            or summary.get("initial_state") != summary.get("final_state")
            or audit.get("status") != "technical_state_neutral"
            or int(audit.get("physical_calls", -1)) != 0
            or int(audit.get("accepted_added", -1)) != 0
            or audit.get("formal_state_sha256") != state_sha
            or _signature(summary, root) != NETWORK_SIGNATURE
        ):
            raise implementation.v49.FailClosed(
                f"v59 historical network preclaim contract failed: {root}"
            )
        normalized_counters = {
            "global": int(counters.get("global", -1)),
            "formal_replay": int(counters.get("formal_replay", -1)),
            "accepted": int(counters.get("accepted", -1)),
        }
        if counter_pin is None:
            counter_pin = normalized_counters
        elif normalized_counters != counter_pin:
            raise implementation.v49.FailClosed(
                "v59 zero-claim network batches changed counters"
            )
        fingerprints.extend(selected)
        batches.append(
            {
                "attempt": attempt,
                "selection_sha256": implementation.v49.sha256_file(selection_path),
                "summary_sha256": implementation.v49.sha256_file(summary_path),
                "events_sha256": implementation.v49.sha256_file(
                    root / "replay/events.jsonl"
                ),
                "diagnostic_audit_sha256": implementation.v49.sha256_file(audit_path),
                "counters_before_sha256": implementation.v49.sha256_file(counters_path),
                "fingerprints": selected,
                "technical_signature": NETWORK_SIGNATURE,
                "physical_workload_calls": 0,
                "ordinal_claims": 0,
            }
        )
    if (
        counter_pin is None
        or counter_pin["global"] != implementation.v49.counter(implementation.v49.GLOBAL_COUNTER)
        or counter_pin["formal_replay"]
        != implementation.v49.counter(implementation.v49.FORMAL_REPLAY_COUNTER)
        or counter_pin["accepted"] != implementation.v49.counter(implementation.v49.ACCEPTED_COUNTER)
        or int(state["accepted_count"]) != counter_pin["accepted"]
        or len(set(fingerprints)) != 15
    ):
        raise implementation.v49.FailClosed("v59 network quarantine state/counter mismatch")
    value = {
        "status": "quarantined_resolved_by_v59_network_contract",
        "contract": NETWORK_QUARANTINE_CONTRACT,
        "technical_signature": NETWORK_SIGNATURE,
        "repeated_signature_count": 3,
        "batches": batches,
        "quarantined_fingerprints": sorted(fingerprints),
        "formal_state_sha256": state_sha,
        "counter_pin": counter_pin,
        "physical_workload_calls": 0,
        "ordinal_claims": 0,
        "valid_replays": 0,
        "accepted_added": 0,
        "formal_state_mutated": False,
        "retry_same_fingerprint_allowed": False,
        "remediation": NETWORK_PREFLIGHT_CONTRACT,
        "new_workload_calls": 0,
    }
    implementation.v49.atomic_json(output, value)
    _validate_network_quarantine(value, cycle_root)
    return value


def generate_fresh_discovery_v59(
    cycle_root: Path,
    replay_attempt: int,
    **kwargs: Any,
) -> Path:
    fresh_root = cycle_root / f"fresh_discovery_{replay_attempt:02d}"
    output = fresh_root / "top5.json"
    second = fresh_root / "top5.second.json"
    budget_path = fresh_root / "v55_successor_budget.json"
    if output.is_file() or second.is_file() or budget_path.is_file():
        if not output.is_file() or not second.is_file() or not budget_path.is_file():
            raise implementation.v49.FailClosed(
                "v59 sealed successor artifact set is partial"
            )
        budget = implementation.v49.read_json(budget_path)
        if (
            output.read_bytes() != second.read_bytes()
            or budget.get("status")
            not in {"ready_for_first_replay", "replay_handoff_started"}
            or budget.get("selection_path") != str(output.resolve())
            or budget.get("selection_sha256")
            != implementation.v49.sha256_file(output)
            or len(budget.get("invocations") or []) != 2
            or any(
                not isinstance(item, Mapping) or item.get("status") != "complete"
                for item in budget.get("invocations") or []
            )
        ):
            raise implementation.v49.FailClosed("v59 sealed successor cannot be reused")
        implementation.v49.validate_top5(
            output,
            roles=("probe",) * 5,
            diagnostic_only=True,
        )
        return output
    result = _GENERATE_V58(cycle_root, replay_attempt, **kwargs)
    if replay_attempt >= 4:
        quarantine = ensure_network_quarantine(cycle_root)
        identifiers = implementation.v49.fingerprint_set(result)
        if identifiers & set(quarantine["quarantined_fingerprints"]):
            raise implementation.v49.FailClosed(
                "v59 remediation rediscovered a quarantined network-failure fingerprint"
            )
    return result


def _signature_count(cycle_root: Path, signature: str) -> int:
    count = 0
    summary_paths = [
        *cycle_root.glob("discovery_replay_*/replay/summary.json"),
        *cycle_root.glob("repeat_*/replay/summary.json"),
    ]
    for summary_path in sorted(summary_paths):
        phase = summary_path.parents[1]
        summary = implementation.v49.read_json(summary_path)
        if int(summary.get("technical_failure_count", 0)) > 0 and _signature(summary, phase) == signature:
            count += 1
    return count


def run_diagnostic_v59(
    selection: Path,
    replay: Path,
    phase_root: Path,
) -> tuple[bool, dict[str, Any]]:
    clean5, summary = _RUN_DIAGNOSTIC_V58(selection, replay, phase_root)
    if clean5 or int(summary.get("technical_failure_count", 0)) <= 0:
        return clean5, summary
    signature = _signature(summary, phase_root)
    cycle_root = next(
        parent
        for parent in (phase_root, *phase_root.parents)
        if parent.name.startswith("cycle_")
    )
    count = _signature_count(cycle_root, signature)
    attempt = _attempt(phase_root)
    if (
        signature == NETWORK_SIGNATURE
        and phase_root.name.startswith("discovery_replay_")
        and attempt in HISTORICAL_NETWORK_ATTEMPTS
        and _network_quarantine_path(cycle_root).is_file()
    ):
        return clean5, summary
    if count >= REPEATED_TECHNICAL_SIGNATURE_LIMIT:
        raise implementation.v49.FailClosed(
            f"v59 repeated technical signature threshold reached: {signature}: {count}"
        )
    return clean5, summary


def resume_position_v59() -> tuple[Path, int]:
    _network_preflight()
    latest, cycle = _RESUME_V58()
    cycle_root = implementation.v49.ROOT / f"cycle_{cycle:03d}"
    if cycle_root.name == "cycle_002" and cycle_root.is_dir():
        ensure_network_quarantine(cycle_root)
    return latest, cycle


def require_v59_hashes() -> None:
    _REQUIRE_V58_HASHES()
    _network_preflight()


implementation.generate_fresh_discovery = generate_fresh_discovery_v59
implementation.run_diagnostic_v50 = run_diagnostic_v59
implementation.resume_position_v50 = resume_position_v59
implementation.require_v50_hashes = require_v59_hashes


if __name__ == "__main__":
    try:
        raise SystemExit(implementation.main())
    except implementation.v49.FailClosed as error:
        try:
            implementation.write_controller_state(status="fail_closed", error=str(error))
        except Exception:
            pass
        print(f"JOB target10 v59 fail closed: {error}", file=implementation.sys.stderr, flush=True)
        raise SystemExit(2)
