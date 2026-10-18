#!/usr/bin/env python3
"""Reset Bonect replay workers to clean qcow2 backing volumes between waves."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Sequence
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
DOMAIN_PREFIX = "bonect-replay-v87-"


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def run_command(args: Sequence[str], *, timeout_s: float = 120.0) -> str:
    proc = subprocess.run(
        list(args),
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        timeout=max(1.0, timeout_s),
    )
    if proc.returncode != 0:
        detail = ((proc.stdout or "") + (proc.stderr or "")).strip()
        raise RuntimeError(f"command failed exit={proc.returncode}: {' '.join(args)}\n{detail}")
    return (proc.stdout or "").strip()


def virsh(*args: str, timeout_s: float = 120.0) -> str:
    return run_command(["virsh", "-c", "qemu:///system", *args], timeout_s=timeout_s)


def selected_workers(pool: Dict[str, Any], scenario: str, max_workers: int) -> List[Dict[str, Any]]:
    workers: List[Dict[str, Any]] = []
    for raw in pool.get("workers") or []:
        if not isinstance(raw, dict) or not raw.get("enabled", True):
            continue
        scenarios = {"memory" if value == "mem" else str(value) for value in raw.get("scenarios") or []}
        if scenarios and scenario not in scenarios:
            continue
        worker = dict(raw)
        domain = str(worker.get("vm_name") or "")
        if not domain.startswith(DOMAIN_PREFIX):
            raise ValueError(f"wave reset refuses non-Bonect domain: {domain!r}")
        clean_volume = str(worker.get("clean_volume") or "")
        live_volume = str(worker.get("live_volume") or "")
        pool_name = str(worker.get("storage_pool") or "")
        if not clean_volume or not live_volume or not pool_name:
            raise ValueError(f"worker={worker.get('worker_id')} lacks clean/live volume metadata")
        if clean_volume == live_volume or not live_volume.endswith("-live.qcow2"):
            raise ValueError(f"unsafe live volume for worker={worker.get('worker_id')}: {live_volume!r}")
        workers.append(worker)
        if len(workers) >= max_workers:
            break
    if not workers:
        raise ValueError(f"no wave-reset workers for scenario={scenario}")
    return workers


def domain_state(domain: str) -> str:
    return virsh("domstate", domain).strip().lower()


def stop_domain(domain: str, timeout_s: float) -> None:
    state = domain_state(domain)
    if state == "shut off":
        return
    if not state.startswith(("running", "paused", "in shutdown")):
        raise RuntimeError(f"refusing reset for domain={domain} state={state!r}")
    if not state.startswith("in shutdown"):
        virsh("shutdown", domain)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if domain_state(domain) == "shut off":
            return
        time.sleep(2.0)
    raise TimeoutError(f"domain={domain} did not shut down; destructive stop is disabled")


def volume_path(pool_name: str, volume_name: str) -> str:
    return virsh("vol-path", volume_name, "--pool", pool_name)


def volume_exists(pool_name: str, volume_name: str) -> bool:
    proc = subprocess.run(
        ["virsh", "-c", "qemu:///system", "vol-info", volume_name, "--pool", pool_name],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def recreate_live_volume(worker: Dict[str, Any]) -> str:
    pool_name = str(worker["storage_pool"])
    clean_volume = str(worker["clean_volume"])
    live_volume = str(worker["live_volume"])
    if volume_exists(pool_name, live_volume):
        virsh("vol-delete", live_volume, "--pool", pool_name, timeout_s=300.0)
    capacity = str(worker.get("volume_capacity") or "350G")
    virsh(
        "vol-create-as",
        pool_name,
        live_volume,
        capacity,
        "--format",
        "qcow2",
        "--backing-vol",
        clean_volume,
        "--backing-vol-format",
        "qcow2",
        timeout_s=300.0,
    )
    return volume_path(pool_name, live_volume)


def rewrite_domain_disk_source(xml_text: str, live_path: str) -> str:
    root = ET.fromstring(xml_text)
    matches = []
    for disk in root.findall("./devices/disk"):
        target = disk.find("target")
        if target is not None and target.get("dev") == "vda":
            matches.append(disk)
    if len(matches) != 1:
        raise ValueError(f"expected one vda disk, found {len(matches)}")
    source = matches[0].find("source")
    if source is None or source.get("file") is None:
        raise ValueError("vda disk does not have a file source")
    source.set("file", live_path)
    return ET.tostring(root, encoding="unicode")


def point_domain_at_live(domain: str, live_path: str) -> None:
    xml_text = virsh("dumpxml", domain, "--inactive")
    updated = rewrite_domain_disk_source(xml_text, live_path)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".xml", delete=False) as handle:
        handle.write(updated)
        xml_path = Path(handle.name)
    try:
        virsh("define", str(xml_path))
    finally:
        xml_path.unlink(missing_ok=True)


def start_domain(domain: str) -> None:
    virsh("start", domain, timeout_s=300.0)


def start_postgres(worker: Dict[str, Any], timeout_s: float) -> None:
    metrics = worker.get("config_override", {}).get("metrics", {}).get("vm_runtime", {})
    target = str(metrics.get("ssh_target") or "")
    if not target:
        host = str(worker.get("config_override", {}).get("postgres", {}).get("host") or "")
        target = f"anonymous@{host}"
    command = str(
        worker.get("postgres_start_command")
        or (
            "sudo -n -u postgres /usr/lib/postgresql/17/bin/pg_ctl "
            "-D /home/anonymous/pg_master/pg_data -w -t 600 start"
        )
    )
    # Detach stdio so PostgreSQL's logging collector cannot keep SSH open.
    remote = f"({command}) </dev/null >/tmp/bonect_pg_start.log 2>&1 &"
    run_command(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            target,
            remote,
        ],
        timeout_s=timeout_s,
    )


def wait_postgres(worker: Dict[str, Any], timeout_s: float) -> None:
    pg = worker.get("config_override", {}).get("postgres", {})
    host = str(pg.get("host") or "")
    port = int(pg.get("port") or 5432)
    user = str(pg.get("user") or "postgres")
    password = str(pg.get("password") or "CHANGE_ME")
    deadline = time.time() + timeout_s
    uri = f"postgresql://{user}:{password}@{host}:{port}/postgres"
    while time.time() < deadline:
        proc = subprocess.run(
            ["psql", uri, "-X", "-Atc", "SELECT 1;"],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        if proc.returncode == 0 and (proc.stdout or "").strip() == "1":
            return
        time.sleep(2.0)
    raise TimeoutError(f"PostgreSQL not ready for worker={worker.get('worker_id')} host={host}:{port}")


def reset_one(worker: Dict[str, Any], shutdown_timeout_s: float) -> Dict[str, Any]:
    started = time.time()
    domain = str(worker["vm_name"])
    stop_domain(domain, shutdown_timeout_s)
    live_path = recreate_live_volume(worker)
    point_domain_at_live(domain, live_path)
    return {
        "worker_id": worker.get("worker_id"),
        "domain": domain,
        "live_path": live_path,
        "prepared_elapsed_s": time.time() - started,
    }


def run(args: argparse.Namespace) -> int:
    started = time.time()
    pool_path = Path(args.worker_pool_json)
    if not pool_path.is_absolute():
        pool_path = ROOT / pool_path
    workers = selected_workers(read_json(pool_path), args.scenario, args.max_workers)
    plan = {
        "scenario": args.scenario,
        "worker_count": len(workers),
        "workers": [
            {
                "worker_id": row.get("worker_id"),
                "vm_name": row.get("vm_name"),
                "storage_pool": row.get("storage_pool"),
                "clean_volume": row.get("clean_volume"),
                "live_volume": row.get("live_volume"),
            }
            for row in workers
        ],
    }
    if args.plan_only:
        print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
        return 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers)) as executor:
        prepared = list(executor.map(lambda row: reset_one(row, args.shutdown_timeout_s), workers))
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers)) as executor:
        list(executor.map(lambda row: start_domain(str(row["vm_name"])), workers))
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers)) as executor:
        list(executor.map(lambda row: start_postgres(row, args.pg_ready_timeout_s), workers))
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers)) as executor:
        list(executor.map(lambda row: wait_postgres(row, args.pg_ready_timeout_s), workers))
    result = {
        **plan,
        "status": "ok",
        "prepared": prepared,
        "prepare_elapsed_s": max((row["prepared_elapsed_s"] for row in prepared), default=0.0),
        "elapsed_s": time.time() - started,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-pool-json", required=True)
    parser.add_argument("--scenario", choices=["cpu", "memory", "none"], required=True)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--shutdown-timeout-s", type=float, default=180.0)
    parser.add_argument("--pg-ready-timeout-s", type=float, default=180.0)
    parser.add_argument("--plan-only", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
