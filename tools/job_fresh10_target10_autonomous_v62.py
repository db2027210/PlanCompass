#!/usr/bin/env python3
"""JOB v62: per-runner live network authority and clean5 resume bridge."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Iterable, Mapping, Sequence

import job_fresh10_target10_autonomous_v61 as v61


implementation = v61.implementation
EXPECTED_V61_SHA256 = "49e6c210380c0852d9b0dffd7da597d318de1932994351c31b028cc6cbf53053"
if hashlib.sha256(Path(v61.__file__).read_bytes()).hexdigest() != EXPECTED_V61_SHA256:
    raise implementation.v49.FailClosed("v62 sealed v61 controller SHA mismatch")

CONTROLLER_VERSION = "v62"
NETWORK_PREFLIGHT_CONTRACT = "job_v62_per_runner_live_network_authority_preflight_v1"
AUTHORITY_BRIDGE_CONTRACT = "job_v62_v61_clean5_authority_bridge_v1"
CONTROLLER_RECOVERY_CONTRACT = "job_v62_zero_call_controller_resume_v1"
NETWORK_NAME = v61.v60.v59.NETWORK_NAME
BRIDGE_INTERFACE = v61.v60.v59.BRIDGE_INTERFACE
NETWORK_PREFLIGHT_MAX_AGE_SECONDS = 60.0

_RUN_LOGGED_V61 = implementation.v49.run_logged
_RESUME_V60 = v61._RESUME_V60
_REQUIRE_V61_HASHES = implementation.require_v50_hashes

implementation.CONTROLLER_VERSION = CONTROLLER_VERSION


def _authority_pin() -> dict[str, Any]:
    state_sha, state = implementation.v49.state_pin()
    counters = {
        "global": implementation.v49.counter(implementation.v49.GLOBAL_COUNTER),
        "formal_replay": implementation.v49.counter(
            implementation.v49.FORMAL_REPLAY_COUNTER
        ),
        "accepted": implementation.v49.counter(implementation.v49.ACCEPTED_COUNTER),
    }
    if int(state.get("accepted_count", -1)) != counters["accepted"]:
        raise implementation.v49.FailClosed("v62 formal state/accepted counter mismatch")
    return {
        "formal_state_sha256": state_sha,
        "accepted_count": int(state["accepted_count"]),
        "counters": counters,
    }


def _inspect_network() -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["docker", "network", "inspect", NETWORK_NAME],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise implementation.v49.FailClosed(
            f"v62 dedicated network inspect failed rc={completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise implementation.v49.FailClosed(
            "v62 dedicated network inspect is not JSON"
        ) from error
    if not isinstance(value, list):
        raise implementation.v49.FailClosed("v62 network inspect root is not a list")
    return value


def _validate_network_document(value: Any) -> dict[str, Any]:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], Mapping):
        raise implementation.v49.FailClosed("v62 network inspect shape is invalid")
    network = dict(value[0])
    containers = network.get("Containers")
    options = network.get("Options")
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
        raise implementation.v49.FailClosed(
            "v62 dedicated bridge/name/empty-container contract failed"
        )
    return network


def seal_live_network_preflight(boundary_root: Path) -> dict[str, Any]:
    """Inspect and bind the dedicated bridge immediately before one runner."""
    authority_before = _authority_pin()
    inspected = _inspect_network()
    _validate_network_document(inspected)
    authority_after = _authority_pin()
    if authority_after != authority_before:
        raise implementation.v49.FailClosed(
            "v62 authority changed during dedicated network inspect"
        )
    boundary_root.mkdir(parents=True, exist_ok=True)
    token = (
        f"g{authority_before['counters']['global']}_"
        f"f{authority_before['counters']['formal_replay']}_"
        f"a{authority_before['counters']['accepted']}_"
        f"{time.time_ns()}_{os.getpid()}"
    )
    inspect_path = boundary_root / f"network_inspect_{token}.json"
    audit_path = boundary_root / f"network_preflight_{token}.json"
    if inspect_path.exists() or audit_path.exists():
        raise implementation.v49.FailClosed("v62 live preflight token collision")
    implementation.v49.atomic_json(inspect_path, inspected)
    created = time.time()
    audit = {
        "contract": NETWORK_PREFLIGHT_CONTRACT,
        "status": "sealed_for_one_runner_boundary",
        "network_name": NETWORK_NAME,
        "driver": "bridge",
        "scope": "local",
        "internal": False,
        "bridge_interface": BRIDGE_INTERFACE,
        "attached_container_count": 0,
        "network_inspect_path": str(inspect_path.resolve()),
        "network_inspect_sha256": implementation.v49.sha256_file(inspect_path),
        "formal_state_sha256": authority_before["formal_state_sha256"],
        "accepted_count": authority_before["accepted_count"],
        "global_counter": authority_before["counters"]["global"],
        "formal_replay_counter": authority_before["counters"]["formal_replay"],
        "accepted_counter": authority_before["counters"]["accepted"],
        "created_unix_seconds": created,
        "expires_after_seconds": NETWORK_PREFLIGHT_MAX_AGE_SECONDS,
        "single_runner_boundary": True,
        "new_workload_calls": 0,
        "formal_state_updates": 0,
        "counter_updates": 0,
    }
    implementation.v49.atomic_json(audit_path, audit)
    audit["audit_path"] = str(audit_path.resolve())
    audit["audit_sha256"] = implementation.v49.sha256_file(audit_path)
    return audit


def validate_live_network_preflight() -> dict[str, Any]:
    path_raw = os.environ.get("RA_JOB_NETWORK_PREFLIGHT_JSON")
    expected_sha = os.environ.get("RA_JOB_NETWORK_PREFLIGHT_SHA256")
    if (
        os.environ.get("RA_JOB_DOCKER_NETWORK") != NETWORK_NAME
        or not path_raw
        or not expected_sha
    ):
        raise implementation.v49.FailClosed("v62 live network environment is absent")
    path = Path(path_raw).resolve()
    if not path.is_file() or implementation.v49.sha256_file(path) != expected_sha:
        raise implementation.v49.FailClosed("v62 live network preflight SHA mismatch")
    value = implementation.v49.read_json(path)
    inspect_path = Path(str(value.get("network_inspect_path") or "")).resolve()
    if (
        not inspect_path.is_file()
        or value.get("network_inspect_sha256")
        != implementation.v49.sha256_file(inspect_path)
    ):
        raise implementation.v49.FailClosed("v62 live network inspect binding changed")
    inspected = json.loads(inspect_path.read_text(encoding="utf-8"))
    _validate_network_document(inspected)
    age = time.time() - float(value.get("created_unix_seconds", 0.0))
    authority = _authority_pin()
    if (
        value.get("contract") != NETWORK_PREFLIGHT_CONTRACT
        or value.get("status") != "sealed_for_one_runner_boundary"
        or value.get("network_name") != NETWORK_NAME
        or value.get("driver") != "bridge"
        or value.get("scope") != "local"
        or value.get("internal") is not False
        or value.get("bridge_interface") != BRIDGE_INTERFACE
        or int(value.get("attached_container_count", -1)) != 0
        or value.get("single_runner_boundary") is not True
        or int(value.get("new_workload_calls", -1)) != 0
        or int(value.get("formal_state_updates", -1)) != 0
        or int(value.get("counter_updates", -1)) != 0
        or age < -1.0
        or age > NETWORK_PREFLIGHT_MAX_AGE_SECONDS
        or value.get("formal_state_sha256") != authority["formal_state_sha256"]
        or int(value.get("accepted_count", -1)) != authority["accepted_count"]
        or int(value.get("global_counter", -1)) != authority["counters"]["global"]
        or int(value.get("formal_replay_counter", -1))
        != authority["counters"]["formal_replay"]
        or int(value.get("accepted_counter", -1))
        != authority["counters"]["accepted"]
    ):
        raise implementation.v49.FailClosed(
            "v62 live dedicated network/current authority contract failed"
        )
    return value


def install_live_network_preflight(boundary_root: Path) -> dict[str, Any]:
    value = seal_live_network_preflight(boundary_root)
    os.environ["RA_JOB_DOCKER_NETWORK"] = NETWORK_NAME
    os.environ["RA_JOB_NETWORK_PREFLIGHT_JSON"] = value["audit_path"]
    os.environ["RA_JOB_NETWORK_PREFLIGHT_SHA256"] = value["audit_sha256"]
    return value


def run_logged_v62(
    command: Sequence[str],
    log_path: Path,
    *,
    timeout_seconds: int,
    allowed_returncodes: Iterable[int] = (0,),
    cwd: Path | None = None,
) -> int:
    if v61.v60._is_runner_boundary(command):
        preflight = install_live_network_preflight(
            log_path.parent / "v62_live_runner_preflights"
        )
        result = v61.v60.run_logged_with_network_environment(
            command,
            log_path,
            timeout_seconds=timeout_seconds,
            allowed_returncodes=allowed_returncodes,
            cwd=cwd,
        )
        if (
            preflight["audit_sha256"]
            != os.environ.get("RA_JOB_NETWORK_PREFLIGHT_SHA256")
        ):
            raise implementation.v49.FailClosed(
                "v62 runner preflight binding changed across subprocess"
            )
        return result
    return _RUN_LOGGED_V61(
        command,
        log_path,
        timeout_seconds=timeout_seconds,
        allowed_returncodes=allowed_returncodes,
        cwd=cwd,
    )


def _bridge_path(cycle_root: Path) -> Path:
    return cycle_root / "v62_v61_clean5_authority_bridge.json"


def build_clean5_authority_bridge(cycle_root: Path) -> dict[str, Any]:
    output = _bridge_path(cycle_root)
    recovery = cycle_root / "fresh_discovery_05/v61_partial_recovery_audit.json"
    phase = cycle_root / "discovery_replay_05"
    selection = phase / "top5.json"
    summary_path = phase / "replay/summary.json"
    events = phase / "replay/events.jsonl"
    audit_path = phase / "diagnostic_audit.json"
    counters_path = phase / "counters_before.json"
    repeat = cycle_root / "repeat_01/top5.json"
    required = (recovery, selection, summary_path, events, audit_path, counters_path, repeat)
    if not all(path.is_file() for path in required):
        raise implementation.v49.FailClosed("v62 clean5 authority bridge evidence is incomplete")
    recovery_value = implementation.v49.read_json(recovery)
    summary = implementation.v49.read_json(summary_path)
    diagnostic_audit = implementation.v49.read_json(audit_path)
    counters = implementation.v49.read_json(counters_path)
    current = _authority_pin()
    implementation.v49.validate_top5(
        repeat,
        roles=("trusted", "trusted", "trusted", "probe", "probe"),
        diagnostic_only=True,
    )
    physical = int(summary.get("physical_invocation_count", -1))
    if (
        recovery_value.get("status") != "committed"
        or recovery_value.get("contract") != v61.RECOVERY_CONTRACT
        or int(recovery_value.get("new_workload_calls", -1)) != 0
        or int(recovery_value.get("formal_state_updates", -1)) != 0
        or int(recovery_value.get("counter_updates", -1)) != 0
        or int(counters.get("global", -1)) + physical
        != current["counters"]["global"]
        or int(counters.get("formal_replay", -1))
        != current["counters"]["formal_replay"]
        or int(counters.get("accepted", -1)) != current["counters"]["accepted"]
        or physical != 5
        or int(summary.get("successful_real_replay_count", -1)) != 5
        or int(summary.get("technical_failure_count", -1)) != 0
        or summary.get("diagnostic_only") is not True
        or summary.get("baseline_params_restored") is not True
        or summary.get("initial_state") != summary.get("final_state")
        or diagnostic_audit.get("status") != "clean5_state_neutral"
        or int(diagnostic_audit.get("physical_calls", -1)) != 5
        or int(diagnostic_audit.get("valid_replays", -1)) != 5
        or int(diagnostic_audit.get("technical_failures", -1)) != 0
        or int(diagnostic_audit.get("accepted_added", -1)) != 0
        or diagnostic_audit.get("formal_state_sha256")
        != current["formal_state_sha256"]
        or summary.get("selection_sha256") != implementation.v49.sha256_file(selection)
        or diagnostic_audit.get("summary_sha256")
        != implementation.v49.sha256_file(summary_path)
    ):
        raise implementation.v49.FailClosed("v62 clean5 authority transition is invalid")
    value = {
        "status": "sealed_state_neutral_transition",
        "contract": AUTHORITY_BRIDGE_CONTRACT,
        "authority_before": {
            "formal_state_sha256": current["formal_state_sha256"],
            "accepted_count": int(counters["accepted"]),
            "counters": {
                "global": int(counters["global"]),
                "formal_replay": int(counters["formal_replay"]),
                "accepted": int(counters["accepted"]),
            },
        },
        "authority_after": current,
        "physical_transition": physical,
        "valid_replays": 5,
        "technical_failures": 0,
        "accepted_delta": 0,
        "formal_state_updates": 0,
        "artifacts": {
            str(path.resolve()): implementation.v49.sha256_file(path)
            for path in required
        },
        "repeat_selection_path": str(repeat.resolve()),
        "repeat_selection_sha256": implementation.v49.sha256_file(repeat),
        "reuse_repeat_01": True,
        "new_workload_calls": 0,
    }
    if output.is_file():
        if implementation.v49.read_json(output) != value:
            raise implementation.v49.FailClosed("v62 clean5 authority bridge changed")
    else:
        implementation.v49.atomic_json(output, value)
    return value


def recover_controller_state_v62(cycle_root: Path) -> dict[str, Any]:
    bridge = build_clean5_authority_bridge(cycle_root)
    bridge_path = _bridge_path(cycle_root)
    state_path = implementation.v49.ROOT / "controller_state.json"
    current = implementation.v49.read_json(state_path)
    if (
        current.get("schema_version") == "job_fresh10_target10_autonomous_v62"
        and current.get("status") == "ready_resume"
    ):
        if current.get("authority_bridge_sha256") != implementation.v49.sha256_file(
            bridge_path
        ):
            raise implementation.v49.FailClosed("v62 recovered controller state changed")
        return current
    if (
        current.get("schema_version") != "job_fresh10_target10_autonomous_v61"
        or current.get("status") != "fail_closed"
        or "network preflight" not in str(current.get("error") or "").lower()
    ):
        raise implementation.v49.FailClosed(
            "v62 controller state does not match the v61 stale-preflight failure"
        )
    original = state_path.read_bytes()
    backup = state_path.with_name(
        f"controller_state.json.pre_v62.{int(time.time())}."
        f"{hashlib.sha256(original).hexdigest()[:12]}.bak"
    )
    if backup.exists():
        raise implementation.v49.FailClosed("v62 controller state backup collision")
    backup.write_bytes(original)
    if hashlib.sha256(backup.read_bytes()).digest() != hashlib.sha256(original).digest():
        raise implementation.v49.FailClosed("v62 controller state backup SHA mismatch")
    authority = _authority_pin()
    recovered = {
        "schema_version": "job_fresh10_target10_autonomous_v62",
        "status": "ready_resume",
        "target_accepted": implementation.v49.TARGET_ACCEPTED,
        "accepted_count": authority["accepted_count"],
        "cycle": 2,
        "phase": "repeat_gate",
        "formal_state_sha256": authority["formal_state_sha256"],
        "qps_used_for_inner_scoring": False,
        "manual_candidate_injection": False,
        "technical_batch_requires_fresh_fingerprints": True,
        "controller_recovery_contract": CONTROLLER_RECOVERY_CONTRACT,
        "authority_bridge_path": str(bridge_path.resolve()),
        "authority_bridge_sha256": implementation.v49.sha256_file(bridge_path),
        "reused_repeat_selection_path": bridge["repeat_selection_path"],
        "reused_repeat_selection_sha256": bridge["repeat_selection_sha256"],
        "new_workload_calls": 0,
        "formal_state_updates": 0,
        "counter_updates": 0,
        "source_fail_closed_state_sha256": hashlib.sha256(original).hexdigest(),
        "source_fail_closed_state_backup": str(backup.resolve()),
    }
    implementation.v49.atomic_json(state_path, recovered)
    return recovered


def resume_position_v62() -> tuple[Path, int]:
    install_live_network_preflight(
        implementation.v49.ROOT / "preflights/v62_controller_resume"
    )
    cycle_root = implementation.v49.ROOT / "cycle_002"
    recover_controller_state_v62(cycle_root)
    return _RESUME_V60()


def require_v62_hashes() -> None:
    install_live_network_preflight(
        implementation.v49.ROOT / "preflights/v62_controller_require"
    )
    _REQUIRE_V61_HASHES()


v61.v60.v59._network_preflight = validate_live_network_preflight
implementation.v49.run_logged = run_logged_v62
implementation.resume_position_v50 = resume_position_v62
implementation.require_v50_hashes = require_v62_hashes


if __name__ == "__main__":
    try:
        raise SystemExit(implementation.main())
    except implementation.v49.FailClosed as error:
        try:
            implementation.write_controller_state(status="fail_closed", error=str(error))
        except Exception:
            pass
        print(
            f"JOB target10 v62 fail closed: {error}",
            file=implementation.sys.stderr,
            flush=True,
        )
        raise SystemExit(2)
