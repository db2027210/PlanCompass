"""Preflight and clone verified owned MySQL bases into a dynamic worker pool."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import subprocess
from typing import Any, Sequence


def run(argv: Sequence[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), text=True, capture_output=True, timeout=timeout, check=False)


def _inspect(name: str) -> dict[str, Any] | None:
    result = run(["docker", "inspect", name], 30)
    if result.returncode:
        return None
    return json.loads(result.stdout)[0]


def _port_free(port: int) -> bool:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", port)); return True
    except OSError:
        return False
    finally:
        sock.close()


def _assert_source_idle(cfg: dict[str, Any], source: dict[str, Any]) -> None:
    """Refuse to stop an owned source while a benchmark/query is still active."""
    item = _inspect(source["container"])
    if item is None:
        raise RuntimeError(f"cannot inspect source before stop: {source['container']}")
    if not item.get("State", {}).get("Running"):
        return
    bindings = item.get("HostConfig", {}).get("PortBindings", {}).get("3306/tcp", []) or []
    ports = [int(row["HostPort"]) for row in bindings if row.get("HostPort")]
    if len(ports) != 1:
        raise RuntimeError(f"source needs exactly one host port: {source['container']}")
    env = dict(os.environ); env["MYSQL_PWD"] = str(cfg["root_password"])
    query = subprocess.run(
        ["mysql", "--connect-timeout=5", "--batch", "--skip-column-names",
         "-h127.0.0.1", "-P", str(ports[0]), "-uroot", "-e",
         "SELECT COUNT(*) FROM information_schema.PROCESSLIST "
         "WHERE COMMAND NOT IN ('Sleep','Daemon') AND ID <> CONNECTION_ID()"],
        text=True, capture_output=True, timeout=30, check=False, env=env,
    )
    if query.returncode:
        raise RuntimeError(f"cannot verify source idle: {source['container']}: {query.stderr.strip()}")
    active = int(query.stdout.strip() or "0")
    if active:
        raise RuntimeError(f"refusing to stop busy source {source['container']}: {active} active sessions")


def preflight(cfg: dict[str, Any]) -> dict[str, Any]:
    errors = []
    inventory_result = run(["docker", "ps", "-a", "--format", "{{.Names}}"], 120)
    if inventory_result.returncode:
        return {"ok": False, "errors": [inventory_result.stderr.strip() or "docker inventory failed"],
                "sources": {}, "workers": []}
    existing_names = {line.strip() for line in inventory_result.stdout.splitlines() if line.strip()}
    source_rows = {}
    for family, source in cfg["sources"].items():
        item = _inspect(source["container"])
        labels = (item or {}).get("Config", {}).get("Labels", {}) or {}
        if item is None or labels.get(cfg["label_key"]) != cfg["label_value"]:
            errors.append(f"source is absent or not owned: {source['container']}")
        if not Path(source["datadir"]).is_dir():
            errors.append(f"source datadir missing: {source['datadir']}")
        source_rows[family] = {"container": source["container"],
                               "state": (item or {}).get("State", {}).get("Status")}
    seen_names, seen_ports, seen_dirs = set(), set(), set()
    worker_rows = []
    for worker in cfg["workers"]:
        name, port, datadir = worker["name"], int(worker["port"]), Path(worker["datadir"])
        if worker["family"] not in cfg["sources"]:
            errors.append(f"unknown family: {worker['family']}")
        if name in seen_names or port in seen_ports or datadir in seen_dirs:
            errors.append("duplicate worker name, port, or datadir")
        if name in existing_names:
            errors.append(f"worker container already exists: {name}")
        if not _port_free(port): errors.append(f"port occupied: {port}")
        if datadir.exists(): errors.append(f"target datadir exists: {datadir}")
        seen_names.add(name); seen_ports.add(port); seen_dirs.add(datadir)
        worker_rows.append({"worker_id":worker["worker_id"],"name":name,"port":port,
                            "datadir":str(datadir), "container_exists": name in existing_names})
    return {"ok": not errors, "errors": errors, "sources": source_rows, "workers": worker_rows}


def preflight_existing(cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate already-copied datadirs without modifying or recopying them."""
    audit = preflight(cfg)
    errors = [error for error in audit["errors"] if not error.startswith("target datadir exists:")]
    for worker in cfg["workers"]:
        datadir = Path(worker["datadir"])
        if not datadir.is_dir():
            errors.append(f"existing target datadir missing: {datadir}")
        row = next(item for item in audit["workers"] if item["name"] == worker["name"])
        item = _inspect(worker["name"]) if row["container_exists"] else None
        if item is not None:
            labels = item.get("Config", {}).get("Labels", {}) or {}
            mounts = {mount.get("Destination"): mount.get("Source") for mount in item.get("Mounts", [])}
            bindings = item.get("HostConfig", {}).get("PortBindings", {}).get("3306/tcp", []) or []
            ports = {int(binding["HostPort"]) for binding in bindings if binding.get("HostPort")}
            if (labels.get(cfg["label_key"]) == cfg["worker_label_value"]
                    and mounts.get("/var/lib/mysql") == str(datadir)
                    and int(worker["port"]) in ports):
                for allowed in (
                    f"worker container already exists: {worker['name']}",
                    f"port occupied: {worker['port']}",
                ):
                    errors = [error for error in errors if error != allowed]
            else:
                errors.append(f"existing worker does not exactly match owned config: {worker['name']}")
    return {**audit, "ok": not errors, "errors": errors, "mode": "start_existing_only"}


