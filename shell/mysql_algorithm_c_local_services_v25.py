"""compute00-local production services for MySQL Algorithm C.

This module reuses the frozen formal campaign and replay gates.  It only
replaces SSH transport with local process execution and binds every fresh
planner/replay container to an explicitly frozen local lane.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
from typing import Any, Mapping, Sequence

import mysql_algorithm_b_production_services as remote_services
import shell.manual_mysql_sgd_r0_campaign as campaign_module
from mysql_algorithm_b_production_services import (
    BENCHMARK_LANES,
    ProductionServices,
    REPLAY_PORT_OFFSET,
    _utc_now,
)
from shell.manual_mysql_sgd_r0_campaign import (
    Campaign, Lane, ROOT, atomic_json, run,
)
from shell.mysql_process_classification_v10 import classify_process


LOCAL_PROJECT = Path("/disk3/resource_adaptive_mysql_algorithm_c_local")
JOB_STORAGE_V2_PROJECT = Path(
    "/disk1/resource_adaptive_mysql_algorithm_c_local_v2/job"
)
TPCH_STORAGE_V2_PROJECT = Path(
    "/disk2/resource_adaptive_mysql_algorithm_c_local_v2/tpch"
)
_BUNDLED_TPCH_BENCHBASE_JAR = (
    ROOT
    / "formal_benchbase_runtime/target/"
    "benchbase-2023-SNAPSHOT-formal-binding-trace-candidate.jar"
)
if _BUNDLED_TPCH_BENCHBASE_JAR.is_file():
    STAGED_TPCH_BENCHBASE_JAR = _BUNDLED_TPCH_BENCHBASE_JAR
    STAGED_TPCH_BENCHBASE_JAR_SHA256 = (
        "6f3fa2c886a2fb8ed8149825b40e337e835e44ebbaf6c667866d73ffed22890e"
    )
else:
    STAGED_TPCH_BENCHBASE_JAR = Path(
        "/tmp/mysql_tpch_q15_lock_v1/target/"
        "benchbase-2023-SNAPSHOT-formal-binding-trace-candidate.jar"
    )
    STAGED_TPCH_BENCHBASE_JAR_SHA256 = (
        "d99ec847352488fc925a3f686988c356650ac917b1e76e3a1ffae127ec9f1c73"
    )


def _compress_cpuset(cpus: set[int]) -> str:
    values = sorted(cpus)
    if not values:
        return ""
    ranges: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)


def _numa_policies(pid: int) -> list[str]:
    return sorted(
        {
            fields[1]
            for line in Path(f"/proc/{pid}/numa_maps").read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            if len(fields := line.split()) > 1
        }
    )


_DEVICE_AUTHORITY_FLAGS = frozenset({
    "--data-dir",
    "--datadir",
    "--output",
    "--output-dir",
    "--output-root",
    "--runtime-root",
})
_DEVICE_AUTHORITY_ENV = re.compile(
    r"(?:BENCHBASE_RUNTIME_ROOT|DATA_DIR|DATADIR|OUTPUT_ROOT|RUNTIME_ROOT)"
    r"=(/[^\s'\";]+)"
)


def _path_device_evidence(path: str, authority: str) -> dict[str, str] | None:
    candidate = Path(path)
    if not candidate.is_absolute():
        return None
    try:
        resolved = candidate.resolve(strict=True)
        metadata = resolved.stat()
    except (FileNotFoundError, OSError, RuntimeError):
        return None
    if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
        return None
    return {
        "authority": authority,
        "path": str(resolved),
        "device_major_minor":
            f"{os.major(metadata.st_dev)}:{os.minor(metadata.st_dev)}",
    }


def _direct_process_device_evidence(
    argv: Sequence[str], cwd: str | None,
) -> list[dict[str, str]]:
    """Resolve only cwd, explicit workload roots and container bind sources."""
    evidence: list[dict[str, str]] = []

    def add(path: str, authority: str) -> None:
        row = _path_device_evidence(path, authority)
        if row is not None and row not in evidence:
            evidence.append(row)

    if cwd:
        add(cwd, "proc_cwd")
    for index, word in enumerate(argv):
        if word in _DEVICE_AUTHORITY_FLAGS and index + 1 < len(argv):
            add(argv[index + 1], f"argv_flag:{word}")
        for flag in _DEVICE_AUTHORITY_FLAGS:
            prefix = f"{flag}="
            if word.startswith(prefix):
                add(word[len(prefix):], f"argv_flag:{flag}")
        for match in _DEVICE_AUTHORITY_ENV.finditer(word):
            add(match.group(1), "argv_environment_root")
        if (
            word in {"-v", "--volume", "--mount"}
            and index + 1 < len(argv)
        ):
            mount = argv[index + 1]
            source = mount.split(":", 1)[0]
            if source.startswith("type=bind,"):
                fields = dict(
                    item.split("=", 1)
                    for item in source.split(",")
                    if "=" in item
                )
                source = fields.get("src") or fields.get("source") or ""
            add(source, f"container_mount:{word}")
        elif word.startswith(("--volume=", "--mount=")):
            flag, mount = word.split("=", 1)
            source = mount.split(":", 1)[0]
            if mount.startswith("type=bind,"):
                fields = dict(
                    item.split("=", 1)
                    for item in mount.split(",")
                    if "=" in item
                )
                source = fields.get("src") or fields.get("source") or ""
            add(source, f"container_mount:{flag}")
        elif word.startswith("/") and ":/" in word:
            add(word.split(":", 1)[0], "container_bind_argument")
    return evidence


def _interference_violations(
    processes: Mapping[int, Mapping[str, Any]],
    *,
    owned: set[int],
    target_cpus: set[int],
    target_device_major_minor: str | None,
) -> list[dict[str, Any]]:
    """Fail closed on CPU overlap, target-device use, or unknown device."""
    relevant = {
        pid
        for pid, row in processes.items()
        if pid not in owned
        and row.get("category") in {"benchmark", "controller"}
    }
    devices = {
        pid: {
            str(item["device_major_minor"])
            for item in (processes[pid].get("direct_device_evidence") or [])
            if item.get("device_major_minor")
        }
        for pid in relevant
    }
    changed = True
    while changed:
        changed = False
        for pid in relevant:
            ppid = int(processes[pid].get("ppid") or 0)
            if ppid not in relevant:
                continue
            union = devices[pid] | devices[ppid]
            if union != devices[pid] or union != devices[ppid]:
                devices[pid] = set(union)
                devices[ppid] = set(union)
                changed = True

    violations: list[dict[str, Any]] = []
    for pid in sorted(relevant):
        row = processes[pid]
        raw_cpuset = str(row.get("cpuset_cpus") or "")
        try:
            allowed = remote_services._expand_cpuset(raw_cpuset)
        except (TypeError, ValueError):
            allowed = set()
        overlap = target_cpus.intersection(allowed)
        observed_devices = sorted(devices[pid])
        reasons: list[str] = []
        if overlap:
            reasons.append("cpu_overlap")
        if target_device_major_minor is None:
            reasons.append("target_device_authority_unknown")
            device_relation = "UNKNOWN_TARGET_DEVICE"
        elif not observed_devices:
            reasons.append("process_device_authority_unknown")
            device_relation = "UNKNOWN_PROCESS_DEVICE"
        elif target_device_major_minor in observed_devices:
            reasons.append("target_device_conflict")
            device_relation = "TARGET_DEVICE_CONFLICT"
        else:
            device_relation = "KNOWN_DISJOINT"
        if not reasons:
            continue
        violations.append({
            **row,
            "overlap_cpus": _compress_cpuset(set(overlap)),
            "device_major_minors": observed_devices,
            "target_device_major_minor": target_device_major_minor,
            "device_relation": device_relation,
            "interference_reasons": reasons,
            "command_sha256": hashlib.sha256(
                str(row["command"]).encode("utf-8")
            ).hexdigest(),
        })
    return violations


@dataclass(frozen=True)
class LocalStorageProfile:
    profile_id: str
    schema_version: str
    project: str
    template: str
    manifest: str
    device: str
    device_major_minor: str
    filesystem: str
    fresh_copy_mode: str
    tree_hash_algorithm: str


JOB_STORAGE_V2 = LocalStorageProfile(
    profile_id="job_local_storage_v2_nvme_fullcopy",
    schema_version="mysql_job_local_storage_v2",
    project=str(JOB_STORAGE_V2_PROJECT),
    template="/disk1/ra_mysql_isolated_v2/job-base",
    manifest="/disk1/ra_mysql_isolated_v2/job-storage-v2.manifest.json",
    device="/dev/nvme1n1",
    device_major_minor="259:5",
    filesystem="ext4",
    fresh_copy_mode="full_physical_copy_no_overlay_no_reflink",
    tree_hash_algorithm="sha256sum_regular_files_relative_v1",
)

TPCH_STORAGE_V2 = LocalStorageProfile(
    profile_id="tpch_local_storage_v2_nvme_fullcopy",
    schema_version="mysql_tpch_local_storage_v2",
    project=str(TPCH_STORAGE_V2_PROJECT),
    template="/disk2/ra_mysql_isolated_v2/tpch-memory",
    manifest="/disk2/ra_mysql_isolated_v2/tpch-storage-v2.manifest.json",
    device="/dev/nvme2n1",
    device_major_minor="259:6",
    filesystem="ext4",
    fresh_copy_mode="full_physical_copy_no_overlay_no_reflink",
    tree_hash_algorithm="sha256sum_regular_files_relative_v1",
)

LOCAL_STORAGE_PROFILES = {
    "job": JOB_STORAGE_V2,
    "tpch": TPCH_STORAGE_V2,
}

LOCAL_LANES = {
    "job": Lane(
        "job", "job_memory", "job", "local://compute00", "compute00",
        JOB_STORAGE_V2.project,
        JOB_STORAGE_V2.template,
        JOB_STORAGE_V2.device, "176-183,432-439", "1", 3, 6442450944, 512,
        1205862400, 1205862400, 65202, "imdb_job", "memory", "Com_select",
        tuple(remote_services.LANES["job"].experience),
    ),
    "xp": Lane(
        "xp", "hybench_xp_io", "hybench_xp", "local://compute00", "compute00",
        str(LOCAL_PROJECT / "xp"),
        "/disk3/ra_mysql_isolated/hybench-base",
        "/dev/sda", "184-199,440-455", "1", 16, 8589934592, 1024,
        104857600, 104857600, 65204, "hybench_sf10x", "io",
        "Com_select+Com_insert+Com_update+Com_delete",
        tuple(remote_services.LANES["xp"].experience),
    ),
    "tpch": Lane(
        "tpch", "tpch_memory", "tpch", "local://compute00", "compute00",
        TPCH_STORAGE_V2.project,
        TPCH_STORAGE_V2.template,
        TPCH_STORAGE_V2.device, "200-207,456-463", "1", 3, 6442450944, 512,
        1205862400, 1205862400, 65203, "tpch", "memory", "Com_select",
        tuple(remote_services.LANES["tpch"].experience),
    ),
    "tpcc": Lane(
        "tpcc", "tpcc_cpu", "tpcc", "local://compute00", "compute00",
        str(LOCAL_PROJECT / "tpcc"),
        "/disk3/ra_mysql_isolated/tpcc-cpu",
        "/dev/sda", "208-215,464-471", "1", 4, 8589934592, 512,
        1205862400, 1205862400, 65206, "tpcc", "cpu",
        "Com_insert+Com_update+Com_delete",
        tuple(remote_services.LANES["tpcc"].experience),
    ),
}

# Kept only to identify and reject old artifacts.  It is never selected by
# LocalProductionServices and must not be merged with storage-v2 evidence.
LEGACY_JOB_STORAGE_V1 = {
    "profile_id": "job_local_storage_v1_disk3_overlay",
    "project": str(LOCAL_PROJECT / "job"),
    "template": "/disk3/ra_mysql_isolated/job-base",
    "device": "/dev/sda",
    "fresh_copy_mode": "overlayfs_read_only_lower_per_point_upper",
}


class LocalCampaign(Campaign):
    """Campaign whose control and sampler processes run directly on compute00."""

    def __init__(
        self,
        *args: Any,
        storage_profile: LocalStorageProfile | None = None,
        storage_manifest_sha256: str = "",
        tpch_terminals: int = 2,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.tpch_terminals = tpch_terminals
        if self.lane.key == "tpch" and self.tpch_terminals < 1:
            raise ValueError("TPC-H terminals must be positive")
        self.storage_profile = storage_profile
        self.storage_manifest_sha256 = storage_manifest_sha256.strip().lower()
        self.storage_manifest_document: dict[str, Any] | None = None
        self._runtime_interference_stop = threading.Event()
        self._runtime_interference_thread: threading.Thread | None = None
        self._runtime_interference_lock = threading.Lock()
        self._runtime_interference_started_epoch_ms: int | None = None
        self._runtime_interference_sample_count = 0
        self._runtime_interference_error: dict[str, Any] | None = None
        self._runtime_interference_ready = threading.Event()
        self._runtime_interference_ready_epoch_ms: int | None = None
        self._runtime_interference_ready_timeout_seconds = 5.0
        self.prepare_timeout_seconds = (
            1200
            if (
                self.lane.key in {"job", "tpch"}
                and storage_profile is not None
                and storage_profile.fresh_copy_mode
                == "full_physical_copy_no_overlay_no_reflink"
            )
            else 600
        )

    def state(self, phase: str, **extra: Any) -> None:
        if (
            self.lane.key == "tpch"
            and _BUNDLED_TPCH_BENCHBASE_JAR.is_file()
        ):
            controller_policies = _numa_policies(os.getpid())
            if controller_policies != ["bind:1"]:
                raise RuntimeError(
                    "bundled TPC-H controller lacks strict NUMA node-1 policy"
                )
            extra["controller_numa_policies"] = controller_policies
            workload_pid = extra.get("workload_pid")
            if isinstance(workload_pid, int) and workload_pid > 1:
                workload_policies = _numa_policies(workload_pid)
                if workload_policies != ["bind:1"]:
                    raise RuntimeError(
                        "bundled TPC-H workload lacks strict NUMA node-1 policy"
                    )
                extra["workload_numa_policies"] = workload_policies
        super().state(phase, **extra)

    def tpch_session(
        self,
        protocol: Mapping[str, Any],
        *,
        query_weights: str | None = None,
        terminals: int | None = None,
    ) -> Any:
        return super().tpch_session(
            protocol,
            query_weights=query_weights,
            terminals=self.tpch_terminals if terminals is None else terminals,
        )

    def _validated_storage_manifest(self) -> Mapping[str, Any]:
        profile = self.storage_profile
        if profile is None:
            return {}
        bound_profile = LOCAL_STORAGE_PROFILES.get(self.lane.key)
        if bound_profile != profile:
            raise RuntimeError(
                f"{self.lane.key} storage-v2 profile is not bound to this lane"
            )
        if (
            self.lane.project != profile.project
            or self.lane.template != profile.template
            or self.lane.device != profile.device
        ):
            raise RuntimeError(
                f"{self.lane.key} storage-v2 lane/profile binding differs"
            )
        expected_sha = self.storage_manifest_sha256
        if re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None:
            raise RuntimeError(
                f"{self.lane.key} storage-v2 manifest SHA-256 is absent or invalid"
            )
        manifest_path = Path(profile.manifest)
        try:
            payload = manifest_path.read_bytes()
        except OSError as exc:
            raise RuntimeError(
                f"{self.lane.key} storage-v2 manifest is unavailable: {manifest_path}"
            ) from exc
        actual_sha = hashlib.sha256(payload).hexdigest()
        if actual_sha != expected_sha:
            raise RuntimeError(
                f"{self.lane.key} storage-v2 manifest SHA-256 differs from frozen config"
            )
        try:
            document = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"{self.lane.key} storage-v2 manifest is invalid JSON"
            ) from exc
        if not isinstance(document, dict):
            raise RuntimeError(
                f"{self.lane.key} storage-v2 manifest is not a mapping"
            )
        expected = {
            "schema_version": profile.schema_version,
            "profile_id": profile.profile_id,
            "template_path": profile.template,
            "device": profile.device,
            "device_major_minor": profile.device_major_minor,
            "filesystem": profile.filesystem,
            "fresh_copy_mode": profile.fresh_copy_mode,
            "tree_hash_algorithm": profile.tree_hash_algorithm,
            "sealed_read_only": True,
            "clean_shutdown": True,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "overlay": False,
            "reflink": False,
        }
        differences = {
            key: (document.get(key), value)
            for key, value in expected.items()
            if document.get(key) != value
        }
        tree_sha = document.get("template_tree_sha256")
        if re.fullmatch(r"[0-9a-f]{64}", str(tree_sha or "")) is None:
            differences["template_tree_sha256"] = (tree_sha, "64 lowercase hex")
        if differences:
            raise RuntimeError(
                f"{self.lane.key} storage-v2 manifest contract differs: "
                + json.dumps(differences, sort_keys=True)
            )
        self.storage_manifest_document = document
        return document

    def _preparation_interference_rows(self) -> list[dict[str, Any]]:
        """Return external CPU/device conflicts, failing unknown device closed."""
        processes: dict[int, dict[str, Any]] = {}
        for directory in Path("/proc").iterdir():
            if not directory.name.isdigit():
                continue
            try:
                raw_cmdline = (directory / "cmdline").read_bytes()
                argv = [
                    item.decode("utf-8", errors="replace")
                    for item in raw_cmdline.split(b"\0")
                    if item
                ]
                command = " ".join(argv).strip()
                if not command:
                    continue
                stat_text = (directory / "stat").read_text(encoding="ascii")
                fields = stat_text[stat_text.rfind(")") + 2 :].split()
                status = {
                    key: value.strip()
                    for line in (directory / "status").read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                    if ":" in line
                    for key, value in [line.split(":", 1)]
                }
                pid = int(directory.name)
                try:
                    process_cwd = os.readlink(directory / "cwd")
                except (
                    FileNotFoundError,
                    PermissionError,
                    ProcessLookupError,
                    OSError,
                ):
                    # Keep the classifiable process in the inventory.  If its
                    # argv also has no device authority, the gate rejects it
                    # as UNKNOWN_PROCESS_DEVICE rather than silently skipping
                    # a relevant controller or benchmark.
                    process_cwd = None
                processes[pid] = {
                    "pid": pid,
                    "ppid": int(fields[1]),
                    "start_ticks": int(fields[19]),
                    "category": classify_process(
                        command,
                        argv=argv,
                        cwd=process_cwd,
                    ),
                    "command": command,
                    "cwd": process_cwd,
                    "direct_device_evidence":
                        _direct_process_device_evidence(argv, process_cwd),
                    "cpuset_cpus": status.get("Cpus_allowed_list", ""),
                    "cpuset_mems": status.get("Mems_allowed_list", ""),
                }
            except (
                FileNotFoundError,
                PermissionError,
                ProcessLookupError,
                ValueError,
            ):
                continue

        owned = {os.getpid()}
        changed = True
        while changed:
            before = len(owned)
            owned.update(
                pid
                for pid, row in processes.items()
                if int(row["ppid"]) in owned
            )
            changed = len(owned) != before
        current = os.getppid()
        while current > 0 and current in processes and current not in owned:
            owned.add(current)
            current = int(processes[current]["ppid"])

        profile = getattr(self, "storage_profile", None)
        target_device_major_minor = (
            profile.device_major_minor
            if profile is not None
            else None
        )
        if target_device_major_minor is None:
            try:
                metadata = os.stat(self.lane.device)
                if stat.S_ISBLK(metadata.st_mode):
                    target_device_major_minor = (
                        f"{os.major(metadata.st_rdev)}:"
                        f"{os.minor(metadata.st_rdev)}"
                    )
            except OSError:
                target_device_major_minor = None
        return _interference_violations(
            processes,
            owned=owned,
            target_cpus=remote_services._expand_cpuset(self.lane.cpuset),
            target_device_major_minor=target_device_major_minor,
        )

    def _preparation_interference_gate(
        self,
        *,
        window_seconds: int = 60,
        interval_seconds: float = 1.0,
    ) -> Mapping[str, Any]:
        if window_seconds <= 0 or interval_seconds <= 0:
            raise ValueError("preparation interference gate window is invalid")
        path = self.output / "preparation_interference_gate_v1.json"
        if path.is_file() and not path.is_symlink():
            prior = json.loads(path.read_text(encoding="utf-8"))
            if prior.get("passed") is True:
                return prior
            raise RuntimeError("prior preparation interference gate failed closed")
        started = time.time()
        samples: list[dict[str, Any]] = []
        deadline = time.monotonic() + float(window_seconds)
        while True:
            violations = self._preparation_interference_rows()
            sample = {
                "epoch_ms": int(time.time() * 1000),
                "violations": violations,
            }
            samples.append(sample)
            if violations:
                document = {
                    "schema_version":
                        "mysql_local_preparation_interference_gate_v1",
                    "scenario": self.lane.scenario,
                    "formal_scope": "mysql_only",
                    "cross_engine_comparison_prohibited": True,
                    "passed": False,
                    "status": "INTERFERENCE_DETECTED",
                    "controller_pid": os.getpid(),
                    "target_cpuset": self.lane.cpuset,
                    "target_device": self.lane.device,
                    "window_seconds": window_seconds,
                    "started_epoch_ms": int(started * 1000),
                    "completed_epoch_ms": int(time.time() * 1000),
                    "samples": samples,
                    "error":
                        "benchmark/controller CPU/device authority conflicts "
                        "with target lane",
                }
                atomic_json(path, document)
                raise RuntimeError(
                    "local preparation interference gate failed closed: "
                    + ", ".join(
                        f"pid={row['pid']}:{row['category']}:"
                        f"cpu={row.get('overlap_cpus', '')}:"
                        f"device={row.get('device_relation', 'UNKNOWN')}"
                        for row in violations
                    )
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(interval_seconds, remaining))
        document = {
            "schema_version": "mysql_local_preparation_interference_gate_v1",
            "scenario": self.lane.scenario,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "passed": True,
            "status": "PASS",
            "controller_pid": os.getpid(),
            "target_cpuset": self.lane.cpuset,
            "target_device": self.lane.device,
            "window_seconds": window_seconds,
            "started_epoch_ms": int(started * 1000),
            "completed_epoch_ms": int(time.time() * 1000),
            "sample_count": len(samples),
            "samples": samples,
        }
        atomic_json(path, document)
        return document

    def _runtime_interference_document(self, *, completed: bool) -> dict[str, Any]:
        with self._runtime_interference_lock:
            error = copy.deepcopy(self._runtime_interference_error)
            sample_count = self._runtime_interference_sample_count
            started_epoch_ms = self._runtime_interference_started_epoch_ms
            ready_epoch_ms = self._runtime_interference_ready_epoch_ms
        return {
            "schema_version": "mysql_local_runtime_interference_gate_v2",
            "scenario": self.lane.scenario,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "passed": completed and error is None,
            "status": (
                "INTERFERENCE_DETECTED"
                if error is not None
                else (
                    "PASS"
                    if completed
                    else ("READY" if ready_epoch_ms is not None else "IN_PROGRESS")
                )
            ),
            "controller_pid": os.getpid(),
            "target_cpuset": self.lane.cpuset,
            "target_device": self.lane.device,
            "started_epoch_ms": started_epoch_ms,
            "ready_epoch_ms": ready_epoch_ms,
            "completed_epoch_ms": int(time.time() * 1000) if completed else None,
            "sample_count": sample_count,
            "minimum_clean_samples_before_workload": 1,
            "cpu_and_device_gate_required": True,
            "unknown_process_device_fails_closed": True,
            "first_violation": error,
        }

    def _write_runtime_interference_document(self, *, completed: bool) -> None:
        atomic_json(
            self.output / "runtime_interference_gate_v1.json",
            self._runtime_interference_document(completed=completed),
        )

    def _runtime_interference_monitor(self) -> None:
        while not self._runtime_interference_stop.is_set():
            epoch_ms = int(time.time() * 1000)
            try:
                violations = self._preparation_interference_rows()
            except BaseException as error:
                violations = []
                with self._runtime_interference_lock:
                    if self._runtime_interference_error is None:
                        self._runtime_interference_error = {
                            "epoch_ms": epoch_ms,
                            "violations": [],
                            "error":
                                "runtime CPU/device interference scan failed "
                                f"closed: {type(error).__name__}: {error}",
                        }
                self._write_runtime_interference_document(completed=True)
                self._runtime_interference_ready.set()
                return
            with self._runtime_interference_lock:
                self._runtime_interference_sample_count += 1
                if violations and self._runtime_interference_error is None:
                    self._runtime_interference_error = {
                        "epoch_ms": epoch_ms,
                        "violations": violations,
                        "error":
                            "benchmark/controller CPU/device authority "
                            "conflicts with target lane",
                    }
                elif self._runtime_interference_ready_epoch_ms is None:
                    self._runtime_interference_ready_epoch_ms = epoch_ms
            if violations:
                self._write_runtime_interference_document(completed=True)
                self._runtime_interference_ready.set()
                return
            self._write_runtime_interference_document(completed=False)
            self._runtime_interference_ready.set()
            self._runtime_interference_stop.wait(1.0)

    def _assert_runtime_interference_clear(self) -> None:
        with self._runtime_interference_lock:
            error = copy.deepcopy(self._runtime_interference_error)
        if error is not None:
            details = ", ".join(
                f"pid={row['pid']}:{row['category']}:"
                f"cpu={row.get('overlap_cpus', '')}:"
                f"device={row.get('device_relation', 'UNKNOWN')}"
                for row in error["violations"]
            )
            raise RuntimeError(
                "local runtime interference gate failed closed: "
                + (details or str(error.get("error") or "unknown scan error"))
            )

    def _await_runtime_interference_ready(self) -> None:
        timeout = float(self._runtime_interference_ready_timeout_seconds)
        if timeout <= 0:
            raise RuntimeError("runtime interference READY timeout is invalid")
        if not self._runtime_interference_ready.wait(timeout):
            epoch_ms = int(time.time() * 1000)
            with self._runtime_interference_lock:
                if self._runtime_interference_error is None:
                    self._runtime_interference_error = {
                        "epoch_ms": epoch_ms,
                        "violations": [],
                        "error":
                            "runtime CPU/device interference monitor did not "
                            f"produce a first sample within {timeout} seconds",
                    }
            self._runtime_interference_stop.set()
            self._write_runtime_interference_document(completed=True)
            self._assert_runtime_interference_clear()
        self._assert_runtime_interference_clear()
        with self._runtime_interference_lock:
            sample_count = self._runtime_interference_sample_count
            ready_epoch_ms = self._runtime_interference_ready_epoch_ms
        if sample_count < 1 or ready_epoch_ms is None:
            raise RuntimeError(
                "runtime interference gate is not READY after first sample"
            )
        self._write_runtime_interference_document(completed=False)

    def _validated_storage_readback(
        self, lines: list[str],
    ) -> Mapping[str, str]:
        profile = self.storage_profile
        manifest = self.storage_manifest_document
        if profile is None or manifest is None:
            raise RuntimeError(
                f"{self.lane.key} storage-v2 contract is not initialized"
            )
        values: dict[str, str] = {}
        for line in lines:
            if not line.startswith("RA_STORAGE_"):
                continue
            name, value = line.split("=", 1)
            if name in values:
                raise RuntimeError(f"duplicate storage readback marker: {name}")
            values[name] = value
        tree_sha = str(manifest["template_tree_sha256"])
        expected = {
            "RA_STORAGE_PROFILE_ID": profile.profile_id,
            "RA_STORAGE_DEVICE": profile.device,
            "RA_STORAGE_DEVICE_MAJOR_MINOR": profile.device_major_minor,
            "RA_STORAGE_FSTYPE": profile.filesystem,
            "RA_STORAGE_FRESH_COPY_MODE": profile.fresh_copy_mode,
            "RA_STORAGE_TEMPLATE_TREE_SHA256": tree_sha,
            "RA_STORAGE_RUNTIME_TREE_SHA256": tree_sha,
            "RA_STORAGE_OVERLAY": "disabled",
            "RA_STORAGE_REFLINK": "disabled",
        }
        if values != expected:
            raise RuntimeError(
                f"{self.lane.key} storage-v2 effective readback differs: "
                + json.dumps(
                    {
                        key: (values.get(key), value)
                        for key, value in expected.items()
                        if values.get(key) != value
                    },
                    sort_keys=True,
                )
            )
        return values

    def _validated_runtime_datadir_bind(
        self, inspect: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        mysql_mounts = [
            mount for mount in inspect.get("Mounts", [])
            if mount.get("Destination") == "/var/lib/mysql"
        ]
        if (
            len(mysql_mounts) != 1
            or mysql_mounts[0].get("Source") != self.remote_data
            or mysql_mounts[0].get("Type") != "bind"
            or mysql_mounts[0].get("RW") is not True
        ):
            raise RuntimeError(
                f"{self.lane.key} storage-v2 final datadir bind readback differs"
            )
        return mysql_mounts[0]

    def _validate_profile_io_device_readback(
        self,
        resource: Mapping[str, Any],
        effective: Mapping[str, Any],
    ) -> None:
        profile = self.storage_profile
        if profile is None:
            return
        observed = {
            "resource": (
                resource.get("effective_cgroup", {})
                .get("device_major_minor")
            ),
            "effective": (
                effective.get("effective", {})
                .get("io_device_major_minor")
            ),
        }
        if any(value != profile.device_major_minor for value in observed.values()):
            raise RuntimeError(
                f"{self.lane.key} storage-v2 IO cap device readback differs: "
                + json.dumps(observed, sort_keys=True)
            )

    def state(self, phase: str, **extra: Any) -> None:
        expected_affinity = remote_services._expand_cpuset(self.lane.cpuset)
        controller_affinity = set(os.sched_getaffinity(0))
        if controller_affinity != expected_affinity:
            raise RuntimeError(
                f"{self.lane.key} controller host affinity differs: "
                f"expected={self.lane.cpuset} "
                f"observed={_compress_cpuset(controller_affinity)}"
            )
        workload_pid = extra.get("workload_pid")
        workload_affinity: set[int] | None = None
        if workload_pid is not None:
            workload_affinity = set(os.sched_getaffinity(int(workload_pid)))
            if workload_affinity != expected_affinity:
                raise RuntimeError(
                    f"{self.lane.key} workload host affinity differs: "
                    f"pid={workload_pid} expected={self.lane.cpuset} "
                    f"observed={_compress_cpuset(workload_affinity)}"
                )
        super().state(phase, **extra)
        document = json.loads(self.state_path.read_text(encoding="utf-8"))
        non_interference = dict(document.get("non_interference") or {})
        job = LOCAL_LANES["job"]
        xp = LOCAL_LANES["xp"]
        non_interference.update(
            {
                "job_xp_cpu_sets_disjoint": not bool(
                    remote_services._expand_cpuset(job.cpuset).intersection(
                        remote_services._expand_cpuset(xp.cpuset)
                    )
                ),
                "job_xp_numa_nodes_disjoint": not bool(
                    remote_services._expand_cpuset(job.mems).intersection(
                        remote_services._expand_cpuset(xp.mems)
                    )
                ),
                "job_xp_data_devices_disjoint": job.device != xp.device,
            }
        )
        document["non_interference"] = non_interference
        document["host_affinity_readback"] = {
            "verified": True,
            "controller_pid": os.getpid(),
            "controller_cpuset": _compress_cpuset(controller_affinity),
            "workload_pid": int(workload_pid) if workload_pid is not None else None,
            "workload_cpuset": (
                _compress_cpuset(workload_affinity)
                if workload_affinity is not None
                else None
            ),
            "expected_cpuset": self.lane.cpuset,
        }
        if self.storage_profile is not None:
            document["storage"] = {
                "schema_version": self.storage_profile.schema_version,
                "profile_id": self.storage_profile.profile_id,
                "fresh_copy_mode": self.storage_profile.fresh_copy_mode,
                "device": self.storage_profile.device,
                "legacy_artifact_mixing_prohibited": True,
            }
        if self.lane.key == "tpch":
            benchbase_jar = Path(campaign_module.BENCHBASE_JAR)
            document["workload_binary"] = {
                "role": "tpch_benchbase_jar",
                "path": str(benchbase_jar.resolve()),
                "sha256": hashlib.sha256(benchbase_jar.read_bytes()).hexdigest(),
                "q15_schema_global_view_serialized": True,
            }
        atomic_json(self.state_path, document)

    def remote(self, script: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
        # compute00's Docker daemon uses the explicit cgroup-parent
        # ``docker-limited`` rather than the remote hosts' ``docker`` parent.
        script = script.replace("/docker/$cid", "/docker-limited/$cid")
        # Some imported templates contain mysqld-auto.cnf values from their
        # source hosts.  They must not override this launch's frozen engine
        # settings (notably the buffer-pool size); the template itself remains
        # read-only and the effective value is still checked below.
        script = script.replace(
            "mysqld --bind-address=0.0.0.0",
            "mysqld --persisted-globals-load=OFF --bind-address=0.0.0.0",
        )
        script = script.replace(
            f"docker stop --time 120 {self.container}_bootstrap >/dev/null",
            (
                f"timeout --signal=TERM 120s docker exec {self.container}_bootstrap "
                f"mysqladmin -uroot "
                f"shutdown >/dev/null 2>&1 || "
                f"docker stop --time 120 {self.container}_bootstrap >/dev/null"
            ),
        )
        if "RA_PREP_STAGE=preflight_passed" in script:
            copy_start = script.index(
                "docker run --rm --network none --cpus 0.5"
            )
            copy_marker = "printf 'RA_PREP_STAGE=template_copied\n'"
            copy_end = script.index(copy_marker, copy_start)
            if self.storage_profile is not None:
                profile = self.storage_profile
                if self.storage_manifest_document is None:
                    raise RuntimeError(
                        f"{self.lane.key} storage-v2 manifest was not validated before prepare"
                    )
                tree_sha = self.storage_manifest_document[
                    "template_tree_sha256"
                ]
                replacement = f"""test ! -L {self.lane.template}
