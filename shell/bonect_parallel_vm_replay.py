#!/usr/bin/env python3
"""Dispatch Bonect real replay batches to isolated libvirt VM workers.

Every replay wave first restores the same immutable database template on all
participating workers, waits at a barrier, drops guest caches, and then starts
one candidate per worker. Candidate effects are normalized against the
worker-local baseline; cross-worker baseline and anchor drift are hard gates.
IO replay remains serial unless every worker declares a distinct, verified
physical-disk isolation ID.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import socket
import statistics
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "shell/bonect_real_validate_candidates.py"
COST_PARAMS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]
DEFAULT_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


LIBVIRT_DEFAULT_URI = "qemu:///system"
VIRSH_TIMEOUT_SEC = 60
VIRSH_KILL_AFTER_SEC = 5


def virsh_safe_command(*virsh_args: str, libvirt_uri: str = LIBVIRT_DEFAULT_URI) -> List[str]:
    """Project-safe virsh wrapper: sudo + LIBVIRT_DEFAULT_URI + hard timeout.

    Never use a naked ``virsh -c`` that can block indefinitely under polkit.
    """
    return [
        "sudo",
        "env",
        f"LIBVIRT_DEFAULT_URI={libvirt_uri}",
        "timeout",
        f"--kill-after={VIRSH_KILL_AFTER_SEC}s",
        f"{VIRSH_TIMEOUT_SEC}s",
        "virsh",
        *[str(a) for a in virsh_args],
    ]


def parse_blkdeviotune_output(text: str) -> Dict[str, int]:
    """Parse ``virsh blkdeviotune --live`` key/value lines into integer bytes/sec fields."""
    observed: Dict[str, int] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower().replace(" ", "_")
        value = value.strip()
        if key in {"read_bytes_sec", "write_bytes_sec", "total_bytes_sec"}:
            try:
                observed[key] = int(float(value))
            except Exception:
                continue
    return observed


def query_live_disk_throttle(
    vm_name: str,
    disk_dev: str,
    *,
    libvirt_uri: str = LIBVIRT_DEFAULT_URI,
) -> Dict[str, Any]:
    """Query live blkdeviotune via the safe sudo/timeout wrapper."""
    started = time.time()
    cmd = virsh_safe_command(
        "blkdeviotune",
        str(vm_name),
        str(disk_dev or "vda"),
        "--live",
        libvirt_uri=libvirt_uri,
    )
    # Outer timeout slightly above the wrapper so we always reap the process tree.
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        timeout=float(VIRSH_TIMEOUT_SEC + VIRSH_KILL_AFTER_SEC + 5),
    )
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if proc.returncode != 0:
        detail = (stdout + stderr).strip()
        raise RuntimeError(
            f"query_live_disk_throttle failed vm={vm_name} disk={disk_dev}: {detail}"
        )
    observed = parse_blkdeviotune_output(stdout)
    if "read_bytes_sec" not in observed or "write_bytes_sec" not in observed:
        raise RuntimeError(
            f"query_live_disk_throttle parse failed vm={vm_name} disk={disk_dev}: "
            f"missing read/write_bytes_sec in output: {stdout!r}"
        )
    return {
        "vm_name": vm_name,
        "disk_dev": disk_dev or "vda",
        "observed_read_bytes_sec": int(observed["read_bytes_sec"]),
        "observed_write_bytes_sec": int(observed["write_bytes_sec"]),
        "observed_total_bytes_sec": int(observed.get("total_bytes_sec", 0)),
        "elapsed_s": time.time() - started,
        "status": "ok",
        "query_command": cmd,
        "raw_output": stdout,
    }


def live_disk_throttle(
    vm_name: str,
    disk_dev: str,
    *,
    read_mb: int,
    write_mb: int,
    libvirt_uri: str = LIBVIRT_DEFAULT_URI,
    verify: bool = True,
) -> Dict[str, Any]:
    """Apply live blkdeviotune, then fail-closed verify observed bytes/sec.

    Used only for IO scenario restore boost / measurement re-apply when the
    worker config explicitly enables ``restore_io_boost``.
    """
    started = time.time()
    read_bps = max(0, int(read_mb) * 1024 * 1024)
    write_bps = max(0, int(write_mb) * 1024 * 1024)
    set_cmd = virsh_safe_command(
        "blkdeviotune",
        str(vm_name),
        str(disk_dev or "vda"),
        "--live",
        "--read-bytes-sec",
        str(read_bps),
        "--write-bytes-sec",
        str(write_bps),
        libvirt_uri=libvirt_uri,
    )
    proc = subprocess.run(
        set_cmd,
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        timeout=float(VIRSH_TIMEOUT_SEC + VIRSH_KILL_AFTER_SEC + 5),
    )
    if proc.returncode != 0:
        detail = ((proc.stdout or "") + (proc.stderr or "")).strip()
        raise RuntimeError(
            f"live_disk_throttle set failed vm={vm_name} disk={disk_dev} "
            f"read_mb={read_mb} write_mb={write_mb}: {detail}"
        )
    result: Dict[str, Any] = {
        "vm_name": vm_name,
        "disk_dev": disk_dev or "vda",
        "read_mb": int(read_mb),
        "write_mb": int(write_mb),
        "requested_read_bytes_sec": read_bps,
        "requested_write_bytes_sec": write_bps,
        # Backward-compatible aliases used by older audit consumers.
        "read_bytes_sec": read_bps,
        "write_bytes_sec": write_bps,
        "elapsed_s": time.time() - started,
        "status": "ok",
        "set_command": set_cmd,
        "command": set_cmd,
    }
    if not verify:
        return result
    query = query_live_disk_throttle(vm_name, disk_dev, libvirt_uri=libvirt_uri)
    result["query_command"] = query["query_command"]
    result["observed_read_bytes_sec"] = int(query["observed_read_bytes_sec"])
    result["observed_write_bytes_sec"] = int(query["observed_write_bytes_sec"])
    result["query_raw_output"] = query.get("raw_output")
    if (
        int(query["observed_read_bytes_sec"]) != read_bps
        or int(query["observed_write_bytes_sec"]) != write_bps
    ):
        raise RuntimeError(
            f"live_disk_throttle verify mismatch vm={vm_name} disk={disk_dev}: "
            f"requested read/write={read_bps}/{write_bps} observed "
            f"{query['observed_read_bytes_sec']}/{query['observed_write_bytes_sec']}"
        )
    result["verify_matched"] = True
    result["elapsed_s"] = time.time() - started
    return result


def rootless_qmp_disk_throttle(
    qmp_socket: str,
    disk_device: str,
    *,
    read_mb: float,
    write_mb: float,
    verify: bool = True,
) -> Dict[str, Any]:
    """Apply and verify a rootless QEMU drive throttle through its owned QMP socket."""
    started = time.time()
    read_bps = max(0, int(float(read_mb) * 1024 * 1024))
    write_bps = max(0, int(float(write_mb) * 1024 * 1024))
    path = str(qmp_socket)
    device = str(disk_device or "drive0")
    client = socket.socket(socket.AF_UNIX)
    client.settimeout(30.0)
    client.connect(path)
    stream = client.makefile("rwb", buffering=0)

    def receive(*keys: str) -> Dict[str, Any]:
        while True:
            line = stream.readline()
            if not line:
                raise RuntimeError(f"QMP closed unexpectedly: {path}")
            payload = json.loads(line.decode("utf-8", errors="replace"))
            if any(key in payload for key in keys):
                return payload

    def execute(command: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        payload: Dict[str, Any] = {"execute": command}
        if arguments is not None:
            payload["arguments"] = arguments
        stream.write((json.dumps(payload) + "\r\n").encode("utf-8"))
        response = receive("return", "error")
        if "error" in response:
            raise RuntimeError(
                f"QMP command failed socket={path} command={command}: {response['error']}"
            )
        return response.get("return")

    try:
        receive("QMP")
        execute("qmp_capabilities")
        execute(
            "block_set_io_throttle",
            {
                "device": device,
                "bps": 0,
                "bps_rd": read_bps,
                "bps_wr": write_bps,
                # QEMU's average-only token bucket permits a large first
                # burst after the restore profile switches from 800 MiB/s to
                # the formal IO limit.  One such burst can double a four-minute
                # mean even though p99 is correctly throttled.  Pin the maximum
                # to the same physical rate so measured averages and the
                # declared capacity use the same bandwidth semantics.
                "bps_max": 0,
                "bps_rd_max": read_bps,
                "bps_wr_max": write_bps,
                "bps_max_length": 1,
                "bps_rd_max_length": 1,
                "bps_wr_max_length": 1,
                "iops": 0,
                "iops_rd": 0,
                "iops_wr": 0,
            },
        )
        observed_read = read_bps
        observed_write = write_bps
        if verify:
            blocks = execute("query-block") or []
            block = next(
                (row for row in blocks if str(row.get("device") or "") == device),
                None,
            )
            if not isinstance(block, dict):
                raise RuntimeError(f"QMP query-block missing device={device}: {path}")
            inserted = block.get("inserted") or {}
            observed_read = int(inserted.get("bps_rd") or 0)
            observed_write = int(inserted.get("bps_wr") or 0)
            observed_read_max = int(inserted.get("bps_rd_max") or 0)
            observed_write_max = int(inserted.get("bps_wr_max") or 0)
            if observed_read != read_bps or observed_write != write_bps:
                raise RuntimeError(
                    f"QMP throttle verify mismatch device={device}: requested "
                    f"{read_bps}/{write_bps}, observed {observed_read}/{observed_write}"
                )
            if observed_read_max != read_bps or observed_write_max != write_bps:
                raise RuntimeError(
                    f"QMP throttle max verify mismatch device={device}: requested "
                    f"{read_bps}/{write_bps}, observed "
                    f"{observed_read_max}/{observed_write_max}"
                )
    finally:
        stream.close()
        client.close()

    return {
        "status": "ok",
        "backend": "rootless_qmp",
        "qmp_socket": path,
        "disk_device": device,
        "read_mb": float(read_mb),
        "write_mb": float(write_mb),
        "requested_read_bytes_sec": read_bps,
        "requested_write_bytes_sec": write_bps,
        "observed_read_bytes_sec": observed_read,
        "observed_write_bytes_sec": observed_write,
        "observed_read_max_bytes_sec": observed_read_max if verify else read_bps,
        "observed_write_max_bytes_sec": observed_write_max if verify else write_bps,
        "verify_matched": bool(verify),
        "elapsed_s": time.time() - started,
    }


def resolve_restore_io_boost(
    worker: Dict[str, Any],
    scenario: str,
) -> Optional[Dict[str, Any]]:
    """Return explicit restore IO boost profile only for IO + config enabled.

    Fail closed for other scenarios or missing/disabled config so CPU/Memory/None
    never receive opportunistic live throttle profile switches.
    """
    if str(scenario).strip().lower() != "io":
        return None
    config = effective_worker_config(worker, scenario)
    recovery = config.get("pre_benchmark_recovery") or {}
    snapshot = recovery.get("snapshot_restore") or {}
    boost_cfg = snapshot.get("restore_io_boost") or {}
    if not isinstance(boost_cfg, dict) or not boost_cfg.get("enabled", False):
        return None
    read_mb = _to_int(boost_cfg.get("read_mb"), 0)
    write_mb = _to_int(boost_cfg.get("write_mb"), 0)
    if read_mb <= 0 or write_mb <= 0:
        raise RuntimeError(
            f"worker={worker.get('worker_id')} restore_io_boost.enabled=true requires "
            f"positive read_mb/write_mb; got read_mb={boost_cfg.get('read_mb')!r} "
            f"write_mb={boost_cfg.get('write_mb')!r}"
        )
    return {
        "enabled": True,
        "read_mb": int(read_mb),
        "write_mb": int(write_mb),
        "source": "pre_benchmark_recovery.snapshot_restore.restore_io_boost",
    }


def restore_io_boost_from_vm_resource(vm_resource: Dict[str, Any]) -> Dict[str, int]:
    """Legacy helper retained for tests; prefer resolve_restore_io_boost + config."""
    read_mb = max(1, _to_int(vm_resource.get("read_mb"), 100))
    write_mb = max(0, _to_int(vm_resource.get("write_mb"), 100))
    restore_write = max(write_mb, 200)
    restore_read = max(read_mb, 100)
    return {"read_mb": int(restore_read), "write_mb": int(restore_write)}


def pool_signature(workers: Sequence[Dict[str, Any]], scenario: str) -> str:
    payload = {
        "scenario": scenario,
        "workers": [
            {
                "worker_id": worker.get("worker_id"),
                "vm_name": worker.get("vm_name"),
                "scenario_config": (worker.get("scenario_configs") or {}).get(scenario),
                "config_override": worker.get("config_override") or {},
                "scenario_override": (worker.get("scenario_overrides") or {}).get(scenario) or {},
            }
            for worker in workers
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def inspect_calibration_cache(
    path: Optional[str],
    *,
    signature: str,
    scenario: str,
    max_age_s: float,
) -> Dict[str, Any]:
    """Validate a worker control cache and report what can be safely reused.

    Fail closed on scenario/profile-signature/age mismatch. Baseline controls may
    be reused when baseline_gate passed; full baseline+anchor reuse also requires
    worker_drift_gate passed (or explicit baseline_only controls_level).
    """
    empty = {
        "usable": False,
        "has_baselines": False,
        "has_anchors": False,
        "worker_control_rows": {},
        "reason": "missing_or_empty",
        "age_s": None,
        "controls_level": None,
        "path": path or "",
    }
    if not path:
        return empty
    cache_path = Path(path)
    if not cache_path.exists():
        return {**empty, "reason": "path_missing"}
    data = read_json(cache_path)
    age_s = time.time() - float(data.get("created_at_epoch") or 0.0)
    if data.get("scenario") != scenario:
        return {**empty, "reason": "scenario_mismatch", "age_s": age_s}
    if data.get("pool_signature") != signature:
        return {**empty, "reason": "pool_signature_mismatch", "age_s": age_s}
    if age_s < 0 or age_s > max_age_s:
        return {**empty, "reason": "stale_or_future_timestamp", "age_s": age_s}
    baseline_gate_ok = bool((data.get("baseline_gate") or {}).get("passed"))
    drift_gate_ok = bool((data.get("worker_drift_gate") or {}).get("passed"))
    controls_level = str(data.get("controls_level") or "").strip().lower()
    rows_raw = data.get("worker_control_rows") or {}
    if not isinstance(rows_raw, dict) or not rows_raw:
        return {**empty, "reason": "missing_worker_control_rows", "age_s": age_s}
    rows = {
        str(worker_id): [copy.deepcopy(row) for row in worker_rows if isinstance(row, dict)]
        for worker_id, worker_rows in rows_raw.items()
        if isinstance(worker_rows, list)
    }
    has_baselines = any(
        any(str(row.get("candidate_id") or "").startswith("__baseline__") for row in worker_rows)
        for worker_rows in rows.values()
    )
    has_anchors = any(
        any(str(row.get("candidate_id") or "").startswith("__anchor__") for row in worker_rows)
        for worker_rows in rows.values()
    )
    if not has_baselines:
        return {**empty, "reason": "no_baseline_rows", "age_s": age_s}
    if not baseline_gate_ok and not drift_gate_ok:
        return {**empty, "reason": "baseline_or_drift_gate_not_passed", "age_s": age_s}
    # Full control reuse requires either an explicit full level or a passed drift gate.
    full_reuse = has_anchors and (drift_gate_ok or controls_level in {"baseline_and_anchor", "full"})
    return {
        "usable": True,
        "has_baselines": True,
        "has_anchors": bool(full_reuse),
        "worker_control_rows": rows if full_reuse else {
            worker_id: [
                copy.deepcopy(row)
                for row in worker_rows
                if str(row.get("candidate_id") or "").startswith("__baseline__")
            ]
            for worker_id, worker_rows in rows.items()
        },
        "reason": "ok_full" if full_reuse else "ok_baseline_only",
        "age_s": age_s,
        "controls_level": "baseline_and_anchor" if full_reuse else "baseline_only",
        "path": str(cache_path),
        "raw": data,
    }


def load_calibration_cache(
    path: Optional[str],
    *,
    signature: str,
    scenario: str,
    max_age_s: float,
) -> Dict[str, List[Dict[str, Any]]]:
    """Backward-compatible helper: return reusable control rows or {}."""
    inspected = inspect_calibration_cache(
        path,
        signature=signature,
        scenario=scenario,
        max_age_s=max_age_s,
    )
    if not inspected.get("usable"):
        return {}
    return inspected.get("worker_control_rows") or {}


def cached_anchor_matches(
    cached_control_rows: Dict[str, List[Dict[str, Any]]],
    workers: Sequence[Dict[str, Any]],
    anchor: Dict[str, Any],
) -> bool:
    """Return true only when every cached worker anchor has the requested params."""
    requested = anchor.get("params") or {}
    try:
        requested_signature = tuple(
            (name, round(float(requested[name]), 12)) for name in COST_PARAMS
        )
    except (KeyError, TypeError, ValueError):
        return False
    for worker in workers:
        worker_id = str(worker["worker_id"])
        expected_id = f"__anchor__{worker_id}"
        cached_anchor = next(
            (
                row
                for row in cached_control_rows.get(worker_id, [])
                if row.get("candidate_id") == expected_id
            ),
            None,
        )
        if cached_anchor is None:
            return False
        cached_params = cached_anchor.get("params") or {}
        try:
            cached_signature = tuple(
                (name, round(float(cached_params[name]), 12))
                for name in COST_PARAMS
            )
        except (KeyError, TypeError, ValueError):
            return False
        if cached_signature != requested_signature:
            return False
    return True


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def clean_params(value: Any) -> Dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError("candidate params must be an object")
    missing = [name for name in COST_PARAMS if name not in value]
    extra = [name for name in value if name not in COST_PARAMS]
    if missing or extra:
        raise ValueError(
            f"candidate must contain exactly seven cost params; missing={missing}, extra={extra}"
        )
    return {name: float(value[name]) for name in COST_PARAMS}


def load_candidates(path: Path, scenario: str) -> List[Dict[str, Any]]:
    raw = read_json(path)
    rows = raw.get("candidates") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise ValueError("candidate manifest must be a list or contain candidates")
    result: List[Dict[str, Any]] = []
    seen = set()
    for index, raw_row in enumerate(rows):
        if not isinstance(raw_row, dict):
            continue
        row = copy.deepcopy(raw_row)
        row_scenario = str(row.get("scenario") or scenario).lower()
        if row_scenario == "mem":
            row_scenario = "memory"
        if row_scenario != scenario:
            continue
        row["scenario"] = scenario
        row["params"] = clean_params(row.get("params"))
        candidate_id = str(row.get("candidate_id") or f"candidate_{index:04d}")
        if candidate_id in seen:
            raise ValueError(f"duplicate candidate_id={candidate_id}")
        seen.add(candidate_id)
        row["candidate_id"] = candidate_id
        row.setdefault("candidate_index", index)
        result.append(row)
    if not result:
        raise ValueError(f"no candidates for scenario={scenario}")
    return result


def load_pool(path: Path, scenario: str, max_workers: int) -> List[Dict[str, Any]]:
    data = read_json(path)
    rows = data.get("workers") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("worker pool JSON must contain a workers list")
    workers: List[Dict[str, Any]] = []
    ids = set()
    for raw in rows:
        if not isinstance(raw, dict) or not raw.get("enabled", True):
            continue
        scenarios = [str(value).lower() for value in raw.get("scenarios", [])]
        scenarios = ["memory" if value == "mem" else value for value in scenarios]
        if scenarios and scenario not in scenarios:
            continue
        worker = copy.deepcopy(raw)
        worker_id = str(worker.get("worker_id") or "").strip()
        if not worker_id or worker_id in ids:
            raise ValueError(f"invalid or duplicate worker_id={worker_id!r}")
        ids.add(worker_id)
        worker["worker_id"] = worker_id
        workers.append(worker)
        if len(workers) >= max_workers:
            break
    if not workers:
        raise ValueError(f"no enabled workers support scenario={scenario}")
    if scenario == "io" and len(workers) > 1:
        isolation_ids = [str(row.get("disk_isolation_id") or "") for row in workers]
        verified = all(bool(row.get("disk_isolation_verified")) for row in workers)
        if not verified or any(not value for value in isolation_ids) or len(set(isolation_ids)) != len(isolation_ids):
            raise ValueError(
                "parallel IO replay requires distinct disk_isolation_id values and "
                "disk_isolation_verified=true on every worker"
            )
    return workers


def scenario_config_path(worker: Dict[str, Any], scenario: str) -> Path:
    value = (worker.get("scenario_configs") or {}).get(scenario)
    if not value:
        raise ValueError(
            f"worker={worker['worker_id']} is missing scenario_configs.{scenario}"
        )
    path = Path(str(value))
    if not path.is_absolute():
        path = ROOT / path
    return path


def materialize_worker_config(
    worker: Dict[str, Any],
    scenario: str,
    worker_dir: Path,
    run_mins: Optional[float] = None,
) -> Dict[str, Any]:
    config_path = scenario_config_path(worker, scenario)
    config = read_json(config_path)
    common_override = worker.get("config_override") or {}
    scenario_override = (worker.get("scenario_overrides") or {}).get(scenario) or {}
    effective = deep_merge(config, common_override)
    effective = deep_merge(effective, scenario_override)
    pg = effective.get("postgres") or {}
    host = str(pg.get("host") or "")
    port = int(pg.get("port") or 5432)
    if not host:
        raise ValueError(f"worker={worker['worker_id']} has no postgres.host")

    workload_cmd = str((effective.get("benchmark") or {}).get("workload_cmd") or "")
    match = re.search(r"(?P<prefix>(?:^|\s)-c\s+)(?P<path>[^\s]+)", workload_cmd)
    if match:
        source_props = Path(match.group("path"))
        if not source_props.is_absolute():
            source_props = ROOT / source_props
        props_text = source_props.read_text(encoding="utf-8")
        props_text = re.sub(
            r"jdbc:postgresql://[^:/?]+:\d+/",
            f"jdbc:postgresql://{host}:{port}/",
            props_text,
        )
        if run_mins is not None:
            run_value = (
                str(int(run_mins))
                if float(run_mins).is_integer()
                else str(float(run_mins))
            )
            props_text = re.sub(
                r"(?m)^(xpRunMins|apRunMins)=.*$",
                lambda match: f"{match.group(1)}={run_value}",
                props_text,
            )
        props_path = worker_dir / "workload" / f"{scenario}.props"
        props_path.parent.mkdir(parents=True, exist_ok=True)
        props_path.write_text(props_text, encoding="utf-8")
        workload_cmd = workload_cmd[: match.start("path")] + str(props_path) + workload_cmd[match.end("path") :]
        scenario_override = deep_merge(
            scenario_override,
            {"benchmark": {"workload_cmd": workload_cmd}},
        )

    vm_runtime = (effective.get("metrics") or {}).get("vm_runtime") or {}
    if run_mins is not None and bool(vm_runtime.get("fixed_horizon_success", False)):
        grace_s = max(0.0, float(vm_runtime.get("fixed_horizon_grace_s") or 5.0))
        scenario_override = deep_merge(
            scenario_override,
            {
                "metrics": {
                    "vm_runtime": {
                        "max_runtime_s": float(run_mins) * 60.0 + grace_s,
                    }
                }
            },
        )

    materialized = copy.deepcopy(worker)
    materialized["scenario_configs"] = {scenario: str(config_path)}
    materialized["scenario_overrides"] = {scenario: scenario_override}
    return materialized


def control_candidate(
    worker_id: str,
    scenario: str,
    kind: str,
    params: Dict[str, float],
) -> Dict[str, Any]:
    return {
        "candidate_id": f"__{kind}__{worker_id}",
        "scenario": scenario,
        "source": f"parallel_worker_{kind}",
        "params": copy.deepcopy(params),
    }


def qps(row: Dict[str, Any]) -> Optional[float]:
    delta = row.get("db_delta") or {}
    db_qps = delta.get("db_qps")
    try:
        if db_qps is not None and math.isfinite(float(db_qps)):
            return float(db_qps)
    except (TypeError, ValueError):
        pass
    benchmark = row.get("benchmark") or {}
    for key in ("xp_qps", "ap_qps", "qps", "db_qps"):
        value = benchmark.get(key)
        try:
            if value is not None and math.isfinite(float(value)):
                return float(value)
        except (TypeError, ValueError):
            continue
    elapsed = float(row.get("elapsed_s") or 0.0)
    commits = delta.get("xact_commit")
    if commits is not None and elapsed > 0:
        return float(commits) / elapsed
    return None


def utilization(row: Dict[str, Any]) -> Dict[str, float]:
    values = row.get("actual_utilization_pct") or {}
    return {
        name: float(values[name])
        for name in ("cpu", "memory", "io")
        if values.get(name) is not None
    }


def relative_pct(value: float, baseline: float) -> float:
    if baseline == 0:
        return 0.0 if value == 0 else math.copysign(float("inf"), value)
    return (value - baseline) / baseline * 100.0


def worker_command(
    *,
    python: str,
    scenario: str,
    manifest: Path,
    worker_config: Path,
    worker_dir: Path,
    args: argparse.Namespace,
    apply_vm_profile: bool,
) -> List[str]:
    cmd = [
        python,
        "-u",
        str(VALIDATOR),
        "--candidates-json",
        str(manifest),
        "--output-dir",
        str(worker_dir),
        "--worker-config-json",
        str(worker_config),
        "--scenarios",
        scenario,
        "--workload-timeout-s",
        str(args.workload_timeout_s),
        "--stop-on-failure",
        "--restore-defaults",
    ]
    if args.run_mins is not None:
        cmd.extend(["--run-mins", str(args.run_mins)])
    if apply_vm_profile:
        cmd.append("--apply-vm-profile")
    if args.force_vm_destroy:
        cmd.append("--force-vm-destroy")
    return cmd


def run_worker(worker_id: str, cmd: Sequence[str], log_path: Path) -> Dict[str, Any]:
    started = time.time()
    with log_path.open("w", encoding="utf-8") as handle:
        handle.write("$ " + " ".join(cmd) + "\n")
        handle.flush()
        proc = subprocess.Popen(
            list(cmd),
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            handle.write(line)
            handle.flush()
        return {
            "worker_id": worker_id,
            "returncode": proc.wait(),
            "elapsed_s": time.time() - started,
            "log_path": str(log_path),
        }


def effective_worker_config(worker: Dict[str, Any], scenario: str) -> Dict[str, Any]:
    config = read_json(scenario_config_path(worker, scenario))
    config = deep_merge(config, worker.get("config_override") or {})
    return deep_merge(config, (worker.get("scenario_overrides") or {}).get(scenario) or {})


def effective_worker_vm_resource(worker: Dict[str, Any], scenario: str) -> Dict[str, Any]:
    config = effective_worker_config(worker, scenario)
    resource = deep_merge(config.get("vm_resource") or {}, worker.get("vm_resource") or {})
    if worker.get("vm_name"):
        resource["vm_name"] = str(worker["vm_name"])
    return resource


def psql_command(config: Dict[str, Any], sql: str) -> Tuple[List[str], Dict[str, str]]:
    pg = config.get("postgres") or {}
    env = dict(os.environ)
    env["PGPASSWORD"] = str(pg.get("password") or pg.get("pg_password") or "CHANGE_ME")
    return (
        [
            "psql",
            "-h",
            str(pg.get("host") or ""),
            "-p",
            str(int(pg.get("port") or 5432)),
            "-U",
            str(pg.get("user") or "postgres"),
            "-d",
            "postgres",
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ],
        env,
    )


def vm_memory_size(vm_resource: Dict[str, Any]) -> str:
    """Return an exact libvirt memory size, allowing MiB profiles such as 2560M."""
    return str(vm_resource.get("memory_size") or f"{vm_resource.get('memory_gb')}G")


def apply_profile_preflight(
    worker: Dict[str, Any],
    scenario: str,
    *,
    force_destroy: bool,
    timeout_s: float,
    log_path: Path,
) -> Dict[str, Any]:
    """Apply one worker profile and wait until PostgreSQL is ready before replay."""
    started = time.time()
    config = effective_worker_config(worker, scenario)
    vm_runtime = (config.get("metrics") or {}).get("vm_runtime") or {}
    vm_resource = effective_worker_vm_resource(worker, scenario)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("w", encoding="utf-8") as handle:
            fixed_params = config.get("fixed_params") or {}
            if not isinstance(fixed_params, dict):
                raise ValueError("fixed_params must be an object")
            for name, value in fixed_params.items():
                safe_name = safe_identifier(name, "fixed parameter")
                quoted = str(value).replace("'", "''")
                sql = f"ALTER SYSTEM SET {safe_name} = '{quoted}';"
                cmd, env = psql_command(config, sql)
                handle.write("$ " + shlex.join(cmd) + "\n")
                handle.flush()
                proc = subprocess.run(
                    cmd,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=90.0,
                )
                handle.write(proc.stdout or "")
                handle.flush()
                if proc.returncode != 0:
                    raise RuntimeError(f"failed to stage fixed parameter {safe_name}")

            profile_cmd = [
                str(ROOT / "shell/set_vm_resource.sh"),
                str(vm_resource.get("vm_name")),
                str(vm_resource.get("cpu")),
                vm_memory_size(vm_resource),
                str(vm_resource.get("read_mb")),
                str(vm_resource.get("write_mb")),
                str(vm_resource.get("disk_dev", "vda")),
            ]
            profile_env = dict(os.environ)
            if force_destroy:
                profile_env["VM_RESOURCE_FORCE_DESTROY_ON_SHUTDOWN_TIMEOUT"] = "1"
            handle.write("$ " + shlex.join(profile_cmd) + "\n")
            handle.flush()
            proc = subprocess.run(
                profile_cmd,
                cwd=str(ROOT),
                env=profile_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
            )
            handle.write(proc.stdout or "")
            handle.flush()
            if proc.returncode != 0:
                raise RuntimeError(f"VM profile command failed exit={proc.returncode}")

            ssh_target = str(vm_runtime.get("ssh_target") or vm_runtime.get("host") or "")
            ssh_port = str(int(vm_runtime.get("ssh_port") or 22))
            ssh_base = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "ConnectTimeout=10",
                "-p",
                ssh_port,
                ssh_target,
            ]
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                proc = subprocess.run(
                    ssh_base + ["true"],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=20.0,
                )
                if proc.returncode == 0:
                    break
                time.sleep(5.0)
            else:
                raise RuntimeError(f"SSH did not become ready: {ssh_target}")

            start_cmd = (
                "sudo -n -u postgres /usr/lib/postgresql/17/bin/pg_ctl "
                "-D /home/anonymous/pg_master/pg_data status >/dev/null 2>&1 && "
                "sudo -n -u postgres /usr/lib/postgresql/17/bin/pg_ctl "
                "-D /home/anonymous/pg_master/pg_data -w -t 600 -m fast restart "
                "</dev/null >/tmp/resource_adaptive_pg_start.log 2>&1 || "
                "sudo -n -u postgres /usr/lib/postgresql/17/bin/pg_ctl "
                "-D /home/anonymous/pg_master/pg_data -w -t 600 start "
                "</dev/null >/tmp/resource_adaptive_pg_start.log 2>&1; "
                "rc=$?; cat /tmp/resource_adaptive_pg_start.log; exit $rc"
            )
            proc = subprocess.run(
                ssh_base + [f"bash -lc {shlex.quote(start_cmd)}"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
            )
            handle.write(proc.stdout or "")
            handle.flush()

            while time.time() < deadline:
                cmd, env = psql_command(config, "SELECT 1;")
                proc = subprocess.run(
                    cmd,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=20.0,
                )
                if proc.returncode == 0:
                    return {
                        "worker_id": worker["worker_id"],
                        "status": "ok",
                        "elapsed_s": time.time() - started,
                        "log_path": str(log_path),
                        "vm_resource": vm_resource,
                    }
                time.sleep(5.0)
            raise RuntimeError("PostgreSQL did not become ready after VM profile apply")
    except Exception as exc:
        return {
            "worker_id": worker["worker_id"],
            "status": "failed",
            "elapsed_s": time.time() - started,
            "log_path": str(log_path),
            "error": repr(exc),
            "vm_resource": vm_resource,
        }


def safe_identifier(value: Any, label: str) -> str:
    text = str(value or "")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        raise ValueError(f"unsafe PostgreSQL {label}: {text!r}")
    return text


def run_snapshot_restore_with_recovery_retry(
    command: List[str],
    *,
    timeout_s: float,
) -> Tuple[subprocess.CompletedProcess[str], int]:
    """Retry only transient PostgreSQL startup/recovery restore failures.

    A FILE_COPY restore is idempotent because its command starts with
    ``DROP DATABASE IF EXISTS``.  Freshly restarted guests can briefly accept
    TCP while PostgreSQL still reports recovery/startup, so treating that
    narrow window as a terminal candidate failure loses an otherwise valid
    independent replay.
    """
    transient_fragments = (
        "could not connect to server",
        "connection refused",
        "database system is starting up",
        "database system is not yet accepting connections",
        "database system is in recovery mode",
        "database system is shutting down",
    )
    deadline = time.monotonic() + max(1.0, float(timeout_s))
    attempts = 0
    while True:
        attempts += 1
        remaining = max(1.0, deadline - time.monotonic())
        proc = subprocess.run(
            command,
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=remaining,
        )
        if proc.returncode == 0:
            return proc, attempts
        detail = ((proc.stdout or "") + (proc.stderr or "")).strip().lower()
        transient = any(fragment in detail for fragment in transient_fragments)
        if not transient or time.monotonic() >= deadline:
            return proc, attempts
        time.sleep(min(3.0, max(0.1, deadline - time.monotonic())))


def restore_worker_database(
    worker: Dict[str, Any],
    scenario: str,
    timeout_s: float,
) -> Dict[str, Any]:
    started = time.time()
    config = effective_worker_config(worker, scenario)
    pg = config.get("postgres") or {}
    host = str(pg.get("host") or "")
    port = int(pg.get("port") or 5432)
    user = safe_identifier(pg.get("user") or "postgres", "user")
    password = str(pg.get("password") or pg.get("pg_password") or "CHANGE_ME")
    dbname = safe_identifier(pg.get("dbname") or "hybench_sf10x", "database")
    recovery = config.get("pre_benchmark_recovery") or {}
    snapshot = recovery.get("snapshot_restore") or {}
    if not snapshot.get("enabled", False):
        raise RuntimeError(
            f"worker={worker['worker_id']} fixed-snapshot replay requires enabled snapshot_restore"
        )
    template_db = safe_identifier(
        snapshot.get("template_db") or "hybench_sf10x_base",
        "template database",
    )
    seeded_reset = snapshot.get("seeded_qemu_reset") or {}
    if isinstance(seeded_reset, dict) and seeded_reset.get("enabled", False):
        from reset_seeded_qemu_worker import reset_seeded_worker  # type: ignore

        reset_payload = dict(seeded_reset)
        reset_payload.setdefault("database", dbname)
        reset_payload.setdefault("template_database", template_db)
        return reset_seeded_worker(
            reset_payload,
            expected_ip=host,
            timeout_s=timeout_s,
        )
    raw_strategy = str(snapshot.get("strategy") or "").strip().upper()
    if raw_strategy and raw_strategy not in {"WAL_LOG", "FILE_COPY"}:
        raise ValueError(
            f"worker={worker['worker_id']} unsupported snapshot restore strategy: "
            f"{raw_strategy!r}"
        )
    strategy_clause = f" STRATEGY={raw_strategy}" if raw_strategy else ""
    uri = f"postgresql://{user}:{password}@{host}:{port}/postgres"
    sql = [
        (
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{dbname}' AND pid <> pg_backend_pid();"
        ),
        f"DROP DATABASE IF EXISTS {dbname} WITH (FORCE);",
        (
            f"CREATE DATABASE {dbname} WITH TEMPLATE {template_db} OWNER {user}"
            f"{strategy_clause};"
        ),
    ]
    command = ["psql", uri, "-X", "-q", "-v", "ON_ERROR_STOP=1"]
    for statement in sql:
        command.extend(["-c", statement])
    proc, restore_attempts = run_snapshot_restore_with_recovery_retry(
        command,
        timeout_s=timeout_s,
    )
    if proc.returncode != 0:
        detail = ((proc.stdout or "") + (proc.stderr or "")).strip()
        raise RuntimeError(
            f"snapshot restore failed worker={worker['worker_id']} "
            f"attempts={restore_attempts} exit={proc.returncode}: {detail}"
        )
    return {
        "worker_id": worker["worker_id"],
        "host": host,
        "database": dbname,
        "template_database": template_db,
        "strategy": raw_strategy or "server_default",
        "attempts": restore_attempts,
        "elapsed_s": time.time() - started,
        "status": "ok",
    }


def resolve_pre_workload_setup_sql_files(
    worker: Dict[str, Any],
    scenario: str,
) -> List[Path]:
    """Return candidate-independent setup SQL paths from the effective scenario config."""
    config = effective_worker_config(worker, scenario)
    benchmark = config.get("benchmark") or {}
    raw_paths = benchmark.get("pre_workload_sql_files") or []
    if not isinstance(raw_paths, list):
        raise ValueError("benchmark.pre_workload_sql_files must be a list")
    paths: List[Path] = []
    for raw_path in raw_paths:
        path = Path(str(raw_path))
        if not path.is_absolute():
            path = ROOT / path
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"pre-workload SQL file does not exist: {path}")
        paths.append(path)
    return paths


def run_worker_setup_sql_under_boost(
    worker: Dict[str, Any],
    scenario: str,
    *,
    timeout_s: float = 900.0,
) -> Dict[str, Any]:
    """Run candidate-independent setup SQL while restore IO boost is still active.

    Control 1059: VACUUM/work_mem setup must not run under the formal low-IO
    measurement throttle. Returns an audit row; empty file list is a no-op ok.
    """
    started = time.time()
    config = effective_worker_config(worker, scenario)
    pg = config.get("postgres") or {}
    host = str(pg.get("host") or "")
    port = int(pg.get("port") or 5432)
    user = str(pg.get("user") or "postgres")
    password = str(pg.get("password") or pg.get("pg_password") or "CHANGE_ME")
    dbname = str(pg.get("dbname") or "hybench_sf10x")
    paths = resolve_pre_workload_setup_sql_files(worker, scenario)
    file_rows: List[Dict[str, Any]] = []
    env = dict(os.environ)
    env["PGPASSWORD"] = password
    for path in paths:
        file_started = time.time()
        proc = subprocess.run(
            [
                "psql",
                "-h",
                host,
                "-p",
                str(port),
                "-U",
                user,
                "-d",
                dbname,
                "-X",
                "-q",
                "-v",
                "ON_ERROR_STOP=1",
                "-f",
                str(path),
            ],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout_s)),
            env=env,
        )
        row = {
            "path": str(path),
            "elapsed_s": time.time() - file_started,
            "returncode": int(proc.returncode),
            "status": "ok" if proc.returncode == 0 else "failed",
        }
        if proc.returncode != 0:
            detail = ((proc.stdout or "") + (proc.stderr or "")).strip()
            row["error"] = detail[-2000:]
            file_rows.append(row)
            raise RuntimeError(
                f"setup-under-boost failed worker={worker.get('worker_id')} "
                f"file={path} exit={proc.returncode}: {detail[-500:]}"
            )
        file_rows.append(row)
    return {
        "worker_id": worker["worker_id"],
        "scenario": scenario,
        "phase": "setup_under_restore_boost",
        "status": "ok",
        "elapsed_s": time.time() - started,
        "sql_files": file_rows,
        "profile": "restore_io_boost_active",
        "completed_timestamp": time.strftime("%Y-%m-%d %H:%M:%S %z"),
    }


def clear_worker_pre_workload_sql_files(worker_config_path: Path) -> Dict[str, Any]:
    """Prevent the validator from re-running setup under measurement throttle."""
    data = read_json(worker_config_path)
    if not isinstance(data, dict):
        raise ValueError(f"worker config is not an object: {worker_config_path}")
    scenario_overrides = data.get("scenario_overrides") or {}
    if not isinstance(scenario_overrides, dict):
        scenario_overrides = {}
    cleared: List[str] = []
    for scenario, override in list(scenario_overrides.items()):
        if not isinstance(override, dict):
            override = {}
        benchmark = dict(override.get("benchmark") or {})
        benchmark["pre_workload_sql_files"] = []
        benchmark["hybench_setup_sql_files"] = []
        override = deep_merge(override, {"benchmark": benchmark})
        scenario_overrides[scenario] = override
        cleared.append(str(scenario))
    # Also clear on any top-level override used by materialize merges.
    config_override = data.get("config_override") or {}
    if isinstance(config_override, dict):
        config_override = deep_merge(
            config_override,
            {
                "benchmark": {
                    "pre_workload_sql_files": [],
                    "hybench_setup_sql_files": [],
                }
            },
        )
        data["config_override"] = config_override
    data["scenario_overrides"] = scenario_overrides
    data["setup_under_boost_cleared_pre_workload"] = True
    write_json(worker_config_path, data)
    return {
        "worker_config": str(worker_config_path),
        "cleared_scenarios": cleared,
        "setup_under_boost_cleared_pre_workload": True,
    }


def drop_worker_cache(
    worker: Dict[str, Any],
    scenario: str,
    timeout_s: float,
) -> Dict[str, Any]:
    started = time.time()
    config = effective_worker_config(worker, scenario)
    recovery = config.get("pre_benchmark_recovery") or {}
    drop_cfg = recovery.get("vm_drop_cache") or {}
    if not drop_cfg.get("enabled", False):
        return {
            "worker_id": worker["worker_id"],
            "status": "skipped",
            "elapsed_s": 0.0,
        }
    metrics = config.get("metrics", {}).get("vm_runtime") or {}
    target = str(metrics.get("ssh_target") or "")
    ssh_port = int(metrics.get("ssh_port") or 22)
    if not target:
        target = f"anonymous@{(config.get('postgres') or {}).get('host')}"
    command = str(drop_cfg.get("command") or "sudo -n /home/anonymous/bin/drop_vm_cache.sh")
    ssh_command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=10",
        "-p",
        str(ssh_port),
        target,
        f"bash -lc {json.dumps(command)}",
    ]
    deadline = time.time() + max(1.0, timeout_s)
    proc: Optional[subprocess.CompletedProcess[str]] = None
    attempts = 0
    while attempts < 3 and time.time() < deadline:
        attempts += 1
        proc = subprocess.run(
            ssh_command,
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=max(1.0, min(45.0, deadline - time.time())),
        )
        if proc.returncode == 0:
            break
        if attempts < 3 and time.time() < deadline:
            time.sleep(2.0)
    assert proc is not None
    if proc.returncode != 0:
        detail = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if drop_cfg.get("required", False):
            raise RuntimeError(
                f"drop cache failed worker={worker['worker_id']} target={target}:{ssh_port} "
                f"attempts={attempts} exit={proc.returncode}: {detail}"
            )
        status = "failed_optional"
    else:
        status = "ok"
    return {
        "worker_id": worker["worker_id"],
        "status": status,
        "attempts": attempts,
        "elapsed_s": time.time() - started,
    }


def build_replay_waves(
    candidates: Sequence[Dict[str, Any]],
    workers: Sequence[Dict[str, Any]],
    scenario: str,
    anchor: Optional[Dict[str, Any]],
    *,
    cache_reused: bool = False,
    cached_anchor_is_candidate: bool = False,
    reuse_baselines: Optional[bool] = None,
    reuse_anchors: Optional[bool] = None,
    baseline_only: bool = False,
) -> List[List[Tuple[Dict[str, Any], Dict[str, Any]]]]:
    """Build ordered waves: optional baseline, optional anchor, then candidates.

    baseline_only=True emits exactly one baseline wave and stops (no anchor/candidates).
    Partial cache reuse can keep worker-local baselines while still replaying anchors.
    When reuse_baselines/reuse_anchors are None, both follow legacy cache_reused.
    """
    waves: List[List[Tuple[Dict[str, Any], Dict[str, Any]]]] = []
    skip_baselines = bool(cache_reused if reuse_baselines is None else reuse_baselines)
    skip_anchors = bool(cache_reused if reuse_anchors is None else reuse_anchors)

    if not skip_baselines:
        waves.append(
            [
                (worker, control_candidate(worker["worker_id"], scenario, "baseline", DEFAULT_PARAMS))
                for worker in workers
            ]
        )
    if baseline_only:
        return waves

    if anchor is None:
        raise ValueError("anchor is required unless baseline_only=True")
    if not skip_anchors:
        waves.append(
            [
                (worker, control_candidate(worker["worker_id"], scenario, "anchor", anchor["params"]))
                for worker in workers
            ]
        )
    # Exclude the anchor candidate from later waves when an anchor control wave
    # will measure it, or when a cached matching anchor already exists.
    exclude_anchor_candidate = (not skip_anchors) or cached_anchor_is_candidate
    pending = [
        candidate
        for candidate in candidates
        if not (
            candidate["candidate_id"] == anchor["candidate_id"] and exclude_anchor_candidate
        )
    ]
    for start in range(0, len(pending), len(workers)):
        chunk = pending[start : start + len(workers)]
        waves.append([(workers[index], candidate) for index, candidate in enumerate(chunk)])
    return waves


def expected_metric_sources(worker: Dict[str, Any], scenario: str) -> Dict[str, str]:
    try:
        config = effective_worker_config(worker, scenario)
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return {}
    vm_runtime = (config.get("metrics") or {}).get("vm_runtime") or {}
    cpu_mode = str(vm_runtime.get("cpu_mode") or "pg_proc").strip().lower()
    memory_mode = str(vm_runtime.get("memory_mode") or "pg_proc_rss").strip().lower()
    io_mode = str(vm_runtime.get("io_mode") or "disk_device").strip().lower()
    return {
        "cpu": "vm_runtime_ssh" if cpu_mode == "vm_busy" else "vm_pg_proc_cpu",
        "memory": {
            "vm_used": "vm_meminfo_used",
            "pg_proc_pss": "vm_pg_proc_pss",
            "pg_proc_rss": "vm_pg_proc_rss",
        }.get(memory_mode, ""),
        "io": "vm_disk_device" if io_mode == "disk_device" else "vm_pg_proc_io",
    }


def baseline_gate(
    worker_rows: Dict[str, List[Dict[str, Any]]],
    workers: Sequence[Dict[str, Any]],
    args: argparse.Namespace,
) -> Dict[str, Any]:
    """Validate worker baselines before spending time on anchor/candidate waves."""
    scenario = "memory" if getattr(args, "scenario", "cpu") == "mem" else getattr(args, "scenario", "cpu")
    records: List[Dict[str, Any]] = []
    failures: List[str] = []
    min_stable_samples = max(
        1,
        int(getattr(args, "min_stable_samples", 45) or 45),
    )
    # Formal IO baseline preferred band (fail-closed). Do not widen these.
    io_baseline_min_pct = float(getattr(args, "io_baseline_min_pct", 80.0) or 80.0)
    io_baseline_max_pct = float(getattr(args, "io_baseline_max_pct", 95.0) or 95.0)
    io_non_target_max_pct = float(getattr(args, "io_non_target_max_pct", 78.0) or 78.0)
    for worker in workers:
        worker_id = str(worker["worker_id"])
        rows = worker_rows.get(worker_id, [])
        baseline = next(
            (row for row in rows if row.get("candidate_id") == f"__baseline__{worker_id}"),
            None,
        )
        baseline_util = utilization(baseline or {})
        expected_sources = expected_metric_sources(worker, scenario)
        record = {
            "worker_id": worker_id,
            "baseline": baseline,
            "baseline_qps": qps(baseline or {}),
            "baseline_utilization_pct": baseline_util,
            "expected_metric_sources": expected_sources,
        }
        records.append(record)
        if not baseline:
            failures.append(f"missing_baseline:{worker_id}")
            continue
        if baseline.get("status") != "ok":
            failures.append(f"baseline_failed:{worker_id}")
        stats = baseline.get("raw_resource_stats") or {}
        workload_exit = stats.get("workload_exit") or {}
        if (
            int(workload_exit.get("returncode") or 0) != 0
            and not bool(workload_exit.get("sigterm_treated_as_success", False))
        ):
            failures.append(f"baseline_workload_not_clean:{worker_id}")
        stable = stats.get("stable_sample_count") or {}
        for resource in ("cpu", "memory", "io"):
            sample_count = int(stable.get(resource) or 0)
            if sample_count <= 0:
                failures.append(f"baseline_{resource}_no_stable_samples:{worker_id}")
            elif sample_count < min_stable_samples:
                failures.append(
                    f"baseline_{resource}_samples_below_{min_stable_samples}:{worker_id}:{sample_count}"
                )
            expected = expected_sources.get(resource)
            actual_source = str(((stats.get(f"{resource}_source") or {}).get("value") or ""))
            if expected and actual_source != expected:
                failures.append(
                    f"baseline_{resource}_source_mismatch:{worker_id}:"
                    f"{actual_source or 'missing'}!={expected}"
                )
        if float((baseline.get("actual_resource") or {}).get("io") or 0.0) <= 0.0:
            failures.append(f"baseline_io_nonpositive:{worker_id}")
        io_warning = str(((stats.get("io_metric_warning") or {}).get("value") or "")).strip()
        if io_warning:
            failures.append(f"baseline_io_metric_warning:{worker_id}:{io_warning}")
        # Formal IO metric gate (0942): require explicit vda device provenance.
        if scenario == "io" and expected_sources.get("io") == "vm_disk_device":
            io_devices = str(((stats.get("io_disk_devices") or {}).get("value") or "")).strip()
            expected_disk = str(
                ((worker.get("vm_resource") or {}).get("disk_dev") or "vda")
            ).strip() or "vda"
            if not io_devices:
                failures.append(f"baseline_io_device_missing:{worker_id}")
            elif expected_disk not in {
                part.strip() for part in io_devices.split(",") if part.strip()
            }:
                failures.append(
                    f"baseline_io_device_mismatch:{worker_id}:{io_devices}!={expected_disk}"
                )
        io_basis = baseline.get("io_capability_basis") or {}
        if bool(io_basis.get("capability_mismatch")):
            failures.append(
                f"baseline_io_capability_mismatch:{worker_id}:"
                f"{io_basis.get('capability_mismatch_reason') or 'mismatch'}"
            )
        config = effective_worker_config(worker, scenario)
        overload_pct = float((config.get("scoring") or {}).get("overload_threshold") or 0.8) * 100.0
        required_resources = ("cpu", "memory", "io")
        # Family causal diagnostics may legitimately sit below the final Memory bottleneck.
        # Keep workload/sample/source/IO quality gates; only skip single-bottleneck geometry.
        diagnostic_local_pair = bool(getattr(args, "diagnostic_local_pair", False))
        if all(name in baseline_util for name in required_resources):
            if scenario == "none":
                overloaded = [name for name in required_resources if baseline_util[name] >= overload_pct]
                if overloaded:
                    failures.append(f"baseline_none_has_bottleneck:{worker_id}:{','.join(overloaded)}")
            elif not diagnostic_local_pair:
                if scenario == "io":
                    io_util = float(baseline_util.get("io") or 0.0)
                    if io_util < io_baseline_min_pct:
                        failures.append(
                            f"baseline_io_below_{io_baseline_min_pct:g}:{worker_id}:{io_util}"
                        )
                    if io_util > io_baseline_max_pct:
                        failures.append(
                            f"baseline_io_above_{io_baseline_max_pct:g}_preferred_band:"
                            f"{worker_id}:{io_util}"
                        )
                    for resource in ("cpu", "memory"):
                        if float(baseline_util.get(resource) or 0.0) >= io_non_target_max_pct:
                            failures.append(
                                f"baseline_non_target_bottleneck:{worker_id}:{resource}"
                            )
                else:
                    if baseline_util[scenario] < overload_pct:
                        failures.append(f"baseline_target_not_bottleneck:{worker_id}:{scenario}")
                    for resource in required_resources:
                        if resource != scenario and baseline_util[resource] >= overload_pct:
                            failures.append(f"baseline_non_target_bottleneck:{worker_id}:{resource}")

    valid_qps = [float(record["baseline_qps"]) for record in records if record["baseline_qps"] is not None]
    median_qps = statistics.median(valid_qps) if valid_qps else None
    for record in records:
        if median_qps is not None and record["baseline_qps"] is not None:
            drift = relative_pct(float(record["baseline_qps"]), median_qps)
            record["baseline_qps_drift_pct"] = drift
            if abs(drift) > args.max_baseline_qps_drift_pct:
                failures.append(f"baseline_qps_drift:{record['worker_id']}")
    for resource in ("cpu", "memory", "io"):
        values = [
            record["baseline_utilization_pct"].get(resource)
            for record in records
            if record["baseline_utilization_pct"].get(resource) is not None
        ]
        if len(values) == len(records) and values:
            median_value = statistics.median(float(value) for value in values)
            for record in records:
                drift = float(record["baseline_utilization_pct"][resource]) - median_value
                record.setdefault("baseline_resource_drift_pp", {})[resource] = drift
                if abs(drift) > args.max_baseline_resource_drift_pp:
                    failures.append(f"baseline_{resource}_drift:{record['worker_id']}")
    return {
        "passed": not failures,
        "failures": sorted(set(failures)),
        "workers": records,
        "thresholds": {
            "single_bottleneck_threshold_pct": 80.0,
            "min_stable_samples": min_stable_samples,
            "io_baseline_min_pct": io_baseline_min_pct,
            "io_baseline_max_pct": io_baseline_max_pct,
            "io_non_target_max_pct": io_non_target_max_pct,
            "max_baseline_qps_drift_pct": args.max_baseline_qps_drift_pct,
            "max_baseline_resource_drift_pp": args.max_baseline_resource_drift_pp,
        },
    }


def drift_gate(
    worker_rows: Dict[str, List[Dict[str, Any]]],
    workers: Sequence[Dict[str, Any]],
    args: argparse.Namespace,
) -> Dict[str, Any]:
    records: List[Dict[str, Any]] = []
    for worker in workers:
        worker_id = worker["worker_id"]
        rows = worker_rows.get(worker_id, [])
        baseline = next((row for row in rows if row.get("candidate_id") == f"__baseline__{worker_id}"), None)
        anchor = next((row for row in rows if row.get("candidate_id") == f"__anchor__{worker_id}"), None)
        records.append(
            {
                "worker_id": worker_id,
                "baseline": baseline,
                "anchor": anchor,
                "baseline_qps": qps(baseline or {}),
                "anchor_qps": qps(anchor or {}),
                "baseline_utilization_pct": utilization(baseline or {}),
                "anchor_utilization_pct": utilization(anchor or {}),
            }
        )
    failures: List[str] = []

    def control_quality(
        row: Optional[Dict[str, Any]],
        kind: str,
        worker_id: str,
        expected_sources: Dict[str, str],
    ) -> None:
        if not row:
            failures.append(f"missing_{kind}:{worker_id}")
            return
        stats = row.get("raw_resource_stats") or {}
        workload_exit = stats.get("workload_exit") or {}
        if (
            int(workload_exit.get("returncode") or 0) != 0
            and not bool(workload_exit.get("sigterm_treated_as_success", False))
        ):
            failures.append(f"{kind}_workload_not_clean:{worker_id}")
        stable = stats.get("stable_sample_count") or {}
        for resource in ("cpu", "memory", "io"):
            if int(stable.get(resource) or 0) <= 0:
                failures.append(f"{kind}_{resource}_no_stable_samples:{worker_id}")
            expected = expected_sources.get(resource)
            actual = str(((stats.get(f"{resource}_source") or {}).get("value") or ""))
            if expected and actual != expected:
                failures.append(
                    f"{kind}_{resource}_source_mismatch:{worker_id}:{actual or 'missing'}!={expected}"
                )
        actual = row.get("actual_resource") or {}
        if float(actual.get("io") or 0.0) <= 0.0:
            failures.append(f"{kind}_io_nonpositive:{worker_id}")

    scenario = "memory" if getattr(args, "scenario", "cpu") == "mem" else getattr(args, "scenario", "cpu")
    sources_by_worker = {
        str(worker["worker_id"]): expected_metric_sources(worker, scenario)
        for worker in workers
    }
    for record in records:
        expected_sources = sources_by_worker.get(record["worker_id"], {})
        record["expected_metric_sources"] = expected_sources
        control_quality(
            record.get("baseline"),
            "baseline",
            record["worker_id"],
            expected_sources,
        )
        baseline_util = record["baseline_utilization_pct"]
        config = effective_worker_config(
            next(worker for worker in workers if worker["worker_id"] == record["worker_id"]),
            scenario,
        )
        overload_pct = float((config.get("scoring") or {}).get("overload_threshold") or 0.8) * 100.0
        required_resources = ("cpu", "memory", "io")
        diagnostic_local_pair = bool(getattr(args, "diagnostic_local_pair", False))
        if all(name in baseline_util for name in required_resources):
            if scenario == "none":
                overloaded = [name for name in required_resources if baseline_util[name] >= overload_pct]
                if overloaded:
                    failures.append(
                        f"baseline_none_has_bottleneck:{record['worker_id']}:{','.join(overloaded)}"
                    )
            elif not diagnostic_local_pair:
                if baseline_util[scenario] < overload_pct:
                    failures.append(
                        f"baseline_target_not_bottleneck:{record['worker_id']}:{scenario}"
                    )
                for resource in required_resources:
                    if resource != scenario and baseline_util[resource] >= overload_pct:
                        failures.append(
                            f"baseline_non_target_bottleneck:{record['worker_id']}:{resource}"
                        )
        control_quality(
            record.get("anchor"),
            "anchor",
            record["worker_id"],
            expected_sources,
        )
    if any(record["baseline_qps"] is None or record["anchor_qps"] is None for record in records):
        failures.append("missing_worker_baseline_or_anchor_qps")
    if any(
        not record["baseline"]
        or not record["anchor"]
        or record["baseline"].get("status") != "ok"
        or record["anchor"].get("status") != "ok"
        for record in records
    ):
        failures.append("worker_baseline_or_anchor_failed")

    valid_qps = [float(record["baseline_qps"]) for record in records if record["baseline_qps"] is not None]
    median_qps = statistics.median(valid_qps) if valid_qps else None
    for record in records:
        if median_qps is not None and record["baseline_qps"] is not None:
            record["baseline_qps_drift_pct"] = relative_pct(float(record["baseline_qps"]), median_qps)
            if abs(record["baseline_qps_drift_pct"]) > args.max_baseline_qps_drift_pct:
                failures.append(f"baseline_qps_drift:{record['worker_id']}")
        baseline_util = record["baseline_utilization_pct"]
        anchor_util = record["anchor_utilization_pct"]
        record["anchor_qps_delta_pct"] = (
            relative_pct(float(record["anchor_qps"]), float(record["baseline_qps"]))
            if record["anchor_qps"] is not None and record["baseline_qps"] is not None
            else None
        )
        record["anchor_resource_delta_pp"] = {
            name: float(anchor_util[name]) - float(baseline_util[name])
            for name in ("cpu", "memory", "io")
            if name in anchor_util and name in baseline_util
        }
        qps_delta = record["anchor_qps_delta_pct"]
        resource_delta = record["anchor_resource_delta_pp"]
        anchor_util = record["anchor_utilization_pct"]
        record["anchor_long_term_goal_hit"] = bool(
            scenario in {"cpu", "memory", "io"}
            and qps_delta is not None
            and qps_delta >= 15.0
            and resource_delta.get(scenario) is not None
            and resource_delta[scenario] <= -5.0
            and anchor_util.get(scenario) is not None
            and anchor_util[scenario] < 80.0
            and any(
                resource_delta.get(resource, 0.0) > 0.0
                for resource in ("cpu", "memory", "io")
                if resource != scenario
            )
        )

    for resource in ("cpu", "memory", "io"):
        values = [
            record["baseline_utilization_pct"].get(resource)
            for record in records
            if record["baseline_utilization_pct"].get(resource) is not None
        ]
        if len(values) == len(records) and values:
            median_value = statistics.median(float(value) for value in values)
            for record in records:
                drift = float(record["baseline_utilization_pct"][resource]) - median_value
                record.setdefault("baseline_resource_drift_pp", {})[resource] = drift
                if abs(drift) > args.max_baseline_resource_drift_pp:
                    failures.append(f"baseline_{resource}_drift:{record['worker_id']}")

    anchor_qps_deltas = [record["anchor_qps_delta_pct"] for record in records if record["anchor_qps_delta_pct"] is not None]
    if len(anchor_qps_deltas) == len(records) and anchor_qps_deltas:
        spread = max(anchor_qps_deltas) - min(anchor_qps_deltas)
        if spread > args.max_anchor_qps_delta_spread_pp:
            failures.append("anchor_qps_delta_spread")
    for resource in ("cpu", "memory", "io"):
        values = [
            record["anchor_resource_delta_pp"].get(resource)
            for record in records
            if record["anchor_resource_delta_pp"].get(resource) is not None
        ]
        if len(values) == len(records) and values:
            if max(values) - min(values) > args.max_anchor_resource_delta_spread_pp:
                failures.append(f"anchor_{resource}_delta_spread")
    goal_rich_override = {
        "enabled": bool(getattr(args, "allow_goal_rich_anchor_qps_spread", False)),
        "applied": False,
        "requirements": {
            "min_db_qps_delta_pct": 15.0,
            "min_target_drop_pp": 5.0,
            "target_exit_bottleneck_pct": 80.0,
            "require_any_non_target_up": True,
        },
    }
    if (
        goal_rich_override["enabled"]
        and set(failures) == {"anchor_qps_delta_spread"}
        and records
        and all(record.get("anchor_long_term_goal_hit", False) for record in records)
    ):
        failures.remove("anchor_qps_delta_spread")
        goal_rich_override["applied"] = True
    return {
        "passed": not failures,
        "failures": sorted(set(failures)),
        "workers": records,
        "goal_rich_anchor_qps_spread_override": goal_rich_override,
        "thresholds": {
            "single_bottleneck_threshold_pct": 80.0,
            "max_baseline_qps_drift_pct": args.max_baseline_qps_drift_pct,
            "max_baseline_resource_drift_pp": args.max_baseline_resource_drift_pp,
            "max_anchor_qps_delta_spread_pp": args.max_anchor_qps_delta_spread_pp,
            "max_anchor_resource_delta_spread_pp": args.max_anchor_resource_delta_spread_pp,
        },
    }


def aggregate_results(
    candidates: Sequence[Dict[str, Any]],
    worker_rows: Dict[str, List[Dict[str, Any]]],
    gate: Dict[str, Any],
) -> List[Dict[str, Any]]:
    by_id = {str(row["candidate_id"]): row for row in candidates}
    baseline_by_worker = {
        record["worker_id"]: record for record in gate.get("workers", [])
    }
    aggregated: List[Dict[str, Any]] = []
    for worker_id, rows in worker_rows.items():
        baseline = baseline_by_worker.get(worker_id) or {}
        baseline_qps = baseline.get("baseline_qps")
        baseline_util = baseline.get("baseline_utilization_pct") or {}
        baseline_row = baseline.get("baseline") or {}
        baseline_benchmark = baseline_row.get("benchmark") or {}
        for row in rows:
            candidate_id = str(row.get("candidate_id") or "")
            if candidate_id not in by_id:
                continue
            enriched = copy.deepcopy(by_id[candidate_id])
            enriched.update(copy.deepcopy(row))
            candidate_qps = qps(row)
            candidate_util = utilization(row)
            enriched["parallel_worker_id"] = worker_id
            enriched["worker_baseline_qps"] = baseline_qps
            enriched["worker_baseline_utilization_pct"] = baseline_util
            enriched["worker_baseline_benchmark"] = copy.deepcopy(
                baseline_benchmark
            )
            enriched["db_qps_delta_pct"] = (
                relative_pct(candidate_qps, float(baseline_qps))
                if candidate_qps is not None and baseline_qps is not None
                else None
            )
            enriched["resource_delta_pp"] = {
                name: float(candidate_util[name]) - float(baseline_util[name])
                for name in ("cpu", "memory", "io")
                if name in candidate_util and name in baseline_util
            }
            aggregated.append(enriched)
    order = {str(row["candidate_id"]): index for index, row in enumerate(candidates)}
    aggregated.sort(key=lambda row: order.get(str(row.get("candidate_id")), 10**9))
    return aggregated


def run(args: argparse.Namespace) -> int:
    scenario = "memory" if args.scenario == "mem" else args.scenario
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    baseline_only = bool(getattr(args, "baseline_only", False))
    candidates: List[Dict[str, Any]] = []
    if not baseline_only:
        if not args.candidates_json:
            raise ValueError("--candidates-json is required unless --baseline-only")
        candidates = load_candidates(Path(args.candidates_json), scenario)
        candidate_limit = getattr(args, "candidate_limit", None)
        if candidate_limit is not None:
            candidates = candidates[: max(1, int(candidate_limit))]
    workers = load_pool(Path(args.worker_pool_json), scenario, args.max_workers)
    signature = pool_signature(workers, scenario)
    cache_info = inspect_calibration_cache(
        args.calibration_cache_json,
        signature=signature,
        scenario=scenario,
        max_age_s=args.calibration_cache_max_age_s,
    )
    # baseline-only never reuses a prior cache: it must measure current-state baselines.
    if baseline_only:
        cache_info = {
            "usable": False,
            "has_baselines": False,
            "has_anchors": False,
            "worker_control_rows": {},
            "reason": "baseline_only_forces_fresh_measurement",
            "age_s": None,
            "controls_level": None,
            "path": args.calibration_cache_json or "",
        }
    cached_control_rows = cache_info.get("worker_control_rows") or {}
    reuse_baselines = bool(cache_info.get("usable") and cache_info.get("has_baselines"))
    cached_anchors_available = bool(cache_info.get("usable") and cache_info.get("has_anchors"))
    reuse_anchors = False
    cache_reused = bool(reuse_baselines)
    anchor: Optional[Dict[str, Any]] = None
    if not baseline_only:
        if args.anchor_candidate_id:
            anchor = next(
                (row for row in candidates if row.get("candidate_id") == args.anchor_candidate_id),
                None,
            )
        if anchor is None:
            anchor = candidates[0]
    cached_anchor_is_candidate = bool(
        cached_anchors_available
        and anchor is not None
        and cached_anchor_matches(cached_control_rows, workers, anchor)
    )
    # Never reuse an anchor control measured at different coordinates. Baseline
    # controls may still be reused when the pool/profile signature is current.
    reuse_anchors = cached_anchor_is_candidate
    cache_reused = bool(reuse_baselines or reuse_anchors)

    worker_configs: Dict[str, Path] = {}
    worker_plans: List[Dict[str, Any]] = []
    for worker in workers:
        worker_id = worker["worker_id"]
        worker_dir = output_dir / "workers" / worker_id
        materialized = materialize_worker_config(
            worker,
            scenario,
            worker_dir,
            run_mins=args.run_mins,
        )
        worker_config_path = worker_dir / "worker_config.json"
        write_json(worker_config_path, materialized)
        worker_configs[worker_id] = worker_config_path
        worker_plans.append(
            {
                "worker_id": worker_id,
                "worker_config": str(worker_config_path),
            }
        )
    waves = build_replay_waves(
        candidates,
        workers,
        scenario,
        anchor,
        cache_reused=cache_reused,
        cached_anchor_is_candidate=cached_anchor_is_candidate,
        reuse_baselines=reuse_baselines,
        reuse_anchors=reuse_anchors,
        baseline_only=baseline_only,
    )
    wave_plans: List[Dict[str, Any]] = []
    for wave_index, entries in enumerate(waves):
        wave_items = []
        for worker, candidate in entries:
            worker_id = worker["worker_id"]
            wave_dir = output_dir / "workers" / worker_id / "waves" / f"wave_{wave_index:03d}"
            manifest_path = wave_dir / "candidate.json"
            write_json(manifest_path, [candidate])
            cmd = worker_command(
                python=args.python,
                scenario=scenario,
                manifest=manifest_path,
                worker_config=worker_configs[worker_id],
                worker_dir=wave_dir,
                args=args,
                apply_vm_profile=False,
            )
            wave_items.append(
                {
                    "worker_id": worker_id,
                    "candidate_id": candidate["candidate_id"],
                    "manifest": str(manifest_path),
                    "worker_config": str(worker_configs[worker_id]),
                    "command": cmd,
                    "log_path": str(wave_dir / "worker.log"),
                    "wave_dir": str(wave_dir),
                }
            )
        wave_plans.append(
            {
                "wave_index": wave_index,
                "candidate_count": len(wave_items),
                "items": wave_items,
            }
        )
    plan = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "scenario": scenario,
        "baseline_only": baseline_only,
        "candidate_count": len(candidates),
        "worker_count": len(workers),
        "anchor_candidate_id": (anchor or {}).get("candidate_id"),
        "io_parallel": scenario == "io" and len(workers) > 1,
        "calibration_cache_reused": cache_reused,
        "reuse_baselines": reuse_baselines,
        "reuse_anchors": reuse_anchors,
        "calibration_cache_inspect": {
            "usable": cache_info.get("usable"),
            "reason": cache_info.get("reason"),
            "has_baselines": cache_info.get("has_baselines"),
            "has_anchors": cache_info.get("has_anchors"),
            "controls_level": cache_info.get("controls_level"),
            "age_s": cache_info.get("age_s"),
        },
        "cached_anchor_is_candidate": cached_anchor_is_candidate,
        "calibration_cache_json": args.calibration_cache_json,
        "pool_signature": signature,
        "workers": worker_plans,
        "fixed_snapshot_waves": True,
        "stops_before_anchor_and_candidates": baseline_only,
        "profile_preflight": {
            "enabled": bool(args.apply_vm_profile),
            "barrier_before_snapshot_restore": True,
            "workers": [
                {
                    "worker_id": worker["worker_id"],
                    "vm_resource": effective_worker_vm_resource(worker, scenario),
                }
                for worker in workers
            ],
        },
        "wave_count": len(wave_plans),
        "waves": wave_plans,
    }
    write_json(output_dir / "parallel_replay_plan.json", plan)
    if args.plan_only:
        print(f"wrote parallel replay plan: {output_dir / 'parallel_replay_plan.json'}", flush=True)
        return 0

    profile_runs: List[Dict[str, Any]] = []
    if args.apply_vm_profile:
        print(f"profile preflight starting for {len(workers)} workers", flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers)) as executor:
            future_map = {
                executor.submit(
                    apply_profile_preflight,
                    worker,
                    scenario,
                    force_destroy=args.force_vm_destroy,
                    timeout_s=args.snapshot_restore_timeout_s,
                    log_path=output_dir / "profile_preflight" / f"{worker['worker_id']}.log",
                ): worker
                for worker in workers
            }
            for future in concurrent.futures.as_completed(future_map):
                result = future.result()
                profile_runs.append(result)
                print(
                    f"profile preflight worker={result['worker_id']} status={result['status']} "
                    f"elapsed={result['elapsed_s']:.1f}s",
                    flush=True,
                )
        write_json(output_dir / "profile_preflight.json", profile_runs)
        if any(row.get("status") != "ok" for row in profile_runs):
            print("profile preflight failed; replay waves are not started", flush=True)
            return 1
        print("profile preflight barrier passed", flush=True)

    runs: List[Dict[str, Any]] = []
    restore_runs: List[Dict[str, Any]] = []
    cache_drop_runs: List[Dict[str, Any]] = []
    # Accumulate across all waves of a paired run (default-A / teacher / default-B).
    # Do not reinitialize per wave or earlier wave rows are overwritten on disk.
    restore_profile_audit: List[Dict[str, Any]] = []
    setup_ordering_audit: List[Dict[str, Any]] = []
    baseline_gate_result: Optional[Dict[str, Any]] = None
    anchor_gate_result: Optional[Dict[str, Any]] = None
    worker_rows: Dict[str, List[Dict[str, Any]]] = {
        worker["worker_id"]: [
            *copy.deepcopy(cached_control_rows.get(worker["worker_id"], []))
        ]
        for worker in workers
    }
    worker_by_id = {worker["worker_id"]: worker for worker in workers}
    for wave in wave_plans:
        wave_index = int(wave["wave_index"])
        items = wave["items"]
        wave_workers = [worker_by_id[item["worker_id"]] for item in items]
        print(
            f"wave={wave_index} restoring fixed snapshot for {len(wave_workers)} workers",
            flush=True,
        )
        # IO-only optional restore/measurement profile switch:
        # enabled only when scenario==io AND config sets
        # pre_benchmark_recovery.snapshot_restore.restore_io_boost.enabled=true.
        # Ordering (when enabled, control 1059):
        #   boost -> restore -> setup SQL under boost -> measurement throttle
        #   -> cache drop -> worker (validator must not re-run setup under throttle).
        # CPU/Memory/None (or disabled) keep the original path with zero live throttle calls.
        workers_with_boost: Dict[str, Dict[str, Any]] = {}
        for worker in wave_workers:
            try:
                boost = resolve_restore_io_boost(worker, scenario)
            except Exception as exc:
                fail_row = {
                    "worker_id": worker["worker_id"],
                    "wave_index": wave_index,
                    "scenario": scenario,
                    "phase": "restore_io_boost_resolve",
                    "status": "failed",
                    "error": repr(exc),
                    "profile_switch_timestamp": time.strftime("%Y-%m-%d %H:%M:%S %z"),
                }
                restore_profile_audit.append(fail_row)
                write_json(output_dir / "restore_measurement_profile_audit.json", restore_profile_audit)
                print(
                    f"wave={wave_index} restore IO boost resolve failed "
                    f"worker={worker['worker_id']}: {exc}",
                    flush=True,
                )
                return 1
            if boost is None:
                continue
            workers_with_boost[str(worker["worker_id"])] = boost

        for worker in wave_workers:
            boost = workers_with_boost.get(str(worker["worker_id"]))
            if boost is None:
                continue
            vm_res = effective_worker_vm_resource(worker, scenario) or {}
            measure_read = max(1, _to_int(vm_res.get("read_mb"), 100))
            measure_write = max(0, _to_int(vm_res.get("write_mb"), 100))
            boost_row: Dict[str, Any] = {
                "worker_id": worker["worker_id"],
                "wave_index": wave_index,
                "scenario": scenario,
                "phase": "restore_io_boost",
                "restore_io_boost_enabled": True,
                "measurement_profile": {
                    "read_mb": measure_read,
                    "write_mb": measure_write,
                },
                "restore_profile": {
                    "read_mb": int(boost["read_mb"]),
                    "write_mb": int(boost["write_mb"]),
                    "source": boost.get("source"),
                },
                "profile_switch_timestamp": time.strftime("%Y-%m-%d %H:%M:%S %z"),
            }
            try:
                boost_row["live_throttle"] = live_disk_throttle(
                    str(vm_res.get("vm_name") or worker.get("vm_name") or worker["worker_id"]),
                    str(vm_res.get("disk_dev") or "vda"),
                    read_mb=int(boost["read_mb"]),
                    write_mb=int(boost["write_mb"]),
                    verify=True,
                )
                boost_row["status"] = "ok"
                boost_row["requested_read_bytes_sec"] = int(boost["read_mb"]) * 1024 * 1024
                boost_row["requested_write_bytes_sec"] = int(boost["write_mb"]) * 1024 * 1024
                boost_row["observed_read_bytes_sec"] = boost_row["live_throttle"].get(
                    "observed_read_bytes_sec"
                )
                boost_row["observed_write_bytes_sec"] = boost_row["live_throttle"].get(
                    "observed_write_bytes_sec"
                )
                boost_row["set_command"] = boost_row["live_throttle"].get("set_command")
                boost_row["query_command"] = boost_row["live_throttle"].get("query_command")
            except Exception as exc:
                boost_row["status"] = "failed"
                boost_row["error"] = repr(exc)
                print(
                    f"wave={wave_index} restore IO boost failed worker={worker['worker_id']}: {exc}",
                    flush=True,
                )
                write_json(
                    output_dir / "restore_measurement_profile_audit.json",
                    restore_profile_audit + [boost_row],
                )
                return 1
            restore_profile_audit.append(boost_row)
            print(
                f"wave={wave_index} restore IO boost worker={worker['worker_id']} "
                f"read_mb={boost['read_mb']} write_mb={boost['write_mb']}",
                flush=True,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(wave_workers)) as executor:
            wave_restores = list(
                executor.map(
                    lambda worker: restore_worker_database(
                        worker,
                        scenario,
                        args.snapshot_restore_timeout_s,
                    ),
                    wave_workers,
                )
            )
        restore_completed_ts = time.strftime("%Y-%m-%d %H:%M:%S %z")
        for row in wave_restores:
            row["wave_index"] = wave_index
            row["restore_completed_timestamp"] = restore_completed_ts
        restore_runs.extend(wave_restores)

        # Setup under restore boost (IO only when boost enabled): work_mem + VACUUM
        # must finish before formal measurement throttle is applied.
        for worker in wave_workers:
            if str(worker["worker_id"]) not in workers_with_boost:
                continue
            boost = workers_with_boost[str(worker["worker_id"])]
            setup_row: Dict[str, Any] = {
                "worker_id": worker["worker_id"],
                "wave_index": wave_index,
                "scenario": scenario,
                "phase": "setup_under_restore_boost",
                "restore_io_boost_enabled": True,
                "restore_completed_timestamp": restore_completed_ts,
                "setup_profile": {
                    "read_mb": int(boost["read_mb"]),
                    "write_mb": int(boost["write_mb"]),
                    "source": boost.get("source"),
                },
                "profile_switch_timestamp": time.strftime("%Y-%m-%d %H:%M:%S %z"),
            }
            try:
                setup_result = run_worker_setup_sql_under_boost(
                    worker,
                    scenario,
                    timeout_s=max(
                        60.0,
                        float(
                            (effective_worker_config(worker, scenario).get("benchmark") or {}).get(
                                "pre_workload_sql_timeout_s"
                            )
                            or 900.0
                        ),
                    ),
                )
                setup_row.update(setup_result)
                setup_row["status"] = "ok"
                # Prevent validator from re-running the same setup under measurement throttle.
                worker_cfg_path = worker_configs.get(str(worker["worker_id"]))
                if worker_cfg_path is not None:
                    setup_row["validator_pre_workload_clear"] = clear_worker_pre_workload_sql_files(
                        Path(worker_cfg_path)
                    )
            except Exception as exc:
                setup_row["status"] = "failed"
                setup_row["error"] = repr(exc)
                print(
                    f"wave={wave_index} setup-under-boost failed worker={worker['worker_id']}: {exc}",
                    flush=True,
                )
                setup_ordering_audit.append(setup_row)
                write_json(
                    output_dir / "setup_measurement_ordering_audit.json",
                    setup_ordering_audit,
                )
                restore_profile_audit.append(setup_row)
                write_json(output_dir / "restore_measurement_profile_audit.json", restore_profile_audit)
                return 1
            setup_ordering_audit.append(setup_row)
            restore_profile_audit.append(setup_row)
            print(
                f"wave={wave_index} setup-under-boost worker={worker['worker_id']} "
                f"files={len(setup_row.get('sql_files') or [])} "
                f"elapsed={float(setup_row.get('elapsed_s') or 0.0):.1f}s",
                flush=True,
            )

        # Measurement phase (only when boost was used): re-apply formal read/write
        # throttle and verify AFTER setup, BEFORE cache drop / warmup / workload.
        for worker in wave_workers:
            if str(worker["worker_id"]) not in workers_with_boost:
                continue
            vm_res = effective_worker_vm_resource(worker, scenario) or {}
            measure_read = max(1, _to_int(vm_res.get("read_mb"), 100))
            measure_write = max(0, _to_int(vm_res.get("write_mb"), 100))
            measure_row: Dict[str, Any] = {
                "worker_id": worker["worker_id"],
                "wave_index": wave_index,
                "scenario": scenario,
                "phase": "measurement_io_throttle",
                "restore_io_boost_enabled": True,
                "restore_completed_timestamp": restore_completed_ts,
                "setup_completed_before_measurement": True,
                "measurement_profile": {
                    "read_mb": measure_read,
                    "write_mb": measure_write,
                },
                "profile_switch_timestamp": time.strftime("%Y-%m-%d %H:%M:%S %z"),
            }
            try:
                measure_row["live_throttle"] = live_disk_throttle(
                    str(vm_res.get("vm_name") or worker.get("vm_name") or worker["worker_id"]),
                    str(vm_res.get("disk_dev") or "vda"),
                    read_mb=measure_read,
                    write_mb=measure_write,
                    verify=True,
                )
                measure_row["status"] = "ok"
                measure_row["measurement_profile_applied_before_warmup"] = True
                measure_row["measurement_sampling_begins_after_formal_profile"] = True
                measure_row["measurement_profile_applied_timestamp"] = time.strftime(
                    "%Y-%m-%d %H:%M:%S %z"
                )
                measure_row["requested_read_bytes_sec"] = measure_read * 1024 * 1024
                measure_row["requested_write_bytes_sec"] = measure_write * 1024 * 1024
                measure_row["observed_read_bytes_sec"] = measure_row["live_throttle"].get(
                    "observed_read_bytes_sec"
                )
                measure_row["observed_write_bytes_sec"] = measure_row["live_throttle"].get(
                    "observed_write_bytes_sec"
                )
                measure_row["set_command"] = measure_row["live_throttle"].get("set_command")
                measure_row["query_command"] = measure_row["live_throttle"].get("query_command")
            except Exception as exc:
                measure_row["status"] = "failed"
                measure_row["error"] = repr(exc)
                measure_row["measurement_profile_applied_before_warmup"] = False
                print(
                    f"wave={wave_index} measurement IO throttle failed worker={worker['worker_id']}: {exc}",
                    flush=True,
                )
                restore_profile_audit.append(measure_row)
                setup_ordering_audit.append(measure_row)
                write_json(output_dir / "restore_measurement_profile_audit.json", restore_profile_audit)
                write_json(
                    output_dir / "setup_measurement_ordering_audit.json",
                    setup_ordering_audit,
                )
                return 1
            restore_profile_audit.append(measure_row)
            setup_ordering_audit.append(measure_row)
            print(
                f"wave={wave_index} measurement IO throttle worker={worker['worker_id']} "
                f"read_mb={measure_read} write_mb={measure_write} "
                f"measurement_profile_applied_before_warmup=true",
                flush=True,
            )
        if restore_profile_audit:
            write_json(output_dir / "restore_measurement_profile_audit.json", restore_profile_audit)
        if setup_ordering_audit:
            write_json(
                output_dir / "setup_measurement_ordering_audit.json",
                setup_ordering_audit,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(wave_workers)) as executor:
            wave_cache_drops = list(
                executor.map(
                    lambda worker: drop_worker_cache(
                        worker,
                        scenario,
                        args.drop_cache_timeout_s,
                    ),
                    wave_workers,
                )
            )
        for row in wave_cache_drops:
            row["wave_index"] = wave_index
        cache_drop_runs.extend(wave_cache_drops)
        if workers_with_boost:
            print(
                f"wave={wave_index} restore barrier passed; setup-under-boost done; "
                f"measurement throttle applied; starting {len(items)} replays",
                flush=True,
            )
        else:
            print(
                f"wave={wave_index} restore barrier passed; starting {len(items)} replays",
                flush=True,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(items)) as executor:
            future_map = {
                executor.submit(
                    run_worker,
                    item["worker_id"],
                    item["command"],
                    Path(item["log_path"]),
                ): item
                for item in items
            }
            for future in concurrent.futures.as_completed(future_map):
                result = future.result()
                item = future_map[future]
                result.update(
                    {
                        "wave_index": wave_index,
                        "candidate_id": item["candidate_id"],
                    }
                )
                runs.append(result)
                print(
                    f"wave={wave_index} worker={result['worker_id']} "
                    f"candidate={item['candidate_id']} exit={result['returncode']} "
                    f"elapsed={result['elapsed_s']:.1f}s",
                    flush=True,
                )
        for item in items:
            rows = load_jsonl(Path(item["wave_dir"]) / "validation_results.jsonl")
            worker_rows[item["worker_id"]].extend(rows)
        for worker_id, rows in worker_rows.items():
            write_jsonl(output_dir / "workers" / worker_id / "validation_results.jsonl", rows)
        baseline_wave = all(
            str(item.get("candidate_id") or "").startswith("__baseline__")
            for item in items
        )
        if baseline_wave and not reuse_baselines:
            baseline_gate_result = baseline_gate(worker_rows, workers, args)
            write_json(output_dir / "baseline_gate.json", baseline_gate_result)
            if not baseline_gate_result["passed"]:
                print(
                    "baseline gate failed; anchor and candidate waves are not started: "
                    + ",".join(baseline_gate_result["failures"]),
                    flush=True,
                )
                break
            if baseline_only:
                print(
                    "baseline-only mode: baseline_gate done; stopping before anchor/candidates",
                    flush=True,
                )
                break
        if (
            not baseline_only
            and not reuse_anchors
            and all(str(item.get("candidate_id") or "").startswith("__anchor__") for item in items)
        ):
            anchor_gate_result = drift_gate(worker_rows, workers, args)
            write_json(output_dir / "anchor_gate.json", anchor_gate_result)
            if not anchor_gate_result["passed"]:
                print(
                    "anchor gate failed; candidate waves are not started: "
                    + ",".join(anchor_gate_result["failures"]),
                    flush=True,
                )
                break
        if any(
            row["returncode"] != 0 and row["wave_index"] == wave_index
            for row in runs
        ):
            print(f"wave={wave_index} failed; later waves are not started", flush=True)
            break

    if (
        not baseline_only
        and anchor is not None
        and workers
    ):
        first_worker_id = workers[0]["worker_id"]
        first_anchor = next(
            (
                row
                for row in worker_rows[first_worker_id]
                if row.get("candidate_id") == f"__anchor__{first_worker_id}"
            ),
            None,
        )
        if first_anchor is not None and (not reuse_anchors or cached_anchor_is_candidate):
            anchor_result = copy.deepcopy(first_anchor)
            anchor_result.update(
                {
                    key: copy.deepcopy(value)
                    for key, value in anchor.items()
                    if key not in {"params"}
                }
            )
            anchor_result["candidate_id"] = anchor["candidate_id"]
            anchor_result["params"] = copy.deepcopy(anchor["params"])
            worker_rows[first_worker_id].append(anchor_result)

    gate: Optional[Dict[str, Any]] = None
    if not baseline_only:
        gate = drift_gate(worker_rows, workers, args)
        write_json(output_dir / "worker_drift_gate.json", gate)
    control_rows = {
        worker_id: [
            copy.deepcopy(row)
            for row in rows
            if str(row.get("candidate_id") or "").startswith(("__baseline__", "__anchor__"))
        ]
        for worker_id, rows in worker_rows.items()
    }
    has_anchor_rows = any(
        any(str(row.get("candidate_id") or "").startswith("__anchor__") for row in rows)
        for rows in control_rows.values()
    )
    controls_level = "baseline_and_anchor" if has_anchor_rows else "baseline_only"
    # Prefer the measured baseline_gate; if we reused baselines, re-evaluate from rows.
    if baseline_gate_result is None and reuse_baselines:
        baseline_gate_result = baseline_gate(worker_rows, workers, args)
        write_json(output_dir / "baseline_gate.json", baseline_gate_result)
    calibration_cache = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "created_at_epoch": time.time(),
        "scenario": scenario,
        "pool_signature": signature,
        "controls_level": controls_level,
        "baseline_only": baseline_only,
        "worker_drift_gate": gate,
        "baseline_gate": baseline_gate_result,
        "worker_control_rows": control_rows,
    }
    write_json(output_dir / "worker_calibration_cache.json", calibration_cache)
    aggregated: List[Dict[str, Any]] = []
    if not baseline_only and gate is not None:
        aggregated = aggregate_results(candidates, worker_rows, gate)
        write_jsonl(output_dir / "parallel_validation_results.jsonl", aggregated)

    baseline_passed = bool(baseline_gate_result and baseline_gate_result.get("passed"))
    runs_ok = all(row["returncode"] == 0 for row in runs) if runs else False
    if baseline_only:
        status = "completed" if baseline_passed and runs_ok else "failed"
        stop_reason = (
            "baseline_only_complete"
            if status == "completed"
            else (
                "baseline_gate_failed"
                if baseline_gate_result is not None and not baseline_gate_result.get("passed")
                else "baseline_only_failed"
            )
        )
    else:
        status = (
            "completed"
            if gate is not None and gate.get("passed") and runs_ok and len(aggregated) == len(candidates)
            else "failed"
        )
        stop_reason = (
            "baseline_gate_failed"
            if baseline_gate_result is not None and not baseline_gate_result.get("passed")
            else (
                "anchor_gate_failed"
                if anchor_gate_result is not None and not anchor_gate_result.get("passed")
                else ""
            )
        )
    diagnostic_local_pair = bool(getattr(args, "diagnostic_local_pair", False))
    summary = {
        "status": status,
        "stop_reason": stop_reason,
        "scenario": scenario,
        "baseline_only": baseline_only,
        "diagnostic_local_pair": diagnostic_local_pair,
        # Diagnostic family pairs are causal-only and never Stage A evidence.
        "eligible_for_stagea": False if diagnostic_local_pair else None,
        "worker_count": len(workers),
        "candidate_count": len(candidates),
        "aggregated_result_count": len(aggregated),
        "worker_runs": sorted(runs, key=lambda row: (row["wave_index"], row["worker_id"])),
        "profile_preflight_runs": sorted(profile_runs, key=lambda row: row["worker_id"]),
        "snapshot_restore_runs": restore_runs,
        "cache_drop_runs": cache_drop_runs,
        "worker_drift_gate": gate,
        "baseline_gate": baseline_gate_result,
        "anchor_gate": anchor_gate_result,
        "calibration_cache_reused": cache_reused,
        "reuse_baselines": reuse_baselines,
        "reuse_anchors": reuse_anchors,
        "cached_anchor_is_candidate": cached_anchor_is_candidate,
        "calibration_cache_json": str(output_dir / "worker_calibration_cache.json"),
        "results_jsonl": str(output_dir / "parallel_validation_results.jsonl") if not baseline_only else "",
        "stops_before_anchor_and_candidates": baseline_only,
    }
    write_json(output_dir / "parallel_replay_summary.json", summary)
    if baseline_only:
        print(
            f"parallel replay baseline-only status={summary['status']} "
            f"baseline_gate_passed={baseline_passed}",
            flush=True,
        )
        return 0 if summary["status"] == "completed" else 2
    print(
        f"parallel replay status={summary['status']} results={len(aggregated)}/{len(candidates)}",
        flush=True,
    )
    return 0 if summary["status"] == "completed" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidates-json",
        default="",
        help="Candidate manifest. Optional when --baseline-only.",
    )
    parser.add_argument("--worker-pool-json", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--scenario", required=True, choices=["cpu", "memory", "mem", "io", "none"])
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=None,
        help="Replay only the first N ranked candidates from the manifest.",
    )
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help=(
            "Run profile preflight, immutable snapshot restore, cache drop, one "
            "default-parameter baseline per worker, baseline_gate, then stop. "
            "Does not run anchor or candidate waves."
        ),
    )
    parser.add_argument("--anchor-candidate-id", default="")
    parser.add_argument("--run-mins", type=float, default=None)
    parser.add_argument("--workload-timeout-s", type=float, default=900.0)
    parser.add_argument("--apply-vm-profile", action="store_true")
    parser.add_argument("--force-vm-destroy", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--calibration-cache-json", default="")
    parser.add_argument("--calibration-cache-max-age-s", type=float, default=3600.0)
    parser.add_argument("--snapshot-restore-timeout-s", type=float, default=600.0)
    parser.add_argument("--drop-cache-timeout-s", type=float, default=120.0)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--max-baseline-qps-drift-pct", type=float, default=5.0)
    parser.add_argument("--max-baseline-resource-drift-pp", type=float, default=3.0)
    parser.add_argument("--max-anchor-qps-delta-spread-pp", type=float, default=5.0)
    parser.add_argument("--max-anchor-resource-delta-spread-pp", type=float, default=3.0)
    parser.add_argument(
        "--allow-goal-rich-anchor-qps-spread",
        action="store_true",
        help=(
            "Allow anchor QPS-delta spread only when every worker independently meets the "
            "15%% QPS, 5pp target-drop, target-exit, and any-non-target-up goal."
        ),
    )
    parser.add_argument(
        "--diagnostic-local-pair",
        action="store_true",
        help=(
            "Causal family-pair diagnostic mode: keep snapshot/cache/sample/source/IO quality "
            "gates, but skip single-bottleneck geometry so single-AP families below final "
            "Memory 80%% can still run default/teacher on the same worker. Not for Stage A freeze."
        ),
    )
    args = parser.parse_args()
    if not args.baseline_only and not args.candidates_json:
        parser.error("--candidates-json is required unless --baseline-only")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