def start_workers(cfg: dict[str, Any], max_parallel: int) -> None:
    inventory = run(["docker", "ps", "-a", "--format", "{{.Names}}"], 120)
    if inventory.returncode:
        raise RuntimeError(inventory.stderr.strip() or "docker inventory failed")
    existing_names = {line.strip() for line in inventory.stdout.splitlines() if line.strip()}

    def start(worker: dict[str, Any]) -> None:
        existing = _inspect(worker["name"]) if worker["name"] in existing_names else None
        if existing is not None:
            labels = existing.get("Config", {}).get("Labels", {}) or {}
            if labels.get(cfg["label_key"]) != cfg["worker_label_value"]:
                raise RuntimeError(f"refusing non-owned existing worker: {worker['name']}")
            if existing.get("State", {}).get("Running"):
                return
            resumed = run(["docker", "start", worker["name"]], 300)
            if resumed.returncode: raise RuntimeError(resumed.stderr.strip())
            return
        argv = ["docker","run","-d","--name",worker["name"],"--label",
                f"{cfg['label_key']}={cfg['worker_label_value']}","--cpuset-cpus",worker["cpuset"],
                "--cpus",str(worker["cpus"]),"--memory",worker["memory"],"--pids-limit","1024",
                "--blkio-weight","500","--publish",f"127.0.0.1:{worker['port']}:3306",
                "--mount",f"type=bind,src={worker['datadir']},dst=/var/lib/mysql",
                "--env",f"MYSQL_ROOT_PASSWORD={cfg['root_password']}"]
        if worker.get("device") and worker.get("disk_bw_mb"):
            rate = f"{float(worker['disk_bw_mb']):g}mb"
            argv += ["--device-read-bps", f"{worker['device']}:{rate}",
                     "--device-write-bps", f"{worker['device']}:{rate}"]
        argv += [cfg["image"],
                "--local-infile=ON","--innodb-buffer-pool-size=6500M",
                "--innodb-flush-log-at-trx-commit=2","--sync-binlog=0","--max-connections=512"]
        started = run(argv, 300)
        if started.returncode: raise RuntimeError(started.stderr.strip())
    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        list(pool.map(start, cfg["workers"]))


def execute(cfg: dict[str, Any], max_parallel: int = 4, *, preflight_checked: bool = False) -> None:
    if not preflight_checked:
        audit = preflight(cfg)
        if not audit["ok"]: raise RuntimeError(json.dumps(audit, indent=2))
    for source in cfg["sources"].values():
        _assert_source_idle(cfg, source)
        stopped = run(["docker", "stop", "--time", "120", source["container"]], 180)
        if stopped.returncode: raise RuntimeError(stopped.stderr.strip())
    def clone(worker: dict[str, Any]) -> None:
        source = cfg["sources"][worker["family"]]["datadir"]
        target = Path(worker["datadir"])
        target.parent.mkdir(parents=True, exist_ok=True)
        helper = f"ra-mysql-grid-copy-{worker['worker_id']}"
        # Run cp as root inside a short-lived, owned helper container.  This
        # avoids depending on host sudo/PAM state and keeps source read-only.
        copied = run([
            "docker", "run", "--rm", "--name", helper, "--label",
            f"{cfg['label_key']}={cfg['worker_label_value']}",
            "--mount", f"type=bind,src={source},dst=/source,readonly",
            "--mount", f"type=bind,src={target.parent},dst=/target_parent",
            "--entrypoint", "/bin/cp", cfg["image"], "-a", "--reflink=auto",
            "/source", f"/target_parent/{target.name}",
        ], 3600)
        if copied.returncode: raise RuntimeError(copied.stderr.strip())
    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        list(pool.map(clone, cfg["workers"]))
    start_workers(cfg, max_parallel)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/mysql_dynamic_worker_pool.json"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--start-existing", action="store_true",
                        help="start verified existing datadirs; never copy or stop sources")
    parser.add_argument("--max-parallel", type=int, default=4)
    parser.add_argument("--families", default="", help="optional comma-separated source families")
    parser.add_argument("--worker-ids", default="",
                        help="optional comma-separated worker ids; limits every operation to those workers")
    args = parser.parse_args(); cfg=json.loads(args.config.read_text())
    selected = {item.strip() for item in args.families.split(",") if item.strip()}
    if selected:
        unknown = selected - set(cfg["sources"])
        if unknown: raise SystemExit(f"unknown families: {sorted(unknown)}")
        cfg = {**cfg,
               "sources": {name: row for name, row in cfg["sources"].items() if name in selected},
               "workers": [row for row in cfg["workers"] if row["family"] in selected]}
    selected_workers = {item.strip() for item in args.worker_ids.split(",") if item.strip()}
    if selected_workers:
        known_workers = {row["worker_id"] for row in cfg["workers"]}
        unknown_workers = selected_workers - known_workers
        if unknown_workers: raise SystemExit(f"unknown worker ids: {sorted(unknown_workers)}")
        workers = [row for row in cfg["workers"] if row["worker_id"] in selected_workers]
        required_families = {row["family"] for row in workers}
        cfg = {**cfg,
               "sources": {name: row for name, row in cfg["sources"].items()
                           if name in required_families},
               "workers": workers}
    if args.execute and args.start_existing:
        raise SystemExit("choose either --execute or --start-existing")
    audit = preflight_existing(cfg) if args.start_existing else preflight(cfg)
    print(json.dumps(audit, indent=2))
    if args.start_existing:
        if not audit["ok"]: return 2
        start_workers(cfg, args.max_parallel)
    if args.execute:
        if not audit["ok"]: return 2
        execute(cfg, args.max_parallel, preflight_checked=True)
    return 0 if audit["ok"] else 2


if __name__ == "__main__": raise SystemExit(main())
