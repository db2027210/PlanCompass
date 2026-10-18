#!/usr/bin/env python3
"""JOB v60: propagate the sealed network contract across runner subprocesses."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Iterable, Mapping, Sequence

import job_fresh10_target10_autonomous_v59 as v59


implementation = v59.implementation
EXPECTED_V59_SHA256 = "994c27feba747152372e0155f58d674f47ee306de68fc4ed7f9f4750f51b56d1"
if hashlib.sha256(Path(v59.__file__).read_bytes()).hexdigest() != EXPECTED_V59_SHA256:
    raise implementation.v49.FailClosed("v60 sealed v59 controller SHA mismatch")

NETWORK_ENVIRONMENT_ALLOWLIST = (
    "RA_JOB_DOCKER_NETWORK",
    "RA_JOB_NETWORK_PREFLIGHT_JSON",
    "RA_JOB_NETWORK_PREFLIGHT_SHA256",
)
NETWORK_QUARANTINE_CONTRACT = "job_v60_v58_v59_network_preclaim_quarantine_v1"
NETWORK_REMEDIATION_EPOCH = "job_v60_explicit_runner_subprocess_environment_v1"
HISTORICAL_NETWORK_ATTEMPTS = frozenset({1, 2, 3, 4})
FIRST_POST_REMEDIATION_ATTEMPT = 5

_RUN_LOGGED_V59 = implementation.v49.run_logged
_RUN_DIAGNOSTIC_V58 = v59._RUN_DIAGNOSTIC_V58
_GENERATE_V59 = implementation.generate_fresh_discovery
_RESUME_V59 = implementation.resume_position_v50
_REQUIRE_V59_HASHES = implementation.require_v50_hashes

implementation.CONTROLLER_VERSION = "v60"


def _canonical_sha(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def validated_runner_environment() -> tuple[dict[str, str], dict[str, Any]]:
    """Build an explicit RA_JOB allowlist after validating its sealed source."""
    preflight = v59._network_preflight()
    expected = {
        "RA_JOB_DOCKER_NETWORK": v59.NETWORK_NAME,
        "RA_JOB_NETWORK_PREFLIGHT_JSON": str(
            Path(os.environ["RA_JOB_NETWORK_PREFLIGHT_JSON"]).resolve()
        ),
        "RA_JOB_NETWORK_PREFLIGHT_SHA256": os.environ[
            "RA_JOB_NETWORK_PREFLIGHT_SHA256"
        ],
    }
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("RA_JOB_")
    }
    environment.update(expected)
    environment.pop("RA_PG_FORMAL_SEARCH_MAX_REPLAYS", None)
    environment.pop("RA_PG_FORMAL_SEARCH_ATTEMPT_SAFETY_CAP", None)
    if (
        tuple(sorted(name for name in environment if name.startswith("RA_JOB_")))
        != tuple(sorted(NETWORK_ENVIRONMENT_ALLOWLIST))
        or any(environment.get(name) != value for name, value in expected.items())
        or preflight.get("network_name") != expected["RA_JOB_DOCKER_NETWORK"]
    ):
        raise implementation.v49.FailClosed(
            "v60 runner subprocess network environment assertion failed"
        )
    return environment, preflight


def run_logged_with_network_environment(
    command: Sequence[str],
    log_path: Path,
    *,
    timeout_seconds: int,
    allowed_returncodes: Iterable[int] = (0,),
    cwd: Path | None = None,
) -> int:
    """Spawn a runner with the validated allowlist and a parent-side audit."""
    command_list = list(command)
    environment, preflight = validated_runner_environment()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        raise implementation.v49.FailClosed(
            f"refusing to overwrite process log: {log_path}"
        )
    audit_path = log_path.parent / "v60_runner_subprocess_environment_audit.json"
    audit = {
        "status": "parent_asserted_before_subprocess",
        "contract": NETWORK_REMEDIATION_EPOCH,
        "allowed_ra_job_environment_names": list(NETWORK_ENVIRONMENT_ALLOWLIST),
        "docker_network": environment["RA_JOB_DOCKER_NETWORK"],
        "network_preflight_path": environment["RA_JOB_NETWORK_PREFLIGHT_JSON"],
        "network_preflight_sha256": environment[
            "RA_JOB_NETWORK_PREFLIGHT_SHA256"
        ],
        "network_inspect_sha256": preflight["network_inspect_sha256"],
        "command_sha256": _canonical_sha(command_list),
        "new_workload_calls_before_assertion": 0,
    }
    if audit_path.is_file():
        if implementation.v49.read_json(audit_path) != audit:
            raise implementation.v49.FailClosed(
                "v60 sealed runner environment audit changed"
            )
    else:
        implementation.v49.atomic_json(audit_path, audit)
    # This is the final parent-side assertion immediately before the process
    # boundary.  The child receives this explicit mapping, not ambient lookup.
    for name in NETWORK_ENVIRONMENT_ALLOWLIST:
        if not environment.get(name):
            raise implementation.v49.FailClosed(
                f"v60 runner environment lost {name} before subprocess"
            )
    completed = subprocess.run(
        command_list,
        cwd=str(cwd) if cwd is not None else None,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    log_path.write_text(completed.stdout or "", encoding="utf-8")
    if completed.returncode not in set(allowed_returncodes):
        raise implementation.v49.FailClosed(
            f"command failed rc={completed.returncode}; log={log_path}"
        )
    return completed.returncode


def _is_runner_boundary(command: Sequence[str]) -> bool:
    if len(command) < 2:
        return False
    script = Path(str(command[1])).resolve()
    authorities = {
        Path(implementation.v49.BASE_RUNNER).resolve(),
        Path(implementation.v49.FORMAL_CONTROLLER).resolve(),
    }
    return script in authorities or script.name in {
        "run_job_round8_strict_serial.py",
        "job_formal_target10_batch_controller.py",
    }


def run_logged_v60(
    command: Sequence[str],
    log_path: Path,
    *,
    timeout_seconds: int,
    allowed_returncodes: Iterable[int] = (0,),
    cwd: Path | None = None,
) -> int:
    if _is_runner_boundary(command):
        return run_logged_with_network_environment(
            command,
            log_path,
            timeout_seconds=timeout_seconds,
            allowed_returncodes=allowed_returncodes,
            cwd=cwd,
        )
    return _RUN_LOGGED_V59(
        command,
        log_path,
        timeout_seconds=timeout_seconds,
        allowed_returncodes=allowed_returncodes,
        cwd=cwd,
    )


def _quarantine_path(cycle_root: Path) -> Path:
    return cycle_root / "v60_network_preclaim_quarantine.json"


def _validate_quarantine(value: Mapping[str, Any], cycle_root: Path) -> None:
    fingerprints = value.get("quarantined_fingerprints") or []
    batches = value.get("batches") or []
    if (
        value.get("status") != "quarantined_before_v60_remediation_epoch"
        or value.get("contract") != NETWORK_QUARANTINE_CONTRACT
        or value.get("technical_signature") != v59.NETWORK_SIGNATURE
        or int(value.get("historical_failure_count", -1)) != 4
        or int(value.get("physical_workload_calls", -1)) != 0
        or int(value.get("ordinal_claims", -1)) != 0
        or int(value.get("valid_replays", -1)) != 0
        or int(value.get("accepted_added", -1)) != 0
        or value.get("formal_state_mutated") is not False
        or value.get("retry_same_fingerprint_allowed") is not False
        or value.get("remediation_epoch") != NETWORK_REMEDIATION_EPOCH
        or not isinstance(batches, list)
        or len(batches) != 4
        or not isinstance(fingerprints, list)
        or len(fingerprints) != 20
        or len(set(fingerprints)) != 20
    ):
        raise implementation.v49.FailClosed("v60 network quarantine is invalid")
    for item in batches:
        if not isinstance(item, Mapping):
            raise implementation.v49.FailClosed("v60 quarantine batch is malformed")
        attempt = int(item.get("attempt", -1))
        root = cycle_root / f"discovery_replay_{attempt:02d}"
        bindings = {
            "selection_sha256": root / "top5.json",
            "summary_sha256": root / "replay/summary.json",
            "events_sha256": root / "replay/events.jsonl",
            "diagnostic_audit_sha256": root / "diagnostic_audit.json",
            "counters_before_sha256": root / "counters_before.json",
        }
        if attempt not in HISTORICAL_NETWORK_ATTEMPTS or any(
            item.get(name) != implementation.v49.sha256_file(path)
            for name, path in bindings.items()
        ):
            raise implementation.v49.FailClosed("v60 quarantine binding changed")


def ensure_network_quarantine_v60(cycle_root: Path) -> dict[str, Any]:
    output = _quarantine_path(cycle_root)
    if output.is_file():
        value = implementation.v49.read_json(output)
        _validate_quarantine(value, cycle_root)
        return value
    state_sha, state = implementation.v49.state_pin()
    batches: list[dict[str, Any]] = []
    fingerprints: list[str] = []
    counter_pin: dict[str, int] | None = None
    for attempt in sorted(HISTORICAL_NETWORK_ATTEMPTS):
        root = cycle_root / f"discovery_replay_{attempt:02d}"
        paths = {
            "selection": root / "top5.json",
            "summary": root / "replay/summary.json",
            "events": root / "replay/events.jsonl",
            "audit": root / "diagnostic_audit.json",
            "counters": root / "counters_before.json",
        }
        if not all(path.is_file() for path in paths.values()):
            raise implementation.v49.FailClosed(
                f"v60 historical network failure is incomplete: {root}"
            )
        implementation.v49.validate_top5(
            paths["selection"], roles=("probe",) * 5, diagnostic_only=True
        )
        summary = implementation.v49.read_json(paths["summary"])
        audit = implementation.v49.read_json(paths["audit"])
        counters = implementation.v49.read_json(paths["counters"])
        selected = sorted(implementation.v49.fingerprint_set(paths["selection"]))
        marker = summary.get("preclaim_failure_marker") or {}
        if (
            len(selected) != 5
            or int(summary.get("physical_invocation_count", -1)) != 0
            or int(summary.get("successful_real_replay_count", -1)) != 0
            or int(summary.get("technical_failure_count", -1)) != 1
            or int(summary.get("pre_invocation_technical_failure_count", -1)) != 1
            or marker.get("bridge_invoked") is not False
            or marker.get("preclaim_no_ordinal_claim") is not True
            or summary.get("diagnostic_only") is not True
            or summary.get("baseline_params_restored") is not True
            or summary.get("initial_state") != summary.get("final_state")
            or audit.get("status") != "technical_state_neutral"
            or int(audit.get("physical_calls", -1)) != 0
            or int(audit.get("accepted_added", -1)) != 0
            or audit.get("formal_state_sha256") != state_sha
            or v59._signature(summary, root) != v59.NETWORK_SIGNATURE
        ):
            raise implementation.v49.FailClosed(
                f"v60 historical preclaim contract failed: {root}"
            )
        normalized = {
            "global": int(counters.get("global", -1)),
            "formal_replay": int(counters.get("formal_replay", -1)),
            "accepted": int(counters.get("accepted", -1)),
        }
        if counter_pin is None:
            counter_pin = normalized
        elif normalized != counter_pin:
            raise implementation.v49.FailClosed(
                "v60 zero-claim network failures changed counters"
            )
        fingerprints.extend(selected)
        batches.append(
            {
                "attempt": attempt,
                "selection_sha256": implementation.v49.sha256_file(paths["selection"]),
                "summary_sha256": implementation.v49.sha256_file(paths["summary"]),
                "events_sha256": implementation.v49.sha256_file(paths["events"]),
                "diagnostic_audit_sha256": implementation.v49.sha256_file(paths["audit"]),
                "counters_before_sha256": implementation.v49.sha256_file(paths["counters"]),
                "fingerprints": selected,
                "technical_signature": v59.NETWORK_SIGNATURE,
                "physical_workload_calls": 0,
                "ordinal_claims": 0,
            }
        )
    if (
        counter_pin is None
        or counter_pin["global"]
        != implementation.v49.counter(implementation.v49.GLOBAL_COUNTER)
        or counter_pin["formal_replay"]
        != implementation.v49.counter(implementation.v49.FORMAL_REPLAY_COUNTER)
        or counter_pin["accepted"]
        != implementation.v49.counter(implementation.v49.ACCEPTED_COUNTER)
        or int(state["accepted_count"]) != counter_pin["accepted"]
        or len(set(fingerprints)) != 20
    ):
        raise implementation.v49.FailClosed("v60 quarantine state/counter mismatch")
    value = {
        "status": "quarantined_before_v60_remediation_epoch",
        "contract": NETWORK_QUARANTINE_CONTRACT,
        "technical_signature": v59.NETWORK_SIGNATURE,
        "historical_failure_count": 4,
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
        "remediation_epoch": NETWORK_REMEDIATION_EPOCH,
        "post_remediation_network_failure_limit": 1,
        "new_workload_calls": 0,
    }
    implementation.v49.atomic_json(output, value)
    _validate_quarantine(value, cycle_root)
    return value


def generate_fresh_discovery_v60(
    cycle_root: Path,
    replay_attempt: int,
    **kwargs: Any,
) -> Path:
    result = _GENERATE_V59(cycle_root, replay_attempt, **kwargs)
    if replay_attempt >= FIRST_POST_REMEDIATION_ATTEMPT:
        quarantine = ensure_network_quarantine_v60(cycle_root)
        identifiers = implementation.v49.fingerprint_set(result)
        if identifiers & set(quarantine["quarantined_fingerprints"]):
            raise implementation.v49.FailClosed(
                "v60 rediscovered a pre-remediation quarantined fingerprint"
            )
    return result


def run_diagnostic_v60(
    selection: Path,
    replay: Path,
    phase_root: Path,
) -> tuple[bool, dict[str, Any]]:
    clean5, summary = _RUN_DIAGNOSTIC_V58(selection, replay, phase_root)
    if clean5 or int(summary.get("technical_failure_count", 0)) <= 0:
        return clean5, summary
    signature = v59._signature(summary, phase_root)
    attempt = v59._attempt(phase_root)
    cycle_root = next(
        parent
        for parent in (phase_root, *phase_root.parents)
        if parent.name.startswith("cycle_")
    )
    if (
        signature == v59.NETWORK_SIGNATURE
        and phase_root.name.startswith("discovery_replay_")
        and attempt in HISTORICAL_NETWORK_ATTEMPTS
        and _quarantine_path(cycle_root).is_file()
    ):
        return clean5, summary
    if signature == v59.NETWORK_SIGNATURE and attempt >= FIRST_POST_REMEDIATION_ATTEMPT:
        raise implementation.v49.FailClosed(
            "v60 first post-remediation network failure; refusing another attempt"
        )
    return clean5, summary


def resume_position_v60() -> tuple[Path, int]:
    v59._network_preflight()
    latest, cycle = _RESUME_V59()
    cycle_root = implementation.v49.ROOT / f"cycle_{cycle:03d}"
    if cycle_root.name == "cycle_002" and cycle_root.is_dir():
        ensure_network_quarantine_v60(cycle_root)
    return latest, cycle


def require_v60_hashes() -> None:
    _REQUIRE_V59_HASHES()
    v59._network_preflight()


implementation.v49.run_logged = run_logged_v60
implementation.generate_fresh_discovery = generate_fresh_discovery_v60
implementation.run_diagnostic_v50 = run_diagnostic_v60
implementation.resume_position_v50 = resume_position_v60
implementation.require_v50_hashes = require_v60_hashes


if __name__ == "__main__":
    try:
        raise SystemExit(implementation.main())
    except implementation.v49.FailClosed as error:
        try:
            implementation.write_controller_state(status="fail_closed", error=str(error))
        except Exception:
            pass
        print(
            f"JOB target10 v60 fail closed: {error}",
            file=implementation.sys.stderr,
            flush=True,
        )
        raise SystemExit(2)
