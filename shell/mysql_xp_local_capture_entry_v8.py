#!/usr/bin/env python3
"""Explicit compute00-local, capture-only XP entry point.

This entry point never invokes the Algorithm C outer loop.  It prepares one
LocalCampaign and refuses to start HyBench until a fresh local readback,
inventory, rolling non-interference gate, and capture-only contract authorize
the exact content-addressed runtime.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Mapping

_BUNDLE_ROOT = Path(__file__).resolve().parents[1]
for _relative in ("python", "orchestration", "driver", "capture"):
    _candidate = _BUNDLE_ROOT / _relative
    if _candidate.is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

import shell.manual_mysql_sgd_r0_campaign as campaign_module
from mysql_algorithm_c_local_services import (
    LocalCampaign,
    LocalProductionServices,
)
from mysql_xp_capture_preflight import (
    CAPTURE_IO_READ_BPS,
    CAPTURE_IO_WRITE_BPS,
)
from offline_registry.xp_strict_registry import sha256_file, write_artifact
from local_capture.xp_capture_readiness import (
    CAPTURE_CONTRACT_SCHEMA,
    RESOURCE_READBACK_SCHEMA,
    authorize_capture,
)
from local_capture.xp_launch_bundle import build_launch_bundle
from shell.mysql_xp_capture_evidence_collector_v9 import (
    _observer_violations,
    _parse_list,
    _proc_sample,
    _tree_device,
)


EXPECTED_PORT = 65204
TRACE_SCHEMA = "formal_mysql_binding_trace_event_v1"
CAPTURE_SCHEDULE = "deterministic_finite_ap13_at6_fixed_seed_binding_capture"
CAPTURE_AP_SEQUENCE = tuple("AP-%d" % value for value in range(1, 14))
CAPTURE_SPEC_KEYS = {
    "schema_version",
    "formal_scope",
    "cross_engine_comparison_prohibited",
    "launch_token",
    "capture_only",
    "outer_execution_authorized",
    "runtime_bundle_id",
    "runtime_artifacts",
    "bootstrap_paths",
    "frozen_resource_envelope",
    "frozen_workload",
    "resolved_jdk_binary",
    "jdk_version_readback",
    "inventory_path",
    "rolling_gate_path",
    "readiness_wait_seconds",
    "binding_capture_contract_path",
    "binding_capture_contract_sha256",
    "isolation_authority_path",
    "isolation_authority_sha256",
}
BOOTSTRAP_KEYS = {
    "wrapper_class_root",
    "driver_class_root",
    "connector_jar",
    "hybench_fat_jar",
    "statement_file",
}
PRESTART_GATE_SCHEMA = "mysql_xp_capture_prestart_interference_gate_v1"
PRESTART_CPU_CATEGORIES = frozenset(
    {"mysql", "postgresql", "benchmark", "controller"}
)
PRESTART_DEVICE_CATEGORIES = frozenset(
    {"mysql", "postgresql", "benchmark"}
)


def _load_object(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("JSON object required: %s" % path)
    return value


def _load_verified_artifact(path: Path) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("fresh capture artifact is absent or unsafe: %s" % path)
    sidecar = path.with_name(path.name + ".sha256")
    expected = "%s  %s\n" % (sha256_file(path), path.name)
    if (
        sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii") != expected
    ):
        raise ValueError("fresh capture artifact sidecar differs: %s" % path)
    return _load_object(path)


def _owned_process_ids(
    entries: Mapping[int, Mapping[str, Any]], controller_pid: int
) -> set[int]:
    owned = {controller_pid}
    changed = True
    while changed:
        before = len(owned)
        owned.update(
            pid
            for pid, row in entries.items()
            if int(row.get("ppid") or 0) in owned
        )
        changed = len(owned) != before
    current = int(entries.get(controller_pid, {}).get("ppid") or 0)
    while current > 0 and current in entries and current not in owned:
        owned.add(current)
        current = int(entries[current].get("ppid") or 0)
    return owned


def _evaluate_capture_prestart_sample(
    *,
    entries: Mapping[int, Mapping[str, Any]],
    target_pid: int,
    owned_pids: set[int],
    xp_cpus: set[int],
    xp_device: str,
    process_devices: Mapping[int, str],
    tree_authorities: Mapping[str, Any],
    process_tree_ppids: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    cpu_overlap = []
    shared_device = []
    unknown_device = []
    for pid, row in sorted(entries.items()):
        category = str(row.get("category") or "")
        if pid == target_pid or pid in owned_pids or category == "observer":
            continue
        overlap = sorted(
            xp_cpus.intersection(_parse_list(str(row.get("cpuset_cpus") or "")))
        )
        if category in PRESTART_CPU_CATEGORIES and overlap:
            cpu_overlap.append(
                {
                    "pid": pid,
                    "category": category,
                    "overlap_cpus": overlap,
                    "command_sha256": hashlib.sha256(
                        str(row.get("command") or "").encode("utf-8")
                    ).hexdigest(),
                }
            )
        if category not in PRESTART_DEVICE_CATEGORIES:
            continue
        device = _tree_device(
            pid, entries, tree_authorities, process_devices
        )
        summary = {
            "pid": pid,
            "category": category,
            "device_major_minor": device,
            "command_sha256": hashlib.sha256(
                str(row.get("command") or "").encode("utf-8")
            ).hexdigest(),
        }
        if device is None:
            unknown_device.append(summary)
        elif device == xp_device:
            shared_device.append(summary)
    observer_checks = _observer_violations(
        {
            "entries": list(entries.values()),
            "process_tree_ppids": process_tree_ppids or {},
        },
        xp_cpus=xp_cpus,
        xp_device=xp_device,
    )
    observer_violations = [
        row for row in observer_checks if not row["passed"]
    ]
    return {
        "passed": (
            not cpu_overlap
            and not shared_device
            and not unknown_device
            and not observer_violations
        ),
        "cpu_overlap": cpu_overlap,
        "shared_device": shared_device,
        "unknown_relevant_device": unknown_device,
        "observer_validations": observer_checks,
        "observer_isolation_violations": observer_violations,
    }


class _CapturePreStartSession:
    """Put the fresh same-token gate immediately before HyBench Popen."""

    def __init__(self, campaign: Any, session: Any) -> None:
        self._campaign = campaign
        self._session = session
        self._start_attempted = False

    def start(
        self, *, warmup_seconds: int, maximum_measurement_seconds: int
    ) -> None:
        if self._start_attempted:
            raise RuntimeError("capture session start was attempted more than once")
        self._start_attempted = True
        self._campaign.assert_capture_prestart_clear()
        self._campaign.start_capture_runtime_interference_monitor()
        self._session.start(
            warmup_seconds=warmup_seconds,
            maximum_measurement_seconds=maximum_measurement_seconds,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


def _validate_capture_spec(spec: Mapping[str, Any]) -> None:
    envelope = spec.get("frozen_resource_envelope")
    frozen_workload = spec.get("frozen_workload")
    workload = (
        frozen_workload.get("workload")
        if isinstance(frozen_workload, Mapping)
        else None
    )
    if set(spec) != CAPTURE_SPEC_KEYS:
        raise ValueError("capture spec fields differ from the frozen v1 contract")
    if (
        spec.get("schema_version") != "mysql_xp_fresh_local_capture_spec_v1"
        or spec.get("formal_scope") != "mysql_only"
        or spec.get("cross_engine_comparison_prohibited") is not True
        or spec.get("capture_only") is not True
        or spec.get("outer_execution_authorized") is not False
        or len(str(spec.get("runtime_bundle_id") or "")) != 64
        or not str(spec.get("launch_token") or "").strip()
        or not isinstance(spec.get("bootstrap_paths"), Mapping)
        or set(spec["bootstrap_paths"]) != BOOTSTRAP_KEYS
        or not isinstance(spec.get("readiness_wait_seconds"), int)
        or isinstance(spec.get("readiness_wait_seconds"), bool)
        or not 60 <= spec["readiness_wait_seconds"] <= 1800
        or not isinstance(envelope, Mapping)
        or envelope.get("io_read_bps") != CAPTURE_IO_READ_BPS
        or envelope.get("io_write_bps") != CAPTURE_IO_WRITE_BPS
        or not isinstance(workload, Mapping)
        or workload.get("capture_schedule") != CAPTURE_SCHEDULE
        or tuple(workload.get("capture_ap_query_sequence") or ())
        != CAPTURE_AP_SEQUENCE
        or workload.get("formal_replay_schedule")
        != "original_weighted_fixed_seed_stream"
        or workload.get("result_usage") != "binding_registry_only"
        or workload.get("qps_latency_resource_results") != "prohibited"
    ):
        raise ValueError("capture spec scope/bootstrap is invalid")
    sources = spec.get("runtime_artifacts")
    manifest_path = (
        Path(str(sources.get("hybench_fat_jar"))).parent / "manifest.json"
        if isinstance(sources, Mapping) and sources.get("hybench_fat_jar")
        else None
    )
    if (
        manifest_path is None
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
    ):
        raise ValueError("capture spec has no immutable runtime bundle manifest")
    manifest = _load_object(manifest_path)
    if (
        manifest.get("bundle_id") != spec["runtime_bundle_id"]
        or manifest_path.parent.name != spec["runtime_bundle_id"]
        or manifest.get("schema_version")
        != "mysql_xp_v8_immutable_runtime_v1"
        or manifest.get("capture_authorized") is not True
        or manifest.get("capture_io_envelope_is_not_r0") is not True
    ):
        raise ValueError("capture spec runtime bundle identity is invalid")


def _bind_capture_ap_sequence(
    properties: Path, frozen_workload: Mapping[str, Any]
) -> None:
    """Bind exhaustive AP coverage only in the capture launch artifact.

    The ordinary formal campaign generator remains weighted. Capture is not a
    performance result, so exactly one AP1-AP13 operation and one AT1-AT6
    transaction are admitted across all capture workers. Once those finite
    queues are empty, workers drain naturally; no new long AT4/AT5 can start
    near the 120-second graceful-quiescence boundary.
    """
    workload = frozen_workload.get("workload")
    if not isinstance(workload, Mapping):
        raise ValueError("capture workload contract is absent")
    expected_ids = ",".join(str(value) for value in range(1, 14))
    expected_weights = "6,6,6,6,6,6,6,6,6,28,6,6,6"
    lines = properties.read_text(encoding="utf-8").splitlines()
    parsed: Dict[str, str] = {}
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key.strip()] = value.strip()
    if (
        parsed.get("random_seed") != str(frozen_workload.get("workload_seed"))
        or parsed.get("ap_query_ids") != expected_ids
        or parsed.get("ap_query_weights") != expected_weights
        or tuple(workload.get("capture_ap_query_sequence") or ())
        != CAPTURE_AP_SEQUENCE
    ):
        raise ValueError("generated XP properties differ from capture authority")
    if any(
        key in parsed
        for key in (
            "ap_query_sequence",
            "capture_finite_ap_sequence",
            "capture_finite_at_sequence",
        )
    ):
        raise ValueError("base formal XP properties unexpectedly bind a sequence")
    lines.append("ap_query_sequence=" + expected_ids)
    lines.append("capture_finite_ap_sequence=" + expected_ids)
    lines.append("capture_finite_at_sequence=1,2,3,4,5,6")
    properties.write_text("\n".join(lines) + "\n", encoding="utf-8")
    properties.chmod(0o600)


def _validate_isolation_authority(spec: Mapping[str, Any]) -> None:
    path = Path(str(spec["isolation_authority_path"]))
    if (
        path.is_symlink()
        or not path.is_file()
        or sha256_file(path) != spec["isolation_authority_sha256"]
    ):
        raise ValueError("capture isolation authority is absent or changed")
    authority = _load_object(path)
    if (
        authority.get("schema_version")
        != "mysql_xp_job_isolation_authority_v1"
        or authority.get("hostname") != "compute00"
        or authority.get("formal_scope") != "mysql_only"
        or authority.get("cross_engine_comparison_prohibited") is not True
        or authority.get("cpu_disjoint") is not True
        or authority.get("device_disjoint") is not True
        or authority.get("numa_disjoint") is not False
        or authority.get("numa_non_disjoint_declared") is not True
        or authority.get("job_lane", {}).get("device") != "/dev/nvme1n1"
        or authority.get("xp_lane", {}).get("device") != "/dev/sda"
    ):
        raise ValueError("capture isolation authority does not match JOB/XP lanes")


def _verify_jdk_readback(binary: Path, expected: str) -> str:
    completed = subprocess.run(
        [str(binary), "-version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
        timeout=15,
    )
    observed = (completed.stdout + completed.stderr).strip()
    if not expected.strip() or observed != expected.strip():
        raise ValueError("resolved JDK version readback mismatch")
    return observed


def _await_readiness_evidence(
    inventory: Path, rolling: Path, timeout_seconds: int
) -> None:
    deadline = time.monotonic() + timeout_seconds
    required = (
        inventory,
        inventory.with_name(inventory.name + ".sha256"),
        rolling,
        rolling.with_name(rolling.name + ".sha256"),
    )
    while not all(path.is_file() and not path.is_symlink() for path in required):
        if time.monotonic() >= deadline:
            raise ValueError("timed out waiting for external capture-readiness evidence")
        time.sleep(0.25)


def _artifact_map(spec: Mapping[str, Any], properties: Path, statements: Path) -> Dict[str, Path]:
    sources = spec.get("runtime_artifacts")
    if not isinstance(sources, Mapping):
        raise ValueError("runtime_artifacts mapping is required")
    result = {str(role): Path(str(path)) for role, path in sources.items()}
    result["workload_properties"] = properties
    result["statement_file"] = statements
    return result


def _effective_envelope(campaign: LocalCampaign) -> Mapping[str, Any]:
    resource = _load_object(campaign.output / "resource_readback.json")
    effective = resource.get("effective_cgroup")
    mysql = resource.get("effective_mysql")
    if (
        resource.get("verified") is not True
        or not isinstance(effective, Mapping)
        or not isinstance(mysql, Mapping)
    ):
        raise ValueError("local campaign resource readback is not verified")
    return {
        "cpu_quota_cores": float(effective["cpu_quota_us"])
        / float(effective["cpu_period_us"]),
        "cpuset_cpus": str(effective["cpuset_cpus"]),
        "cpuset_mems": str(effective["cpuset_mems"]),
        "innodb_buffer_pool_bytes": int(mysql["innodb_buffer_pool_bytes"]),
        "io_device": str(effective["device_major_minor"]),
        "io_read_bps": int(effective["io_read_bps"]),
        "io_write_bps": int(effective["io_write_bps"]),
        "memory_bytes": int(effective["memory_bytes"]),
        "memory_swap_bytes": int(effective["memory_swap_bytes"]),
        "pids_max": int(effective["pids_max"]),
    }


def _write_capture_readback(
    campaign: LocalCampaign,
    *,
    launch_token: str,
    envelope: Mapping[str, Any],
    mysql_database: str,
    output: Path,
) -> None:
    source = _load_object(campaign.output / "resource_readback.json")
    fixed_mysql = source.get("effective_mysql")
    if not isinstance(fixed_mysql, Mapping) or not fixed_mysql:
        raise ValueError("fixed MySQL settings are absent from fresh readback")
    write_artifact(
        output,
        {
            "schema_version": RESOURCE_READBACK_SCHEMA,
            "hostname": "compute00",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "transport": "local_no_ssh",
            "launch_token": launch_token,
            "source": "fresh_local_readback",
            "observed_after_database_ready": True,
            "readback_verified": True,
            "effective_resource_envelope": dict(envelope),
            "mysql": {
                "host": "127.0.0.1",
                "port": EXPECTED_PORT,
                "database": mysql_database,
            },
            "fixed_mysql_settings_verified": True,
            "fixed_mysql_settings": dict(fixed_mysql),
            "source_resource_readback_path": str(
                (campaign.output / "resource_readback.json").resolve()
            ),
            "source_resource_readback_sha256": sha256_file(
                campaign.output / "resource_readback.json"
            ),
        },
    )


def _write_capture_contract(
    spec: Mapping[str, Any],
    *,
    launch_token: str,
    runtime_sha256: str,
    output: Path,
) -> None:
    source = Path(str(spec["binding_capture_contract_path"]))
    expected = str(spec["binding_capture_contract_sha256"])
    if sha256_file(source) != expected:
        raise ValueError("binding capture contract SHA mismatch")
    source_contract = _load_object(source)
    if (
        source_contract.get("execution_authorized") is not False
        or source_contract.get("benchmark") != "hybench_xp"
        or not isinstance(source_contract.get("required_trace"), Mapping)
    ):
        raise ValueError("source binding capture contract is not fail-closed")
    write_artifact(
        output,
        {
            "schema_version": CAPTURE_CONTRACT_SCHEMA,
            "hostname": "compute00",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "transport": "local_no_ssh",
            "launch_token": launch_token,
            "capture_authorized": True,
            "binding_capture_required": True,
            "binding_authority_preexisting": False,
            "outer_execution_authorized": False,
            "runtime_authority_sha256": runtime_sha256,
            "source_contract_path": str(source.resolve()),
            "source_contract_sha256": expected,
        },
    )


class CaptureLocalCampaign(LocalCampaign):
    capture_spec: Mapping[str, Any]

    def state(self, phase: str, **extra: Any) -> None:
        extra["formal_scope"] = "mysql_only"
        extra["cross_engine_comparison_prohibited"] = True
        extra["transport"] = "local_no_ssh"
        extra["capture_only"] = True
        extra["algorithm_c_authorized"] = False
        super().state(phase, **extra)

    def start_sampler(self) -> None:
        # The external same-token collector is itself a short-lived controller.
        # Start only the resource/database samplers here.  The LocalCampaign
        # runtime monitor is armed synchronously by the pre-start wrapper after
        # the external collector has sealed and the fresh rescan has passed.
        campaign_module.Campaign.start_sampler(self)

    def _prestart_identity_artifacts(
        self,
    ) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
        bundle_dir = self.output / "capture_launch_bundle"
        authorization = _load_verified_artifact(
            bundle_dir / "capture_authorization.json"
        )
        inventory = _load_verified_artifact(
            Path(str(self.capture_spec["inventory_path"]))
        )
        rolling = _load_verified_artifact(
            Path(str(self.capture_spec["rolling_gate_path"]))
        )
        token = str(self.capture_spec["launch_token"])
        if (
            authorization.get("schema_version")
            != "mysql_xp_capture_launch_authorization_v1"
            or authorization.get("status") != "CAPTURE_READY"
            or authorization.get("capture_launch_authorized") is not True
            or authorization.get("outer_execution_authorized") is not False
            or authorization.get("launch_token") != token
            or inventory.get("launch_token") != token
            or rolling.get("launch_token") != token
            or rolling.get("passed") is not True
            or rolling.get("window_complete") is not True
        ):
            raise RuntimeError(
                "capture pre-start artifacts are not one same-token PASS chain"
            )
        return authorization, inventory, rolling

    def assert_capture_prestart_clear(self) -> None:
        authorization, inventory, rolling = self._prestart_identity_artifacts()
        source = _load_object(self.output / "resource_readback.json")
        container_id = str(source.get("container_id") or "")
        if len(container_id) < 12:
            raise RuntimeError("capture pre-start target container id is absent")
        sample = _proc_sample()
        entries = {
            int(row["pid"]): row for row in sample.get("entries", [])
        }
        target_pids = {
            pid
            for pid, row in entries.items()
            if row.get("category") == "mysql"
            and container_id in str(row.get("cgroup") or "")
        }
        process_devices_raw = inventory.get(
            "shared_snapshot_process_devices"
        )
        tree_authorities = inventory.get(
            "shared_snapshot_host_process_tree_authorities"
        )
        if (
            len(target_pids) != 1
            or not isinstance(process_devices_raw, Mapping)
            or not isinstance(tree_authorities, Mapping)
        ):
            raise RuntimeError(
                "capture pre-start target/device authority is absent or ambiguous"
            )
        target_pid = next(iter(target_pids))
        process_devices = {
            int(pid): str(device)
            for pid, device in process_devices_raw.items()
        }
        result = _evaluate_capture_prestart_sample(
            entries=entries,
            target_pid=target_pid,
            owned_pids=_owned_process_ids(entries, os.getpid()),
            xp_cpus=set(
                _parse_list(
                    str(
                        self.capture_spec["frozen_resource_envelope"][
                            "cpuset_cpus"
                        ]
                    )
                )
            ),
            xp_device=str(
                self.capture_spec["frozen_resource_envelope"]["io_device"]
            ),
            process_devices=process_devices,
            tree_authorities=tree_authorities,
            process_tree_ppids=sample.get("process_tree_ppids") or {},
        )
        document = {
            "schema_version": PRESTART_GATE_SCHEMA,
            "status": "PASS" if result["passed"] else "INTERFERENCE_DETECTED",
            "passed": result["passed"],
            "hostname": "compute00",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "transport": "local_no_ssh",
            "launch_token": str(self.capture_spec["launch_token"]),
            "capture_authorization_sha256": sha256_file(
                self.output
                / "capture_launch_bundle/capture_authorization.json"
            ),
            "inventory_sha256": sha256_file(
                Path(str(self.capture_spec["inventory_path"]))
            ),
            "rolling_gate_sha256": sha256_file(
                Path(str(self.capture_spec["rolling_gate_path"]))
            ),
            "external_window_seconds": int(
                rolling.get("window_seconds") or 0
            ),
            "fresh_sample_epoch_ms": int(time.time() * 1000),
            "target_mysql_pid": target_pid,
            "target_container_id": container_id,
            "hybench_process_started": False,
            "evaluation": result,
            "authorization_status": authorization["status"],
        }
        artifact = (
            self.output
            / "capture_launch_bundle/capture_prestart_interference_gate.json"
        )
        write_artifact(artifact, document)
        if result["passed"] is not True:
            raise RuntimeError(
                "capture pre-start interference gate failed before HyBench: "
                "cpu_overlap=%d shared_device=%d unknown_device=%d "
                "observer_violations=%d"
                % (
                    len(result["cpu_overlap"]),
                    len(result["shared_device"]),
                    len(result["unknown_relevant_device"]),
                    len(result["observer_isolation_violations"]),
                )
            )
        self.state(
            "CAPTURE_PRESTART_INTERFERENCE_GATE_PASSED",
            capture_launch_authorized=True,
            capture_prestart_gate="PASS",
            capture_prestart_gate_path=str(artifact.resolve()),
            workload_started=False,
        )

    def start_capture_runtime_interference_monitor(self) -> None:
        if self._runtime_interference_thread is not None:
            raise RuntimeError("capture runtime interference monitor already exists")
        self._runtime_interference_stop.clear()
        with self._runtime_interference_lock:
            self._runtime_interference_started_epoch_ms = int(time.time() * 1000)
            self._runtime_interference_sample_count = 0
            self._runtime_interference_error = None
        # This synchronous CPU rescan is the first runtime sample.  It closes
        # the thread-scheduling gap between the device-aware pre-start sample
        # and HyBench Popen.
        violations = self._preparation_interference_rows()
        with self._runtime_interference_lock:
            self._runtime_interference_sample_count = 1
            if violations:
                self._runtime_interference_error = {
                    "epoch_ms": int(time.time() * 1000),
                    "violations": violations,
                    "error":
                        "benchmark/controller CPU affinity overlaps target lane",
                }
        self._write_runtime_interference_document(
            completed=bool(violations)
        )
        self._assert_runtime_interference_clear()
        self._runtime_interference_thread = threading.Thread(
            target=self._runtime_interference_monitor,
            name="runtime-interference-%s" % self.lane.key,
            daemon=True,
        )
        self._runtime_interference_thread.start()

    def hybench_session(self, workload: str, protocol: Mapping[str, Any]) -> Any:
        if workload != "xp" or self.lane.remote_port != EXPECTED_PORT:
            raise ValueError("capture entry point accepts only local XP port 65204")
        spec = self.capture_spec
        original = {
            "FORMAL_CLASSES": campaign_module.FORMAL_CLASSES,
            "ATTRIBUTION_CLASSES": campaign_module.ATTRIBUTION_CLASSES,
            "CONNECTOR": campaign_module.CONNECTOR,
            "HYBENCH_JAR": campaign_module.HYBENCH_JAR,
            "STATEMENTS": campaign_module.STATEMENTS,
        }
        bootstrap = spec["bootstrap_paths"]
        try:
            campaign_module.FORMAL_CLASSES = Path(str(bootstrap["wrapper_class_root"]))
            campaign_module.ATTRIBUTION_CLASSES = Path(str(bootstrap["driver_class_root"]))
            campaign_module.CONNECTOR = Path(str(bootstrap["connector_jar"]))
            campaign_module.HYBENCH_JAR = Path(str(bootstrap["hybench_fat_jar"]))
            campaign_module.STATEMENTS = Path(str(bootstrap["statement_file"]))
            session = super().hybench_session(workload, protocol)
        finally:
            for name, value in original.items():
                setattr(campaign_module, name, value)

        properties = self.output / "private/formal_hybench.properties"
        statements = self.output / "hybench_runtime/conf/stmt_mysql_formal_case_normalized.toml"
        _bind_capture_ap_sequence(properties, spec["frozen_workload"])
        envelope = _effective_envelope(self)
        frozen = spec.get("frozen_resource_envelope")
        if not isinstance(frozen, Mapping) or dict(frozen) != dict(envelope):
            raise ValueError("fresh local readback differs from frozen capture envelope")
        token = str(spec["launch_token"])
        jdk_binary = Path(str(spec["resolved_jdk_binary"]))
        jdk_version = _verify_jdk_readback(
            jdk_binary, str(spec["jdk_version_readback"])
        )
        bundle_dir = self.output / "capture_launch_bundle"
        bundle = build_launch_bundle(
            bundle_dir,
            _artifact_map(spec, properties, statements),
            spec["frozen_workload"],
            envelope,
            jdk_binary=jdk_binary,
            jdk_version=jdk_version,
            mysql_port=EXPECTED_PORT,
            mysql_database=self.lane.database,
            launch_token=token,
            hostname=socket.gethostname(),
        )
        readback = bundle_dir / "capture_resource_readback.json"
        _write_capture_readback(
            self,
            launch_token=token,
            envelope=envelope,
            mysql_database=self.lane.database,
            output=readback,
        )
        contract = bundle_dir / "capture_contract_authority.json"
        _write_capture_contract(
            spec,
            launch_token=token,
            runtime_sha256=bundle["runtime_authority_sha256"],
            output=contract,
        )
        inventory = Path(str(spec["inventory_path"]))
        rolling = Path(str(spec["rolling_gate_path"]))
        write_artifact(
            bundle_dir / "capture_readiness_request.json",
            {
                "schema_version": "mysql_xp_capture_readiness_request_v1",
                "status": "WAITING_FOR_EXTERNAL_EVIDENCE",
                "hostname": "compute00",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "transport": "local_no_ssh",
                "launch_token": token,
                "target_mysql_ready": True,
                "target_mysql_port": EXPECTED_PORT,
                "resource_readback_path": str(readback.resolve()),
                "resource_readback_sha256": sha256_file(readback),
                "inventory_path": str(inventory),
                "rolling_gate_path": str(rolling),
                "workload_started": False,
                "outer_execution_authorized": False,
            },
        )
        _await_readiness_evidence(
            inventory, rolling, int(spec["readiness_wait_seconds"])
        )
        authorization = authorize_capture(
            bundle_dir / "launch_manifest.json",
            resource_readback_path=readback,
            inventory_path=inventory,
            rolling_gate_path=rolling,
            capture_contract_path=contract,
            output_path=bundle_dir / "capture_authorization.json",
        )
        if authorization["capture_launch_authorized"] is not True:
            raise ValueError(
                "capture readiness failed: %s" % authorization["reasons"]
            )
        if authorization["outer_execution_authorized"] is not False:
            raise AssertionError("capture gate must never authorize Algorithm C outer")
        os.environ["FORMAL_MYSQL_BINDING_RUNTIME_AUTHORITY_SHA256"] = str(
            bundle["runtime_authority_sha256"]
        )
        os.environ["FORMAL_MYSQL_BINDING_CAPTURE_AUTHORIZATION_SHA256"] = (
            sha256_file(bundle_dir / "capture_authorization.json")
        )
        command = tuple(bundle["launch_manifest"]["command_argv_template"])
        session.command_builder = lambda _stop, argv=command: argv
        self.state(
            "CAPTURE_RUNTIME_AUTHORIZED",
            capture_launch_authorized=True,
            outer_execution_authorized=False,
            capture_authorization_path=str(
                (bundle_dir / "capture_authorization.json").resolve()
            ),
        )
        return _CapturePreStartSession(self, session)

    def execute(self) -> None:
        try:
            super().execute()
        finally:
            self.algorithm_c_authorized = False
            self.xap_authorized = False


class CaptureLocalProductionServices(LocalProductionServices):
    def __init__(self, config: Mapping[str, Any], capture_spec: Mapping[str, Any]) -> None:
        _validate_capture_spec(capture_spec)
        _validate_isolation_authority(capture_spec)
        super().__init__(config)
        if self.benchmark != "hybench_xp" or self.lane.remote_port != EXPECTED_PORT:
            raise ValueError("capture service must resolve the compute00 XP lane on port 65204")
        envelope = capture_spec["frozen_resource_envelope"]
        self.lane = replace(
            self.lane,
            read_bps=int(envelope["io_read_bps"]),
            write_bps=int(envelope["io_write_bps"]),
        )
        self.capture_spec = copy.deepcopy(dict(capture_spec))

    def new_capture_campaign(self, run_id: str) -> CaptureLocalCampaign:
        campaign = CaptureLocalCampaign(
            self.lane,
            self.points / run_id,
            run_id,
            candidate_costs=None,
            measurement_seconds=int(self.config.get("measurement_seconds", 600)),
            minimum_measurement_seconds=int(
                self.config.get("measurement_seconds", 600)
            ),
            minimum_client_completions=int(
                self.config.get("minimum_client_completions", 0)
            ),
            require_r0_profile_gate=True,
        )
        campaign.capture_spec = self.capture_spec
        return campaign


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--capture-spec", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if socket.gethostname() != "compute00":
        raise SystemExit("local XP capture is restricted to compute00")
    config = dict(_load_object(args.config))
    spec = dict(_load_object(args.capture_spec))
    _validate_capture_spec(spec)
    if (
        config.get("formal_scope") != "mysql_only"
        or config.get("cross_engine_comparison_prohibited") is not True
        or config.get("execution_authorized") is not False
        or spec.get("launch_token") != args.run_id
        or spec.get("capture_only") is not True
        or spec.get("outer_execution_authorized") is not False
    ):
        raise SystemExit("capture config/spec identity is invalid")
    services = CaptureLocalProductionServices(config, spec)
    campaign = services.new_capture_campaign(args.run_id)
    trace = campaign.output / "formal_binding_trace.jsonl"
    if trace.exists() or trace.is_symlink():
        raise SystemExit("fresh trace path already exists")
    os.environ["FORMAL_MYSQL_BINDING_TRACE_PATH"] = str(trace)
    os.environ["FORMAL_MYSQL_BINDING_TRACE_RUN_ID"] = args.run_id
    os.environ["FORMAL_MYSQL_BINDING_TRACE_SCHEMA"] = TRACE_SCHEMA
    try:
        campaign.execute()
        return 0
    finally:
        campaign.cleanup()


if __name__ == "__main__":
    raise SystemExit(_main())