test ! -L {self.remote_data}
test ! -e {self.remote_root}/overlay_upper
test ! -e {self.remote_root}/overlay_work
test "$(findmnt -n -o SOURCE -T {self.lane.template})" = {profile.device}
test "$(findmnt -n -o FSTYPE -T {self.lane.template})" = {profile.filesystem}
test -z "$(find {self.lane.template} -xdev -perm /222 -print -quit)"
test "$(findmnt -n -o SOURCE -T {self.remote_data})" = {profile.device}
test "$(findmnt -n -o FSTYPE -T {self.remote_data})" = {profile.filesystem}
test "$(lsblk -dn -o MAJ:MIN {profile.device} | tr -d '[:space:]')" = {profile.device_major_minor}
test ! -e {self.remote_root}/.ra-storage-profile
printf '%s\\n' {profile.profile_id} >{self.remote_root}/.ra-storage-profile
docker run --rm --network none --cpus 0.5 --cpuset-cpus {self.lane.cpuset.split(',')[0]} \\
  --cpuset-mems {self.lane.mems} --memory 268435456 --memory-swap 268435456 --pids-limit 64 \\
  -v {self.lane.template}:/template:ro -v {self.remote_data}:/copy:rw \\
  --entrypoint /bin/sh {campaign_module.IMAGE} \\
  -c 'cp -a --reflink=never /template/. /copy/'
