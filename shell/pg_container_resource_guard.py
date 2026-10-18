#!/usr/bin/env python3
"""Fail-closed Docker CPU and memory execution-domain guard for PostgreSQL runs."""

from __future__ import annotations

import fcntl
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from shell.pg_fixed_guc_guard import canonical_sha256
from shell.run_pg_algorithm_c_scene import worker_key


DOCKER_INSPECT_ATTEMPTS = 3
DOCKER_INSPECT_RETRY_DELAY_S = 5.0
DOCKER_INSPECT_TIMEOUT_S = 180.0
DOCKER_INSPECT_LOCK = Path("/tmp/resource_adaptive_pg_docker_inspect.lock")


def parse_binary_size(value: object) -> int:
    """Parse the compact Docker memory values used by scene contracts."""
    text = str(value or "").strip().lower()
    if not text:
        return 0
    multipliers = {
        "k": 1024,
        "kb": 1024,
        "m": 1024**2,
        "mb": 1024**2,
        "g": 1024**3,
        "gb": 1024**3,
        "t": 1024**4,
        "tb": 1024**4,
    }
    for suffix in ("kb", "mb", "gb", "tb", "k", "m", "g", "t"):
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * multipliers[suffix])
    return int(float(text))


def normalized_cpuset(value: object) -> list[int]:
    """Expand a Docker cpuset expression into a stable sorted CPU list."""
    cpus: set[int] = set()
    for item in str(value or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "-" in item:
            start_text, end_text = item.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if end < start:
                raise ValueError(f"invalid cpuset range: {item}")
            cpus.update(range(start, end + 1))
        else:
            cpus.add(int(item))
    return sorted(cpus)


def audit_container_resource_domain(
    contracts: Mapping[str, Mapping[str, Any]],
    *,
    expected_semantic_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """Require exact Docker cpuset, CPU quota, and memory for every worker."""
    containers = sorted(
        {
            str(worker.get("container") or "")
            for contract in contracts.values()
            for worker in contract["workers"]
        }
    )
    if not containers or any(not name for name in containers):
        raise RuntimeError("container resource audit found an empty container name")
    lock_started = time.perf_counter()
    inspect_attempts = 0
    last_timeout: Optional[subprocess.TimeoutExpired] = None
    DOCKER_INSPECT_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with DOCKER_INSPECT_LOCK.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        lock_wait_s = time.perf_counter() - lock_started
        proc: Optional[subprocess.CompletedProcess[str]] = None
        for inspect_attempts in range(1, DOCKER_INSPECT_ATTEMPTS + 1):
            try:
                proc = subprocess.run(
                    ["docker", "inspect", *containers],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=DOCKER_INSPECT_TIMEOUT_S,
                )
                break
            except subprocess.TimeoutExpired as exc:
                last_timeout = exc
                if inspect_attempts == DOCKER_INSPECT_ATTEMPTS:
                    raise RuntimeError(
                        "docker resource audit timed out after "
                        f"{inspect_attempts} attempts"
                    ) from exc
                time.sleep(DOCKER_INSPECT_RETRY_DELAY_S * inspect_attempts)
    if proc is None:
        raise RuntimeError("docker resource audit produced no result") from last_timeout
    if proc.returncode != 0:
        raise RuntimeError(
            "docker resource audit failed: "
            f"returncode={proc.returncode} stderr={proc.stderr.strip()}"
        )
    documents = json.loads(proc.stdout)
    by_name = {
        str(document.get("Name") or "").lstrip("/"): document
        for document in documents
    }
    rows = []
    errors = []
    for scene, contract in sorted(contracts.items()):
        for worker in sorted(contract["workers"], key=worker_key):
            name = str(worker.get("container") or "")
            document = by_name.get(name)
            if document is None:
                errors.append(f"{scene}/{worker_key(worker)}: missing container {name}")
                continue
            host = document.get("HostConfig") or {}
            state = document.get("State") or {}
            expected_cpuset = normalized_cpuset(worker.get("cpuset_cpus"))
            actual_cpuset = normalized_cpuset(host.get("CpusetCpus"))
            expected_nano_cpus = int(float(worker.get("cpus") or 0.0) * 1_000_000_000)
            actual_nano_cpus = int(host.get("NanoCpus") or 0)
            expected_memory = parse_binary_size(worker.get("memory"))
            actual_memory = int(host.get("Memory") or 0)
            if not bool(state.get("Running")):
                errors.append(f"{scene}/{worker_key(worker)}: container is not running")
            if actual_cpuset != expected_cpuset:
                errors.append(
                    f"{scene}/{worker_key(worker)}: cpuset actual={host.get('CpusetCpus')} "
                    f"expected={worker.get('cpuset_cpus')}"
                )
            if actual_nano_cpus != expected_nano_cpus:
                errors.append(
                    f"{scene}/{worker_key(worker)}: NanoCpus actual={actual_nano_cpus} "
                    f"expected={expected_nano_cpus}"
                )
            if actual_memory != expected_memory:
                errors.append(
                    f"{scene}/{worker_key(worker)}: memory actual={actual_memory} "
                    f"expected={expected_memory}"
                )
            rows.append(
                {
                    "scene": scene,
                    "worker": worker_key(worker),
                    "container": name,
                    "running": bool(state.get("Running")),
                    "cpuset_cpus": str(host.get("CpusetCpus") or ""),
                    "nano_cpus": actual_nano_cpus,
                    "memory_bytes": actual_memory,
                    "memory_swap_bytes": int(host.get("MemorySwap") or 0),
                    "shm_size_bytes": int(host.get("ShmSize") or 0),
                    "network_mode": str(host.get("NetworkMode") or ""),
                }
            )
    semantic = {"workers": rows}
    semantic_sha256 = canonical_sha256(semantic)
    if expected_semantic_sha256 and semantic_sha256 != expected_semantic_sha256:
        errors.append(
            "container resource fingerprint drift: "
            f"actual={semantic_sha256} expected={expected_semantic_sha256}"
        )
    receipt = {
        "contract": "pg_container_resource_domain_guard_v1",
        "semantic": semantic,
        "semantic_sha256": semantic_sha256,
        "errors": errors,
        "passed": not errors,
        "docker_inspect_attempts": inspect_attempts,
        "docker_inspect_lock_wait_s": lock_wait_s,
        "docker_inspect_timeout_s": DOCKER_INSPECT_TIMEOUT_S,
        "captured_at_epoch_s": time.time(),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    if errors:
        raise RuntimeError("container resource domain audit failed: " + "; ".join(errors))
    return receipt
