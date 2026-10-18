#!/usr/bin/env python3
"""Persistent auto-launch controller for one formal MySQL SGD lane.

The controller never consumes a hand-written launch_request.json.  It records
the frozen manifest gates and starts the registered production entry directly
only after the authoritative verifier passes.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_sgd_env_agents_20260723"
METHOD = "experience_table_all_subset_sgd"
CONTROLLER_CONTRACT_VERSION = "mysql_formal_algorithm_b_launch_v5"
READINESS = ROOT / "shell/verify_mysql_sgd_v4_readiness.py"
READINESS_MANIFEST = (
    ROOT
    / "config/formal/frozen_mysql_deployment_schedule_20260723_v4_mysql_only"
    / "readiness_manifest.json"
)
GATE_POLICY = ROOT / "config/mysql_sgd_pre_tuning_gate_v1.json"
# JOB and XP are intentionally allowed to run concurrently on host02.  Each
# lane still holds its own controller lock to prevent duplicate campaigns.
HOST02_SCENARIOS: set[str] = set()
HOST02_CAMPAIGN_LOCK = HISTORY / "host02_job_xp_campaign.lock"
PRODUCTION_ENTRY = ROOT / "formal_mysql_sgd_campaign.py"
SCENARIOS = {
    "job_memory": "job",
    "hybench_xp_io": "xp",
    "tpch_memory": "tpch",
    "tpcc_cpu": "tpcc",
}
TARGET_RESOURCES = {
    "job_memory": "memory",
    "hybench_xp_io": "io",
    "tpch_memory": "memory",
    "tpcc_cpu": "cpu",
}
RESOURCE_NAMES = {"cpu", "memory", "io"}
ENVELOPE_FIELDS = {
    "cpu_quota_cores",
    "cpuset_cpus",
    "cpuset_mems",
    "memory_bytes",
    "memory_swap_bytes",
    "io_read_bps",
    "io_write_bps",
    "pids_max",
    "cpu_isolation_degree",
    "numa_isolation_degree",
}
AUTO_LAUNCH_REGISTRY = {
    scenario: {
        "config": ROOT / "config/formal/mysql_sgd_production" / (
            f"{scenario}.algorithm_c.optimizer.json"
        ),
        "journal_root": HISTORY / SCENARIOS[scenario] / "formal_sgd_journals",
        # A complete factory must provide registration, R0, sampling, SQL,
        # correctness, recovery, and the host02 pressure probe when applicable.
        "service_factory": "mysql_algorithm_c_production_services:build",
    }
    for scenario in SCENARIOS
}
STOP = False


def try_acquire_host02_campaign_lease(
    scenario: str, lock_path: Path = HOST02_CAMPAIGN_LOCK
) -> int | None:
    if scenario not in HOST02_SCENARIOS:
        return None
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    return descriptor


def release_host02_campaign_lease(descriptor: int | None) -> None:
    if descriptor is None:
        return
    fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)


def request_stop(_signum: int, _frame: object) -> None:
    global STOP
    STOP = True


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prerequisites(scenario: str) -> dict[str, Any]:
    key = SCENARIOS[scenario]
    ap_t = (
        HISTORY / "ap/ap_v5_endpoint_real_20260723T082000Z_salvaged_t/"
        "T.mysql_ap_estimator_v1.json"
    )
    facts: dict[str, Any] = {
        "template": False,
        "mysql_envelope": False,
        "experience_table_t": False,
        "static_isolation": False,
        "dynamic_interference": False,
        "r0": False,
        "exclusive_lease": False,
        "production_factory": False,
        "optimizer_config": False,
        "automatic_launch": True,
    }
    if key == "ap":
        facts.update(
            template=(ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/evidence/ap_template_manifest_20260723.json").is_file(),
            mysql_envelope=(HISTORY / "ap/formal_mysql_normalized_envelope_readback.json").is_file(),
            experience_table_t=ap_t.is_file(),
        )
    elif key == "job":
        job_tables = tuple(sorted(
            (HISTORY / "job").glob(
                "job_plan_effect_t_*/T.mysql_job_physical_plan_effect_v1.json"
            )
        ))
        facts.update(
            template=(ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/evidence/job_template_manifest_20260723.json").is_file(),
            mysql_envelope=(HISTORY / "job/formal_mysql_normalized_envelope_readback.json").is_file(),
            experience_table_t=bool(job_tables),
        )
    elif key == "xp":
        facts.update(
            template=(ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/evidence/xp_template_deployment_20260723.json").is_file(),
            mysql_envelope=(HISTORY / "xp/formal_mysql_normalized_envelope_readback.json").is_file(),
        )
    elif key == "tpch":
        facts.update(
            template=(ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/evidence/tpch_template_manifest_20260723.json").is_file(),
            mysql_envelope=(HISTORY / "tpch/formal_mysql_normalized_envelope_readback.json").is_file(),
        )
    else:
        facts.update(
            template=(ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/evidence/tpcc_e1_template_deployment_20260723.json").is_file(),
            mysql_envelope=(HISTORY / "tpcc/formal_mysql_normalized_envelope_readback.json").is_file(),
        )
    return facts


def manifest_scenario(scenario: str) -> dict[str, Any]:
    manifest = load(READINESS_MANIFEST)
    scenarios = manifest.get("scenarios")
    value = scenarios.get(scenario) if isinstance(scenarios, Mapping) else None
    return dict(value) if isinstance(value, Mapping) else {}


def auto_argv(scenario: str) -> tuple[list[str] | None, list[str]]:
    registration = AUTO_LAUNCH_REGISTRY[scenario]
    errors: list[str] = []
    config = Path(registration["config"])
    service_factory = registration.get("service_factory")
    if not config.is_file() or config.is_symlink():
        errors.append(f"production optimizer config missing: {config.relative_to(ROOT)}")
    if not PRODUCTION_ENTRY.is_file() or PRODUCTION_ENTRY.is_symlink():
        errors.append(f"production entry missing: {PRODUCTION_ENTRY.relative_to(ROOT)}")
    if not isinstance(service_factory, str) or not service_factory.strip():
        errors.append(f"formal production service factory is not registered: {scenario}")
    if not READINESS_MANIFEST.is_file() or READINESS_MANIFEST.is_symlink():
        errors.append("formal readiness manifest is missing or unsafe")
    if errors:
        return None, errors
    return [
        sys.executable,
        str(PRODUCTION_ENTRY),
        "--config",
        str(config),
        "--readiness-manifest",
        str(READINESS_MANIFEST),
        "--service-factory",
        service_factory,
        "--journal-root",
        str(registration["journal_root"]),
    ], []


def apply_manifest_facts(
    facts: dict[str, Any], scenario: str, scenario_manifest: Mapping[str, Any]
) -> None:
    def passed(name: str) -> bool:
        value = scenario_manifest.get(name)
        return isinstance(value, Mapping) and value.get("passed") is True

    template = scenario_manifest.get("template")
    envelope = scenario_manifest.get("mysql_envelope")
    table = scenario_manifest.get("experience_table")
    lease = scenario_manifest.get("lane_lease")
    facts.update(
        template=isinstance(template, Mapping) and template.get("ready") is True,
        mysql_envelope=(
            isinstance(envelope, Mapping)
            and isinstance(envelope.get("readback"), Mapping)
        ),
        static_isolation=passed("static_isolation"),
        dynamic_interference=passed("dynamic_interference"),
        r0=passed("r0_calibration"),
        bottleneck_construction=passed("r0_calibration"),
        experience_table_t=(
            isinstance(table, Mapping)
            and table.get("version") is not None
            and isinstance(table.get("artifact"), Mapping)
        ),
        exclusive_lease=(
            isinstance(lease, Mapping)
            and lease.get("exclusive") is True
            and isinstance(lease.get("evidence"), Mapping)
        ),
    )
    registration = AUTO_LAUNCH_REGISTRY[scenario]
    facts["optimizer_config"] = Path(registration["config"]).is_file()
    facts["production_factory"] = (
        PRODUCTION_ENTRY.is_file()
        and isinstance(registration.get("service_factory"), str)
        and bool(registration["service_factory"].strip())
    )


def global_readiness() -> dict[str, Any]:
    try:
        result = subprocess.run(
            [os.fspath(Path(os.sys.executable)), os.fspath(READINESS), "--json"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            return {"ready": False, "status": "INVALID_READINESS_OUTPUT"}
        value["verifier_returncode"] = result.returncode
        return value
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {"ready": False, "status": "READINESS_VERIFY_ERROR"}


def phase(facts: Mapping[str, Any], child: subprocess.Popen[str] | None) -> str:
    if child is not None and child.poll() is None:
        return "SGD_RUNNING"
    if not facts.get("experience_table_t"):
        return "PREPARING_T"
    if not all(facts.get(name) for name in (
        "static_isolation", "dynamic_interference", "r0", "exclusive_lease"
    )):
        return "PREPARING_RUNTIME_GATES"
    if not facts.get("optimizer_config"):
        return "PREPARING_OPTIMIZER_CONFIG"
    if not facts.get("production_factory"):
        return "PREPARING_PRODUCTION_FACTORY"
    if not facts.get("global_readiness"):
        return "WAITING_GLOBAL_READINESS"
    return "READY_TO_AUTO_LAUNCH"


def _pointer_error(value: Any, label: str) -> str | None:
    if not isinstance(value, Mapping):
        return f"{label} must be an evidence pointer"
    raw_path = value.get("path")
    expected = str(value.get("sha256") or "").lower()
    if not isinstance(raw_path, str) or not raw_path.strip():
        return f"{label}.path is missing"
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        return f"{label}.sha256 is invalid"
    path = Path(raw_path)
    path = path if path.is_absolute() else ROOT / path
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(ROOT.resolve())
    except (OSError, ValueError):
        return f"{label}.path must be a regular file under the project root"
    if path.is_symlink() or not resolved.is_file():
        return f"{label}.path must be a non-symlink regular file"
    if sha256(resolved) != expected:
        return f"{label}.sha256 mismatch"
    return None


def validate_launch_request(
    request: Mapping[str, Any], scenario: str
) -> tuple[list[str] | None, list[str]]:
    errors: list[str] = []
    argv = request.get("argv")
    if not (
        request.get("schema_version") == "manual_mysql_sgd_launch_request_v1"
        and request.get("scenario") == scenario
        and request.get("method") == METHOD
        and request.get("formal_scope") == "mysql_only"
        and request.get("ready") is True
        and request.get("preflight_status") == "PASS"
        and isinstance(argv, list) and argv
        and all(isinstance(item, str) and item for item in argv)
    ):
        errors.append("base launch request fields are invalid")
    joined = " ".join(argv) if isinstance(argv, list) else ""
    if "formal_mysql_sgd_campaign.py" not in joined or "experience_table" not in joined:
        errors.append("argv is not the registered experience-table campaign")

    bottleneck = request.get("bottleneck_construction")
    target = TARGET_RESOURCES[scenario]
    if not isinstance(bottleneck, Mapping):
        errors.append("bottleneck_construction is missing")
    else:
        target_util = bottleneck.get("target_utilization")
        non_target = bottleneck.get("non_target_utilization")
        attempts = bottleneck.get("attempts")
        if (
            bottleneck.get("status") != "PASS"
            or bottleneck.get("target_resource") != target
            or not isinstance(target_util, (int, float))
            or isinstance(target_util, bool)
            or not 0.85 <= float(target_util) <= 0.95
        ):
            errors.append("target bottleneck is not proven inside [0.85,0.95]")
        expected_non_targets = RESOURCE_NAMES - {target}
        if (
            not isinstance(non_target, Mapping)
            or set(non_target) != expected_non_targets
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0.30 <= float(value) <= 0.50
                for value in non_target.values()
            )
        ):
            errors.append("non-target resources are not proven inside [0.30,0.50]")
        if (
            not isinstance(attempts, list)
            or not attempts
            or not isinstance(attempts[-1], Mapping)
            or attempts[-1].get("status") != "PASS"
        ):
            errors.append("bottleneck attempts must end in PASS")
        else:
            for index, attempt in enumerate(attempts[:-1]):
                if not isinstance(attempt, Mapping):
                    errors.append(f"bottleneck attempt {index + 1} is invalid")
                    continue
                if attempt.get("status") == "PASS":
                    continue
                adjustment = attempt.get("next_adjustment")
                if (
                    not isinstance(adjustment, Mapping)
                    or adjustment.get("kind") not in {
                        "machine_resource_envelope",
                        "experiment_config_json",
                        "machine_resource_and_config_json",
                    }
                    or not isinstance(adjustment.get("before"), Mapping)
                    or not isinstance(adjustment.get("after"), Mapping)
                ):
                    errors.append(
                        f"failed bottleneck attempt {index + 1} lacks a concrete resource/config adjustment"
                    )
                pointer_error = _pointer_error(
                    adjustment.get("evidence") if isinstance(adjustment, Mapping) else None,
                    f"bottleneck.attempts[{index}].next_adjustment.evidence",
                )
                if pointer_error:
                    errors.append(pointer_error)
        pointer_error = _pointer_error(
            bottleneck.get("evidence"), "bottleneck_construction.evidence"
        )
        if pointer_error:
            errors.append(pointer_error)

    snapshot = request.get("pre_tuning_snapshot")
    if not isinstance(snapshot, Mapping):
        errors.append("pre_tuning_snapshot is missing")
    else:
        if (
            snapshot.get("formal_scope") != "mysql_only"
            or snapshot.get("cross_engine_comparison_prohibited") is not True
            or not isinstance(snapshot.get("captured_at"), str)
            or not str(snapshot.get("captured_at")).strip()
        ):
            errors.append("pre_tuning_snapshot scope/timestamp is invalid")
        configured = snapshot.get("configured_resource_envelope")
        readback = snapshot.get("effective_resource_readback")
        effective = readback.get("values") if isinstance(readback, Mapping) else None
        if not isinstance(configured, Mapping) or set(configured) != ENVELOPE_FIELDS:
            errors.append("configured_resource_envelope fields are incomplete")
        if not isinstance(effective, Mapping) or set(effective) != ENVELOPE_FIELDS:
            errors.append("effective resource read-back fields are incomplete")
        if (
            isinstance(configured, Mapping)
            and isinstance(effective, Mapping)
            and set(configured) == ENVELOPE_FIELDS
            and set(effective) == ENVELOPE_FIELDS
            and dict(configured) != dict(effective)
        ):
            errors.append("configured resource envelope differs from effective read-back")
        for field in ("host_id", "hostname", "device"):
            if not isinstance(snapshot.get(field), str) or not snapshot.get(field):
                errors.append(f"pre_tuning_snapshot.{field} is missing")
        for label, pointer in (
            ("config_json", snapshot.get("config_json")),
            (
                "effective_resource_readback.evidence",
                readback.get("evidence") if isinstance(readback, Mapping) else None,
            ),
            ("process_inventory.evidence", (
                snapshot.get("process_inventory", {}).get("evidence")
                if isinstance(snapshot.get("process_inventory"), Mapping)
                else None
            )),
            ("dynamic_interference.evidence", (
                snapshot.get("dynamic_interference", {}).get("evidence")
                if isinstance(snapshot.get("dynamic_interference"), Mapping)
                else None
            )),
        ):
            pointer_error = _pointer_error(pointer, f"pre_tuning_snapshot.{label}")
            if pointer_error:
                errors.append(pointer_error)
        process_inventory = snapshot.get("process_inventory")
        if (
            not isinstance(process_inventory, Mapping)
            or not isinstance(process_inventory.get("database_and_benchmark_processes"), list)
            or process_inventory.get("complete") is not True
        ):
            errors.append("co-resident database/benchmark process inventory is incomplete")
        interference = snapshot.get("dynamic_interference")
        if (
            not isinstance(interference, Mapping)
            or interference.get("passed") is not True
            or not isinstance(interference.get("window_seconds"), (int, float))
            or float(interference.get("window_seconds", 0)) < 180.0
        ):
            errors.append("rolling dynamic interference gate has not passed")

    return (list(argv) if not errors and isinstance(argv, list) else None), errors


def validated_argv(request: Mapping[str, Any], scenario: str) -> list[str] | None:
    """Compatibility wrapper returning argv only for a fully valid request."""
    return validate_launch_request(request, scenario)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=tuple(SCENARIOS), required=True)
    parser.add_argument("--interval-seconds", type=int, default=30)
    args = parser.parse_args()
    if args.interval_seconds < 1:
        parser.error("--interval-seconds must be positive")
    if not GATE_POLICY.is_file() or GATE_POLICY.is_symlink():
        raise SystemExit(f"pre-tuning gate policy missing or unsafe: {GATE_POLICY}")
    gate_policy_pointer = {
        "path": str(GATE_POLICY.relative_to(ROOT)),
        "sha256": sha256(GATE_POLICY),
    }
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    lane = HISTORY / "manual_controllers" / args.scenario
    lane.mkdir(parents=True, exist_ok=True)
    lock_descriptor = os.open(lane / "controller.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(f"controller already active: {args.scenario}")
        child: subprocess.Popen[str] | None = None
        host02_lease_descriptor: int | None = None
        launched_auto_signature: str | None = None
        while not STOP:
            facts = prerequisites(args.scenario)
            readiness = global_readiness()
            facts["global_readiness"] = readiness.get("ready") is True
            facts["global_readiness_status"] = readiness.get("status", "UNKNOWN")
            scenario_manifest = manifest_scenario(args.scenario)
            apply_manifest_facts(facts, args.scenario, scenario_manifest)
            argv, auto_errors = auto_argv(args.scenario)
            facts["auto_launch_registered"] = argv is not None
            if args.scenario in HOST02_SCENARIOS:
                facts["host02_shared_campaign_lease"] = (
                    host02_lease_descriptor is not None
                )
            facts["pre_tuning_snapshot"] = all(
                facts.get(name)
                for name in (
                    "mysql_envelope",
                    "static_isolation",
                    "dynamic_interference",
                    "r0",
                    "exclusive_lease",
                )
            )
            signature = None
            if argv is not None:
                digest = hashlib.sha256()
                for path in (
                    Path(AUTO_LAUNCH_REGISTRY[args.scenario]["config"]),
                    PRODUCTION_ENTRY,
                ):
                    digest.update(sha256(path).encode("ascii"))
                signature = digest.hexdigest()
            if (
                argv is not None
                and facts["pre_tuning_snapshot"]
                and facts["global_readiness"]
                and child is None
                and signature != launched_auto_signature
            ):
                if args.scenario in HOST02_SCENARIOS:
                    lease = try_acquire_host02_campaign_lease(args.scenario)
                    if lease is None:
                        facts["host02_shared_campaign_lease"] = False
                        auto_errors = [
                            *auto_errors,
                            "host02 JOB/XP shared campaign lease is held",
                        ]
                    else:
                        host02_lease_descriptor = lease
                        facts["host02_shared_campaign_lease"] = True
                if (
                    args.scenario in HOST02_SCENARIOS
                    and host02_lease_descriptor is None
                ):
                    argv = None
            if (
                argv is not None
                and facts["pre_tuning_snapshot"]
                and facts["global_readiness"]
                and child is None
                and signature != launched_auto_signature
            ):
                environment = scenario_manifest.get("environment")
                environment = environment if isinstance(environment, Mapping) else {}
                envelope = scenario_manifest.get("mysql_envelope")
                envelope = envelope if isinstance(envelope, Mapping) else {}
                r0 = scenario_manifest.get("r0_calibration")
                r0 = r0 if isinstance(r0, Mapping) else {}
                atomic_json(
                    lane / "pre_tuning_context.json",
                    {
                        "schema_version": "mysql_pre_tuning_auto_context_v2",
                        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "scenario": args.scenario,
                        "formal_scope": "mysql_only",
                        "cross_engine_comparison_prohibited": True,
                        "gate_policy": gate_policy_pointer,
                        "automatic_launch_signature": signature,
                        "bottleneck_construction": {
                            "status": "PASS",
                            "target_resource": TARGET_RESOURCES[args.scenario],
                            "evidence": r0.get("evidence"),
                        },
                        "pre_tuning_snapshot": {
                            "formal_scope": "mysql_only",
                            "cross_engine_comparison_prohibited": True,
                            "captured_at": datetime.now().astimezone().isoformat(
                                timespec="seconds"
                            ),
                            "host_id": environment.get("host_id"),
                            "hostname": environment.get("host_address"),
                            "device": environment.get("device"),
                            "configured_resource_envelope": envelope.get("normalized"),
                            "effective_resource_readback": envelope.get("readback"),
                            "process_inventory": (
                                scenario_manifest.get("dynamic_interference")
                            ),
                        },
                        "authoritative_scenario_manifest": scenario_manifest,
                        "argv": argv,
                    },
                )
                stdout = (lane / "campaign.stdout.log").open("a", encoding="utf-8")
                stderr = (lane / "campaign.stderr.log").open("a", encoding="utf-8")
                child_environment = os.environ.copy()
                if args.scenario in HOST02_SCENARIOS:
                    child_environment["MYSQL_HOST02_CAMPAIGN_LEASE_HELD"] = "1"
                child = subprocess.Popen(
                    argv,
                    cwd=ROOT,
                    text=True,
                    stdout=stdout,
                    stderr=stderr,
                    env=child_environment,
                )
                launched_auto_signature = signature
            child_rc = child.poll() if child is not None else None
            direct_state = load(lane / "direct_campaign_state.json")
            state = {
                "schema_version": "manual_mysql_sgd_lane_state_v1",
                "controller_contract_version": CONTROLLER_CONTRACT_VERSION,
                "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                "controller_mode": "formal_config_auto_launch",
                "controller_pid": os.getpid(),
                "scenario": args.scenario,
                "method": METHOD,
                "gate_policy": gate_policy_pointer,
                "phase": phase(facts, child),
                "accepted_shrinkage": int(
                    direct_state.get("accepted_shrinkage", 0) or 0
                ),
                "round": direct_state.get("round"),
                "direct_stage": direct_state.get("stage"),
                "prerequisites": facts,
                "launch_request_required": False,
                "legacy_launch_request_ignored": (lane / "launch_request.json").exists(),
                "auto_launch_errors": auto_errors,
                "auto_launch_registration": {
                    "config": str(
                        Path(AUTO_LAUNCH_REGISTRY[args.scenario]["config"]).relative_to(ROOT)
                    ),
                    "service_factory": AUTO_LAUNCH_REGISTRY[args.scenario].get(
                        "service_factory"
                    ),
                    "journal_root": str(
                        AUTO_LAUNCH_REGISTRY[args.scenario]["journal_root"]
                    ),
                },
                "global_readiness": readiness,
                "bottleneck_construction": {
                    "status": "PASS" if facts["bottleneck_construction"] else "NOT_PROVEN",
                    "target_resource": TARGET_RESOURCES[args.scenario],
                    "evidence": (
                        scenario_manifest.get("r0_calibration", {}).get("evidence")
                        if isinstance(scenario_manifest.get("r0_calibration"), Mapping)
                        else None
                    ),
                },
                "pre_tuning_snapshot": None,
                "campaign_pid": child.pid if child is not None and child_rc is None else None,
                "campaign_returncode": child_rc,
                "automatic_launch_signature": launched_auto_signature,
            }
            atomic_json(lane / "state.json", state)
            if child is not None and child_rc is not None:
                child = None
                if host02_lease_descriptor is not None:
                    release_host02_campaign_lease(host02_lease_descriptor)
                    host02_lease_descriptor = None
            deadline = time.monotonic() + args.interval_seconds
            while not STOP and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGTERM)
        return 0
    finally:
        if host02_lease_descriptor is not None:
            release_host02_campaign_lease(host02_lease_descriptor)
        os.close(lock_descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
