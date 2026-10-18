#!/usr/bin/env python3
"""JOB strict runner adapter for sealed candidate costs and preclaim failures."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from types import ModuleType
from typing import Any, Mapping


FROZEN_BASE_PATH = Path(
    os.environ.get(
        "JOB_ROUND8_FROZEN_BASE_RUNNER",
        "/mnt/nvme2n1/anonymous/resource_adaptive/"
        "pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/"
        "attempt_7_cap5_contract_20260726/job_round8_corrected_top5_v6_20260731/"
        "run_job_round8_strict_serial.py",
    )
).resolve()
EXPECTED_FROZEN_BASE_SHA256 = (
    "9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1"
)
EXPLICIT_COST_ENV = "RA_PG_EXPLICIT_COST_PARAMETERS_JSON"
JOB_DOCKER_NETWORK_ENV = "RA_JOB_DOCKER_NETWORK"
PRECLAIM_MARKER = "technical_failure_before_ordinal_claim"
READINESS_REQUIRED_SUCCESSES = 3
READINESS_MAX_ATTEMPTS = 6
READINESS_TIMEOUT_SECONDS = 5
READINESS_RETRY_INTERVAL_SECONDS = 1.0
COST_PARAMETER_RANGES = {
    "seq_page_cost": (0.1, 10.0),
    "random_page_cost": (0.1, 20.0),
    "cpu_tuple_cost": (0.001, 0.1),
    "cpu_index_tuple_cost": (0.0005, 0.05),
    "cpu_operator_cost": (0.00025, 0.025),
    "parallel_setup_cost": (0.0, 10000.0),
    "parallel_tuple_cost": (0.001, 1.0),
}
PROHIBITED_DOCKER_NETWORKS = frozenset({"bridge", "host", "none"})
_ACTIVE_COSTS: dict[str, float] | None = None
_ACTIVE_PSQL: list[str] | None = None


def _load_frozen() -> ModuleType:
    if (
        not FROZEN_BASE_PATH.is_file()
        or hashlib.sha256(FROZEN_BASE_PATH.read_bytes()).hexdigest()
        != EXPECTED_FROZEN_BASE_SHA256
    ):
        raise RuntimeError("frozen JOB strict base runner SHA mismatch")
    spec = importlib.util.spec_from_file_location(
        "job_round8_strict_serial_frozen_v6", FROZEN_BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen JOB strict base runner")
    module = importlib.util.module_from_spec(spec)
    inserted = str(FROZEN_BASE_PATH.parent)
    sys.path.insert(0, inserted)
    try:
        spec.loader.exec_module(module)
    finally:
        if sys.path and sys.path[0] == inserted:
            sys.path.pop(0)
    return module


frozen = _load_frozen()
for _name in dir(frozen):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(frozen, _name))

_frozen_apply_params = frozen.apply_params
_frozen_workload_environment = frozen.workload_environment


def _canonical_costs(params: Mapping[str, Any]) -> dict[str, float]:
    if set(params) != set(frozen.PARAMETER_NAMES):
        raise RuntimeError("candidate cost vector is incomplete")
    result = {
        name: float(frozen.finite(params[name], f"candidate_params.{name}"))
        for name in frozen.PARAMETER_NAMES
    }
    for name, value in result.items():
        if not math.isfinite(value):
            raise RuntimeError(f"candidate cost is not finite: {name}")
        lower, upper = COST_PARAMETER_RANGES[name]
        if value < lower or value > upper:
            raise RuntimeError(
                f"candidate cost is outside bridge range: {name}={value} "
                f"not in [{lower}, {upper}]"
            )
    return result


def apply_params(params: Mapping[str, Any], psql: list[str]) -> dict[str, Any]:
    global _ACTIVE_COSTS, _ACTIVE_PSQL
    result = _frozen_apply_params(params, psql)
    _ACTIVE_COSTS = _canonical_costs(params)
    _ACTIVE_PSQL = list(psql)
    return result


def _control_readiness(psql: list[str]) -> dict[str, Any]:
    """Require three consecutive successful control sessions within six tries."""
    consecutive = 0
    observations: list[dict[str, Any]] = []
    environment = {
        **os.environ,
        "PGPASSWORD": os.environ.get("PGPASSWORD", "CHANGE_ME"),
        "PGCONNECT_TIMEOUT": "3",
    }
    query = "SELECT 1, pg_is_in_recovery();"
    for attempt in range(1, READINESS_MAX_ATTEMPTS + 1):
        try:
            completed = subprocess.run(
                psql + ["-Atc", query],
                check=False,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=READINESS_TIMEOUT_SECONDS,
            )
            output = (completed.stdout or "").strip()
            healthy = bool(
                completed.returncode == 0
                and any(
                    line.strip().lower() in {"1|f", "1|false"}
                    for line in output.splitlines()
                )
            )
            observations.append(
                {
                    "attempt": attempt,
                    "returncode": completed.returncode,
                    "healthy_primary": healthy,
                    "output_tail": output[-512:],
                }
            )
        except subprocess.TimeoutExpired as error:
            healthy = False
            observations.append(
                {
                    "attempt": attempt,
                    "returncode": 124,
                    "healthy_primary": False,
                    "output_tail": str(error)[-512:],
                }
            )
        consecutive = consecutive + 1 if healthy else 0
        if consecutive >= READINESS_REQUIRED_SUCCESSES:
            return {
                "status": "passed",
                "required_consecutive_successes": READINESS_REQUIRED_SUCCESSES,
                "maximum_attempts": READINESS_MAX_ATTEMPTS,
                "attempts_used": attempt,
                "observations": observations,
            }
        if attempt < READINESS_MAX_ATTEMPTS:
            time.sleep(READINESS_RETRY_INTERVAL_SECONDS)
    raise RuntimeError(
        "control readiness failed before bridge invocation: "
        + json.dumps(observations, sort_keys=True)
    )


def _docker_network_readiness() -> dict[str, Any]:
    """Validate an unused, dedicated local bridge without starting a container."""
    raw = os.environ.get(JOB_DOCKER_NETWORK_ENV, "")
    network = raw.strip()
    if (
        not network
        or network != raw
        or network.lower() in PROHIBITED_DOCKER_NETWORKS
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", network) is None
    ):
        raise RuntimeError("invalid or prohibited RA_JOB_DOCKER_NETWORK")
    completed = subprocess.run(
        ["docker", "network", "inspect", network],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=READINESS_TIMEOUT_SECONDS,
    )
    try:
        documents = json.loads(completed.stdout or "")
    except json.JSONDecodeError as error:
        raise RuntimeError("docker network inspect returned invalid JSON") from error
    if (
        completed.returncode != 0
        or not isinstance(documents, list)
        or len(documents) != 1
        or not isinstance(documents[0], Mapping)
    ):
        raise RuntimeError("dedicated JOB Docker network is unavailable")
    document = documents[0]
    containers = document.get("Containers") or {}
    options = document.get("Options") or {}
    ipam = document.get("IPAM") or {}
    ipam_config = ipam.get("Config") or []
    if (
        document.get("Name") != network
        or document.get("Driver") != "bridge"
        or document.get("Scope") != "local"
        or document.get("Internal") is not False
        or not isinstance(containers, Mapping)
        or len(containers) != 0
        or not isinstance(options, Mapping)
        or options.get("com.docker.network.bridge.name") in (None, "", "docker0")
        or not isinstance(ipam_config, list)
        or not ipam_config
        or not all(
            isinstance(item, Mapping) and item.get("Subnet") and item.get("Gateway")
            for item in ipam_config
        )
    ):
        raise RuntimeError("dedicated JOB Docker network contract mismatch")
    return {
        "status": "passed",
        "name": network,
        "driver": "bridge",
        "scope": "local",
        "attached_container_count": 0,
        "bridge_interface": options["com.docker.network.bridge.name"],
        "ipam_config": ipam_config,
    }


def workload_environment(
    point_root: Path,
    *,
    diagnostic_only: bool,
) -> dict[str, str]:
    if _ACTIVE_COSTS is None or _ACTIVE_PSQL is None:
        raise RuntimeError("candidate costs were not sealed before workload launch")
    environment = _frozen_workload_environment(
        point_root,
        diagnostic_only=diagnostic_only,
    )
    canonical = json.dumps(_ACTIVE_COSTS, sort_keys=True, separators=(",", ":"))
    environment[EXPLICIT_COST_ENV] = canonical
    environment["RA_PG_CALIBRATION_SELECTION_ID"] = (
        "strict-serial-candidate-sha256:"
        + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )
    environment["RA_PG_CALIBRATION_SUBSET_ID"] = (
        "job_v53_dedicated_network_exact_seven_candidate_costs"
    )
    try:
        readiness = {
            "docker_network": _docker_network_readiness(),
            "control_postgresql": _control_readiness(_ACTIVE_PSQL),
        }
    except RuntimeError as error:
        point_root.mkdir(parents=True, exist_ok=True)
        marker = {
            "status": PRECLAIM_MARKER,
            "preclaim_no_ordinal_claim": True,
            "technical_failure": True,
            "reason": "network_or_control_readiness_failed_before_bridge_invocation",
            "error": str(error),
            "sealed_candidate_sha256": hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest(),
            "bridge_invoked": False,
        }
        (point_root / "workload.log").write_text(
            json.dumps(marker, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        raise RuntimeError(
            "network/control readiness failed before bridge invocation; "
            "ordinal was not claimed"
        ) from error
    environment["RA_PG_CONTROL_READINESS_JSON"] = json.dumps(
        readiness,
        sort_keys=True,
        separators=(",", ":"),
    )
    environment[JOB_DOCKER_NETWORK_ENV] = readiness["docker_network"]["name"]
    return environment


def _payload_from_log(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    for line in reversed(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _normalize_preclaim_failure(
    result: Mapping[str, Any],
    replay_root: Path,
) -> dict[str, Any]:
    normalized_result = dict(result)
    summary_path = Path(str(result["summary_path"])).resolve()
    summary = dict(frozen.load_json(summary_path))
    failures = summary.get("technical_failures") or []
    if not failures:
        return normalized_result
    rank = int(failures[-1].get("rank", -1))
    marker = _payload_from_log(replay_root / f"rank_{rank:02d}/workload.log")
    if not (
        isinstance(marker, Mapping)
        and marker.get("status") == PRECLAIM_MARKER
        and marker.get("preclaim_no_ordinal_claim") is True
    ):
        return normalized_result
    invocations = summary.get("invocations") or []
    kept = [item for item in invocations if int(item.get("rank", -1)) != rank]
    if len(kept) != len(invocations) - 1:
        raise RuntimeError("preclaim failure does not bind one invocation row")
    physical = int(summary.get("physical_invocation_count", -1)) - 1
    if physical < 0:
        raise RuntimeError("preclaim normalization would make physical count negative")
    summary.update(
        {
            "invocations": kept,
            "physical_invocation_count": physical,
            "real_workload_invocation_count": physical,
            "batch_budget_attempt_count": (
                int(summary.get("successful_real_replay_count", 0))
                + int(summary.get("technical_failure_count", 0))
            ),
            "pre_invocation_technical_failure_count": 1,
            "pre_invocation_technical_failure_ranks": [rank],
            "preclaim_no_ordinal_claim": True,
            "preclaim_failure_marker": copy.deepcopy(dict(marker)),
        }
    )
    temporary = summary_path.with_name(f".{summary_path.name}.v51.{os.getpid()}")
    temporary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, summary_path)
    normalized_result.update(
        {
            "physical_invocation_count": physical,
            "batch_budget_attempt_count": summary["batch_budget_attempt_count"],
            "pre_invocation_technical_failure_count": 1,
            "pre_invocation_technical_failure_ranks": [rank],
            "preclaim_no_ordinal_claim": True,
            "technical_failures": copy.deepcopy(failures),
        }
    )
    return normalized_result


def run(args: argparse.Namespace) -> dict[str, Any]:
    global _ACTIVE_COSTS, _ACTIVE_PSQL
    _ACTIVE_COSTS = None
    _ACTIVE_PSQL = None
    frozen.apply_params = apply_params
    frozen.workload_environment = workload_environment
    # robust-v7 replaces this adapter attribute after import.  Propagate that
    # decision hook into the frozen implementation immediately before run.
    frozen.decide_state_update = globals()["decide_state_update"]
    result = frozen.run(args)
    return _normalize_preclaim_failure(result, Path(args.replay_root).resolve())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path)
    parser.add_argument("replay_root", type=Path)
    parser.add_argument("--authority-root", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--exclusion", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--accepted-target", type=int, default=10)
    parser.add_argument("--diagnostic-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        print(
            json.dumps(
                frozen.preflight(
                    args.selection.resolve(),
                    args.authority_root.resolve(),
                    args.observations.resolve(),
                    args.exclusion.resolve(),
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