template_tree_sha=$(docker run --rm --network none -v {self.lane.template}:/tree:ro \\
  --entrypoint /bin/sh {campaign_module.IMAGE} \\
  -c "cd /tree && find . -xdev -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d ' ' -f1")
runtime_tree_sha=$(docker run --rm --network none -v {self.remote_data}:/tree:ro \\
  --entrypoint /bin/sh {campaign_module.IMAGE} \\
  -c "cd /tree && find . -xdev -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d ' ' -f1")
test "$template_tree_sha" = {tree_sha}
test "$runtime_tree_sha" = {tree_sha}
test -f {self.lane.template}/ibdata1
test -f {self.remote_data}/ibdata1
test "$(stat -c %d:%i {self.lane.template}/ibdata1)" != "$(stat -c %d:%i {self.remote_data}/ibdata1)"
docker run --rm --network none --cpus 0.5 --cpuset-cpus {self.lane.cpuset.split(',')[0]} \\
  --cpuset-mems {self.lane.mems} --memory 268435456 --memory-swap 268435456 --pids-limit 64 \\
  -v {self.remote_data}:/copy:rw --entrypoint /bin/sh {campaign_module.IMAGE} \\
  -c 'rm -f /copy/mysql.sock /copy/mysqld.sock /copy/mysqld.pid; chown -R mysql:mysql /copy; find /copy -type d -exec chmod 0750 {{}} +; find /copy -type f -exec chmod 0640 {{}} +'
