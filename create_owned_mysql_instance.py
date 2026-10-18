"""Create one explicitly owned MySQL container after strict local preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
from typing import Any, Sequence


OWNER_KEY = "resource-adaptive-mysql.owner"
NAME_PREFIX = "ra-mysql-grid-"


def _run(argv: Sequence[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), text=True, capture_output=True, timeout=timeout, check=False)


def _inspect(name: str) -> dict[str, Any] | None:
    result = _run(["docker", "inspect", name], 120)
    if result.returncode:
        return None
    value = json.loads(result.stdout)
    return value[0] if value else None


def _container_exists(name: str) -> bool:
    result = _run([
        "docker", "ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}",
    ], 30)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "docker inventory failed")
    return name in {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _port_free(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def preflight(*, name: str, port: int, datadir: Path, owner: str) -> dict[str, Any]:
    errors: list[str] = []
    if not name.startswith(NAME_PREFIX):
        errors.append(f"name must start with {NAME_PREFIX}")
    if not owner.strip():
        errors.append("owner must be non-empty")
    if _container_exists(name):
        errors.append("container name already exists")
    if not _port_free(port):
        errors.append("host port is occupied")
    resolved = datadir.resolve(strict=False)
    if resolved != datadir.absolute():
        errors.append("datadir must not contain symlink aliases")
    if not datadir.is_dir():
        errors.append("datadir must be an existing directory")
    elif any(datadir.iterdir()):
        errors.append("datadir must be empty")
    return {"ok": not errors, "errors": errors, "name": name, "port": port,
            "datadir": str(resolved), "label": f"{OWNER_KEY}={owner}"}


def create(args: argparse.Namespace) -> dict[str, Any]:
    datadir = args.datadir.resolve(strict=True)
    argv = [
        "docker", "run", "-d", "--name", args.name,
        "--label", f"{OWNER_KEY}={args.owner}",
        "--cpuset-cpus", args.cpuset, "--cpus", str(args.cpus),
        "--memory", args.memory, "--pids-limit", "1024", "--blkio-weight", "500",
        "--publish", f"127.0.0.1:{args.port}:3306",
        "--mount", f"type=bind,src={datadir},dst=/var/lib/mysql",
        "--env", f"MYSQL_ROOT_PASSWORD={args.root_password}", args.image,
        "--local-infile=ON", f"--innodb-buffer-pool-size={args.buffer_pool}",
        "--innodb-flush-log-at-trx-commit=2", "--sync-binlog=0", "--max-connections=512",
    ]
    if args.innodb_flush_method:
        argv.append(f"--innodb-flush-method={args.innodb_flush_method}")
    result = _run(argv, 300)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "docker run failed")
    item = _inspect(args.name)
    labels = (item or {}).get("Config", {}).get("Labels", {}) or {}
    state = (item or {}).get("State", {})
    bindings = (item or {}).get("HostConfig", {}).get("PortBindings", {}).get("3306/tcp", []) or []
    ports = {int(row["HostPort"]) for row in bindings if row.get("HostPort")}
    if labels.get(OWNER_KEY) != args.owner or args.port not in ports or not state.get("Running"):
        raise RuntimeError("created container failed ownership/port/running verification")
    return {"name": args.name, "container_id": result.stdout.strip(),
            "pid": int(state.get("Pid", 0)), "port": args.port,
            "label": f"{OWNER_KEY}={args.owner}"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--datadir", type=Path, required=True)
    parser.add_argument("--cpuset", required=True)
    parser.add_argument("--cpus", type=float, required=True)
    parser.add_argument("--memory", required=True)
    parser.add_argument("--buffer-pool", default="8G")
    parser.add_argument(
        "--innodb-flush-method", choices=("O_DIRECT", "fsync"), default=None,
        help="optional startup-only InnoDB flush method; use O_DIRECT for IO experiments",
    )
    parser.add_argument("--image", default="mysql:8.0")
    parser.add_argument("--root-password", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    audit = preflight(name=args.name, port=args.port, datadir=args.datadir, owner=args.owner)
    print(json.dumps({"preflight": audit}, indent=2))
    if not args.execute:
        return 0 if audit["ok"] else 2
    expected = f"CREATE {args.name} {args.port}"
    if args.confirm != expected:
        raise SystemExit(f"confirmation must be exactly: {expected}")
    if not audit["ok"]:
        return 2
    print(json.dumps(create(args), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