printf 'RA_STORAGE_PROFILE_ID={profile.profile_id}\\n'
printf 'RA_STORAGE_DEVICE={profile.device}\\n'
printf 'RA_STORAGE_DEVICE_MAJOR_MINOR={profile.device_major_minor}\\n'
printf 'RA_STORAGE_FSTYPE={profile.filesystem}\\n'
printf 'RA_STORAGE_FRESH_COPY_MODE={profile.fresh_copy_mode}\\n'
printf 'RA_STORAGE_TEMPLATE_TREE_SHA256=%s\\n' "$template_tree_sha"
printf 'RA_STORAGE_RUNTIME_TREE_SHA256=%s\\n' "$runtime_tree_sha"
printf 'RA_STORAGE_OVERLAY=disabled\\n'
printf 'RA_STORAGE_REFLINK=disabled\\n'
"""
            else:
                replacement = f"""mkdir -p {self.remote_root}/overlay_upper {self.remote_root}/overlay_work
sudo -n mount -t overlay overlay \\
  -o lowerdir={self.lane.template},upperdir={self.remote_root}/overlay_upper,workdir={self.remote_root}/overlay_work \\
  {self.remote_data}
"""
            script = script[:copy_start] + replacement + script[copy_end:]
        if (
            f"docker rm -f {self.container}_bootstrap" in script
            and "RA_PREP_STAGE=preflight_passed" not in script
        ):
            script = script.replace(
                f"docker stop --time 120 {self.container} >/dev/null 2>&1",
                (
                    f"timeout --signal=TERM 120s docker exec {self.container} mysqladmin "
                    f"--defaults-file=/run/ra-credentials/selected.cnf "
                    f"--connect-timeout=3 --ssl-mode=DISABLED "
                    f"--get-server-public-key "
                    f"shutdown >/dev/null 2>&1\n"
                    f"docker stop --time 120 {self.container} >/dev/null 2>&1"
                ),
                1,
            )
            cleanup_anchor = (
                f"docker rm -f {self.container}_bootstrap >/dev/null 2>&1\n"
            )
            if self.storage_profile is None:
                script = script.replace(
                    cleanup_anchor,
                    cleanup_anchor
                    + (
                        f"if mountpoint -q {self.remote_data}; then "
                        f"sudo -n umount {self.remote_data}; fi\n"
                    ),
                    1,
                )
            else:
                profile = self.storage_profile
                cleanup_guard = f"""test "$(realpath -m {self.remote_root})" = {self.remote_root} || exit 92
test "$(realpath -m $(dirname {self.remote_root}))" = {profile.project}/formal_sgd_runs || exit 93
test ! -L {self.remote_root} || exit 94
test ! -L {self.remote_data} || exit 95
test ! -d {self.remote_root} || test "$(cat {self.remote_root}/.ra-storage-profile)" = {profile.profile_id} || exit 96
test ! -d {self.remote_root} || test "$(findmnt -n -o SOURCE -T {self.remote_root})" = {profile.device} || exit 97
test ! -d {self.remote_data} || ! mountpoint -q {self.remote_data} || exit 98
"""
                script = script.replace(
                    cleanup_anchor, cleanup_anchor + cleanup_guard, 1
                )
        memsw = (
            "/sys/fs/cgroup/memory/docker-limited/$cid/"
            "memory.memsw.limit_in_bytes"
        )
        # The host has no swap and no memsw controller.  The base campaign's
        # compatibility marker expects a number; the retained artifacts are
        # replaced below with the independently verified effective value 0.
        script = script.replace(
            f"cat {memsw}",
            (
                f"if test -r {memsw}; then cat {memsw}; "
                f"else printf '{self.lane.memory_bytes}'; fi"
            ),
        )
        try:
            completed = run(["bash", "-s"], input=script, timeout=timeout)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "local control command failed: "
                f"rc={exc.returncode}; "
                f"stdout_tail={(exc.stdout or '')[-4000:]!r}; "
                f"stderr_tail={(exc.stderr or '')[-4000:]!r}"
            ) from exc
        if "RA_PREP_STAGE=preflight_passed" in script:
            lines = completed.stdout.splitlines()
            if lines and lines[-1].startswith("{"):
                inspect = json.loads(lines[-1])
                if (
                    inspect.get("HostConfig", {}).get("MemorySwap") == -1
                    and not Path("/proc/swaps").read_text(
                        encoding="utf-8"
                    ).splitlines()[1:]
                ):
                    # Compatibility input for the remote-host validator only.
                    # prepare_remote() independently proves host swap=0 and
                    # replaces every retained artifact with the effective 0.
                    inspect["HostConfig"]["MemorySwap"] = self.lane.memory_bytes
                    lines[-1] = json.dumps(inspect, separators=(",", ":"))
                    completed = subprocess.CompletedProcess(
                        completed.args,
                        completed.returncode,
                        "\n".join(lines) + "\n",
                        completed.stderr,
                    )
        return completed

    def prepare_remote(self) -> None:
        self.state(
            "PREPARATION_INTERFERENCE_GATE_RUNNING",
            preparation_interference_gate="IN_PROGRESS",
        )
        gate = self._preparation_interference_gate()
        self.state(
            "PREPARATION_INTERFERENCE_GATE_PASSED",
            preparation_interference_gate="PASS",
            preparation_interference_gate_path=str(
                self.output / "preparation_interference_gate_v1.json"
            ),
            preparation_interference_gate_samples=gate["sample_count"],
        )
        if self.storage_profile is not None:
            self._validated_storage_manifest()
        super().prepare_remote()
        swaps = [
            line for line in Path("/proc/swaps").read_text(encoding="utf-8").splitlines()[1:]
            if line.strip()
        ]
        if swaps:
            raise RuntimeError(
                "compute00 local formal lane requires host swap to remain disabled"
            )
        container_pid = int(
            subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Pid}}", self.container],
                text=True, capture_output=True, check=True, timeout=10,
            ).stdout.strip()
        )
        cgroups = Path(f"/proc/{container_pid}/cgroup").read_text(
            encoding="utf-8"
        )
        memory_rows = [
            row.split(":", 2)[2]
            for row in cgroups.splitlines()
            if row.split(":", 2)[1] == "memory"
        ]
        if len(memory_rows) != 1:
            raise RuntimeError("local container memory cgroup path is ambiguous")
        memsw_path = (
            Path("/sys/fs/cgroup/memory")
            / memory_rows[0].lstrip("/")
            / "memory.memsw.limit_in_bytes"
        )
        if memsw_path.exists():
            raise RuntimeError(
                "local swap contract expected an absent memsw controller"
            )
        resource_path = self.output / "resource_readback.json"
        resource = json.loads(resource_path.read_text(encoding="utf-8"))
        resource["effective_cgroup"]["memory_swap_bytes"] = 0
        resource["swap_readback"] = {
            "mode": "host_swap_disabled_no_memsw_controller",
            "host_swap_entries": 0,
            "effective_swap_bytes": 0,
            "memsw_controller_available": False,
        }
        atomic_json(resource_path, resource)
        effective_path = self.output / "effective_resource_readback_v2.json"
        effective = json.loads(effective_path.read_text(encoding="utf-8"))
        effective["requested"]["memory_swap_bytes"] = 0
        effective["effective"]["memory_swap_bytes"] = 0
        effective["effective"]["swap_mode"] = (
            "host_swap_disabled_no_memsw_controller"
        )
        effective["effective"]["memsw_controller_available"] = False
        if self.storage_profile is None:
            effective["effective"]["fresh_copy_mode"] = (
                "overlayfs_read_only_lower_per_point_upper"
            )
            effective["effective"]["template_lowerdir"] = self.lane.template
        else:
            profile = self.storage_profile
            prepare_lines = (
                self.output / "remote_prepare.stdout.log"
            ).read_text(encoding="utf-8").splitlines()
            markers = self._validated_storage_readback(prepare_lines)
            inspect_rows = [
                json.loads(line)
                for line in prepare_lines
                if line.startswith("{")
            ]
            if len(inspect_rows) != 1:
                raise RuntimeError(
                    f"{self.lane.key} storage-v2 docker inspect cardinality differs"
                )
            mysql_mount = self._validated_runtime_datadir_bind(inspect_rows[0])
            self._validate_profile_io_device_readback(resource, effective)
            storage = {
                "schema_version": profile.schema_version,
                "verified": True,
                "profile_id": profile.profile_id,
                "manifest_path": profile.manifest,
                "manifest_sha256": self.storage_manifest_sha256,
                "template_path": profile.template,
                "runtime_datadir": self.remote_data,
                "docker_datadir_bind": {
                    "type": mysql_mount["Type"],
                    "source": mysql_mount["Source"],
                    "read_write": mysql_mount["RW"],
                },
                "physical_runtime_directory_verified": True,
                "device": profile.device,
                "device_major_minor": profile.device_major_minor,
                "filesystem": profile.filesystem,
                "fresh_copy_mode": profile.fresh_copy_mode,
                "tree_hash_algorithm": profile.tree_hash_algorithm,
                "template_tree_sha256": markers[
                    "RA_STORAGE_TEMPLATE_TREE_SHA256"
                ],
                "runtime_tree_sha256_before_normalization": markers[
                    "RA_STORAGE_RUNTIME_TREE_SHA256"
                ],
                "overlay": False,
                "reflink": False,
                "legacy_artifact_mixing_prohibited": True,
                "cross_engine_comparison_prohibited": True,
            }
            effective["effective"].update(
                {
                    "storage_profile_id": profile.profile_id,
                    "fresh_copy_mode": profile.fresh_copy_mode,
                    "storage_device": profile.device,
                    "storage_device_major_minor": profile.device_major_minor,
                    "storage_filesystem": profile.filesystem,
                    "storage_overlay": False,
                    "storage_reflink": False,
                }
            )
            resource["storage"] = storage
            atomic_json(resource_path, resource)
            atomic_json(self.output / "storage_readback_v2.json", storage)
        atomic_json(effective_path, effective)

    def start_tunnel(self) -> None:
        defaults = self.output / "private/mysql.cnf"
        for _ in range(120):
            try:
                probe = subprocess.run(
                    [
                        "/usr/bin/mysql", f"--defaults-file={defaults}",
                        "--protocol=tcp", "--host=127.0.0.1",
                        f"--port={self.lane.remote_port}", "--user=root",
                        "--connect-timeout=3", "--ssl-mode=DISABLED",
                        "--get-server-public-key", "-NBe", "SELECT 1",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                )
            except subprocess.TimeoutExpired:
                time.sleep(1)
                continue
            if probe.returncode == 0:
                return
            time.sleep(1)
        raise RuntimeError("local MySQL endpoint did not become ready")

    def _local_transport_run(
        self,
        original_run: Any,
        command: Any,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        argv = [str(value) for value in command]
        if argv and argv[0] == "scp":
            source_token = argv[-2]
            source_prefix = f"{self.lane.host}:"
            if not source_token.startswith(source_prefix):
                raise RuntimeError("local capture copy source is outside its lane")
            source = Path(source_token[len(source_prefix):])
            destination = Path(argv[-1])
            shutil.copy2(source, destination)
            return subprocess.CompletedProcess(argv, 0, "", "")
        return original_run(command, **kwargs)

    def export_capture_general_log(self, *, require_complete: bool) -> None:
        # The inherited exporter uses scp because its lanes are remote.  This
        # lane is already on compute00, so retain all validation/extraction logic
        # while replacing only that transport call with a local file copy.
        original_run = campaign_module.run

        def localized(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return self._local_transport_run(
                original_run, command, **kwargs
            )

        campaign_module.run = localized
        try:
            super().export_capture_general_log(require_complete=require_complete)
        finally:
            campaign_module.run = original_run

    def _open_resource_sampler(self) -> subprocess.Popen[str]:
        if self._sampler_script is None or self._sampler_stderr is None:
            raise RuntimeError("resource sampler is not initialized")
        sampler_script = self._sampler_script.replace(
            "/docker/$cid", "/docker-limited/$cid"
        )
        sampler_lines = []
        for line in sampler_script.splitlines():
            if line.startswith("while docker inspect ") and line.endswith(
                "2>/dev/null | grep -qx true; do"
            ):
                sampler_lines.extend(
                    (
                        "next_tick_ms=$(date +%s%3N)",
                        'while test -r "$cpu"; do',
                    )
                )
            elif line.strip() == "sleep 0.90":
                sampler_lines.extend(
                    (
                        "  next_tick_ms=$((next_tick_ms + 1000))",
                        "  now_ms=$(date +%s%3N)",
                        "  wait_ms=$((next_tick_ms - now_ms))",
                        "  if test \"$wait_ms\" -gt 0; then",
                        "    printf -v wait_seconds '%d.%03d' "
                        '"$((wait_ms / 1000))" "$((wait_ms % 1000))"',
                        '    sleep "$wait_seconds"',
                        "  else",
                        "    next_tick_ms=$now_ms",
                        "  fi",
                    )
                )
            else:
                sampler_lines.append(line)
        sampler_script = "\n".join(sampler_lines) + "\n"
        sampler = subprocess.Popen(
            ["bash", "-s"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._sampler_stderr,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        assert sampler.stdin is not None
        sampler.stdin.write(sampler_script)
        sampler.stdin.close()
        return sampler

    def start_sampler(self) -> None:
        super().start_sampler()
        self._runtime_interference_stop.clear()
        self._runtime_interference_ready.clear()
        with self._runtime_interference_lock:
            self._runtime_interference_started_epoch_ms = int(time.time() * 1000)
            self._runtime_interference_sample_count = 0
            self._runtime_interference_error = None
            self._runtime_interference_ready_epoch_ms = None
        self._write_runtime_interference_document(completed=False)
        self._runtime_interference_thread = threading.Thread(
            target=self._runtime_interference_monitor,
            name=f"runtime-interference-{self.lane.key}",
            daemon=True,
        )
        self._runtime_interference_thread.start()
        self._await_runtime_interference_ready()

    def resource_samples(self) -> Sequence[Mapping[str, Any]]:
        self._assert_runtime_interference_clear()
        return super().resource_samples()

    def database_samples(self) -> Sequence[Mapping[str, Any]]:
        self._assert_runtime_interference_clear()
        return super().database_samples()

    def cleanup(self) -> None:
        self._runtime_interference_stop.set()
        thread = self._runtime_interference_thread
        if thread is not None:
            thread.join(timeout=5)
            self._write_runtime_interference_document(completed=True)
        super().cleanup()


class LocalProductionServices(ProductionServices):
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = copy.deepcopy(dict(config))
        benchmark = str(config.get("benchmark") or "").lower()
        lane_key = BENCHMARK_LANES.get(benchmark)
        if lane_key is None or lane_key not in LOCAL_LANES:
            raise remote_services.FormalSGDNotReady(
                f"NOT_READY: unsupported local production benchmark {benchmark!r}"
            )
        self.benchmark = benchmark
        if lane_key == "tpch":
            benchbase_path = STAGED_TPCH_BENCHBASE_JAR
            if (
                not benchbase_path.is_file()
                or benchbase_path.is_symlink()
            ):
                raise remote_services.FormalSGDNotReady(
                    "NOT_READY: frozen staged TPC-H BenchBase JAR authority is invalid"
                )
            actual_sha = hashlib.sha256(benchbase_path.read_bytes()).hexdigest()
            if actual_sha != STAGED_TPCH_BENCHBASE_JAR_SHA256:
                raise remote_services.FormalSGDNotReady(
                    "NOT_READY: staged TPC-H BenchBase JAR SHA-256 differs"
                )
            campaign_module.BENCHBASE_JAR = benchbase_path
        profile_ref = config.get("profile")
        if not isinstance(profile_ref, str) or not profile_ref.strip():
            raise remote_services.FormalSGDNotReady(
                "NOT_READY: frozen production profile is missing"
            )
        profile_path = Path(profile_ref)
        if not profile_path.is_absolute():
            profile_path = ROOT / profile_path
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        required = {
            "cpu_cores", "memory_bytes", "pids", "read_bps", "write_bps",
            "buffer_pool_bytes",
        }
        missing = sorted(required - set(profile))
        if missing:
            raise remote_services.FormalSGDNotReady(
                "NOT_READY: frozen production profile is incomplete: "
                + ", ".join(missing)
            )
        base = LOCAL_LANES[lane_key]
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        memory_bytes = (
            int(profile["memory_bytes"]) // page_size
        ) * page_size
        self.lane = replace(
            base,
            cpu_cores=float(profile["cpu_cores"]),
            memory_bytes=memory_bytes,
            pids=int(profile["pids"]),
            read_bps=int(profile["read_bps"]),
            write_bps=int(profile["write_bps"]),
            buffer_pool_bytes=int(profile["buffer_pool_bytes"]),
        )
        if lane_key == "xp" and config.get("runtime_bundle_id"):
            authorization_stage = str(
                config.get("authorization_stage") or ""
            )
            expected_port = (
                65204 if authorization_stage == "capture_only" else 65304
            )
            configured_mysql = config.get("mysql")
            configured_port = (
                configured_mysql.get("port")
                if isinstance(configured_mysql, Mapping)
                else None
            )
            if (
                authorization_stage
                not in {"capture_only", "formal_r0_only", "formal_outer"}
                or configured_port != expected_port
            ):
                raise remote_services.FormalSGDNotReady(
                    "NOT_READY: XP authorization stage/port differs from "
                    "capture=65204 or formal=65304"
                )
            self.lane = replace(self.lane, remote_port=expected_port)
        workload_config = config.get("workload")
        configured_host_cpuset = (
            str(workload_config.get("host_control_cpuset") or "").strip()
            if isinstance(workload_config, Mapping)
            else ""
        )
        if configured_host_cpuset != self.lane.cpuset:
            raise remote_services.FormalSGDNotReady(
                f"NOT_READY: {lane_key} host_control_cpuset differs: "
                f"configured={configured_host_cpuset!r} "
                f"expected={self.lane.cpuset!r}"
            )
        expected_affinity = remote_services._expand_cpuset(self.lane.cpuset)
        os.sched_setaffinity(0, expected_affinity)
        effective_affinity = set(os.sched_getaffinity(0))
        if effective_affinity != expected_affinity:
            raise remote_services.FormalSGDNotReady(
                f"NOT_READY: {lane_key} controller affinity readback differs: "
                f"{_compress_cpuset(effective_affinity)}"
            )
        self.storage_profile = LOCAL_STORAGE_PROFILES.get(lane_key)
        configured_storage_id = str(
            config.get("local_storage_profile_id") or ""
        ).strip()
        if (
            self.storage_profile is not None
            and configured_storage_id != self.storage_profile.profile_id
        ):
            raise remote_services.FormalSGDNotReady(
                f"NOT_READY: {lane_key} local storage profile differs from storage-v2"
            )
        configured_manifest = str(
            config.get("local_storage_manifest") or ""
        ).strip()
        if (
            self.storage_profile is not None
            and configured_manifest != self.storage_profile.manifest
        ):
            raise remote_services.FormalSGDNotReady(
                f"NOT_READY: {lane_key} storage-v2 manifest path differs"
            )
        self.storage_manifest_sha256 = str(
            config.get("local_storage_manifest_sha256") or ""
        ).strip().lower()
        self.replay_lane = replace(
            self.lane, remote_port=self.lane.remote_port + REPLAY_PORT_OFFSET
        )
        output = Path(str((config.get("output") or {}).get("directory", "history/mysql")))
        self.output = output if output.is_absolute() else ROOT / output
        self.points = self.output / "production_points"
        self.planner = None
        self.active = None
        self.point_lock = threading.Lock()
        self.sql_client = remote_services.LivePlannerSQLClient(self)

    def _new_campaign(
        self, lane: Lane, role: str, costs: Mapping[str, float] | None = None,
    ) -> LocalCampaign:
        run_id = self._run_id(role)
        workload = self.config.get("workload") or {}
        tpch_terminals = int(workload.get("tpch_terminals", 0))
        if lane.key == "tpch" and tpch_terminals != 4:
            raise remote_services.FormalSGDNotReady(
                "NOT_READY: TPC-H launch requires exactly four terminals"
            )
        return LocalCampaign(
            lane,
            self.points / run_id,
            run_id,
            candidate_costs=costs,
            measurement_seconds=int(self.config.get("measurement_seconds", 600)),
            minimum_measurement_seconds=int(
                self.config.get("minimum_measurement_seconds", 180)
            ),
            minimum_client_completions=int(
                self.config.get("minimum_client_completions", 0)
            ),
            require_r0_profile_gate=(role == "r0"),
            storage_profile=(
                self.storage_profile if lane.key == self.lane.key else None
            ),
            storage_manifest_sha256=(
                self.storage_manifest_sha256
                if lane.key == self.lane.key and self.storage_profile is not None
                else ""
            ),
            tpch_terminals=tpch_terminals if lane.key == "tpch" else 2,
        )

    def pressure_probe(self, scenario: str, phase: str) -> Mapping[str, Any]:
        listing = subprocess.run(
            ["docker", "ps", "--no-trunc", "--format", "{{.Names}}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=True,
        ).stdout.splitlines()
        job_count = sum(name.startswith("ra_mysql_sgd_job_") for name in listing)
        xp_count = sum(name.startswith("ra_mysql_sgd_xp_") for name in listing)
        job = LOCAL_LANES["job"]
        xp = LOCAL_LANES["xp"]
        return {
            "observed_at": _utc_now(),
            "probe_id": uuid.uuid4().hex,
            "scenario": scenario,
            "phase": phase,
            "job_pressure_count": job_count,
            "xp_pressure_count": xp_count,
            "isolation_verified": (
                not remote_services._expand_cpuset(job.cpuset).intersection(
                    remote_services._expand_cpuset(xp.cpuset)
                )
                and job.remote_port != xp.remote_port
                and job.memory_bytes > 0
                and xp.memory_bytes > 0
                and job.pids > 0
                and xp.pids > 0
            ),
            "isolation_readback": {
                "job": {
                    "cpuset": job.cpuset, "mems": job.mems,
                    "device": job.device, "memory_bytes": job.memory_bytes,
                    "pids": job.pids,
                },
                "xp": {
                    "cpuset": xp.cpuset, "mems": xp.mems,
                    "device": xp.device, "memory_bytes": xp.memory_bytes,
                    "pids": xp.pids,
                },
            },
            "job_xp_cpu_sets_disjoint": not bool(
                remote_services._expand_cpuset(job.cpuset).intersection(
                    remote_services._expand_cpuset(xp.cpuset)
                )
            ),
            "job_xp_numa_nodes_disjoint": not bool(
                remote_services._expand_cpuset(job.mems).intersection(
                    remote_services._expand_cpuset(xp.mems)
                )
            ),
            "job_xp_data_devices_disjoint": job.device != xp.device,
            "cross_engine_comparison_prohibited": True,
            "formal_scope": "mysql_only",
            "transport": "local_no_ssh",
        }


def build(config: Mapping[str, Any]) -> Mapping[str, Any]:
    return LocalProductionServices(config).bundle()


__all__ = [
    "JOB_STORAGE_V2",
    "TPCH_STORAGE_V2",
    "LOCAL_STORAGE_PROFILES",
    "LEGACY_JOB_STORAGE_V1",
    "LOCAL_LANES",
    "LocalCampaign",
    "LocalProductionServices",
    "LocalStorageProfile",
    "build",
]
