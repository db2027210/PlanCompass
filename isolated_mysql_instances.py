"""Plan and (only with explicit flags) manage isolated MySQL Docker instances.

The safety boundary is a Docker label, not a name prefix.  Mutating an existing
container is refused unless both the configured ownership label and value match.
The default CLI only prints a preflight/command plan and changes no state.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import socket
import subprocess
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config" / "mysql_instance_pool.json"
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), text=True, capture_output=True, check=False)


def load_config(path: Path | str = DEFAULT_CONFIG) -> dict[str, Any]:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"label_key", "label_value", "name_prefix", "target_root", "instances"}
    if not isinstance(cfg, dict) or required - set(cfg):
        raise ValueError(f"config is missing {sorted(required - set(cfg or {}))}")
    if not str(cfg["label_key"]).startswith("resource-adaptive-mysql."):
        raise ValueError("label_key must use the resource-adaptive-mysql namespace")
    prefix = str(cfg["name_prefix"])
    if not prefix or prefix in {"mysql", "mysqld"}:
        raise ValueError("name_prefix must be dedicated and non-generic")
    target_root = Path(cfg["target_root"])
    if not target_root.is_absolute() or target_root not in {Path("/disk1"), Path("/disk2"), Path("/disk3")}:
        raise ValueError("target_root must be an explicitly configured data disk")
    if not isinstance(cfg["instances"], list) or not cfg["instances"]:
        raise ValueError("instances must be a non-empty list")
    names, ports, datadirs = set(), set(), set()
    for row in cfg["instances"]:
        missing = {"id", "port", "cpuset", "memory", "blkio_weight", "pids_limit", "datadir"} - set(row)
        if missing:
            raise ValueError(f"instance missing {sorted(missing)}")
        name = prefix + str(row["id"])
        datadir = Path(row["datadir"])
        if not datadir.is_absolute() or target_root not in datadir.parents:
            raise ValueError(f"{name}: datadir must be a direct or nested /disk1 path")
        if name in names or int(row["port"]) in ports or datadir in datadirs:
            raise ValueError("instance names, ports, and datadirs must be unique")
        if not 1 <= int(row["blkio_weight"]) <= 1000 or int(row["pids_limit"]) < 1:
            raise ValueError(f"{name}: invalid blkio_weight or pids_limit")
        names.add(name); ports.add(int(row["port"])); datadirs.add(datadir)
    return cfg


def _docker_inventory(runner: Runner) -> tuple[list[dict[str, Any]], list[str]]:
    result = runner(["docker", "ps", "-a", "--format", "{{json .}}"])
    if result.returncode:
        return [], [f"docker inventory failed: {result.stderr.strip()}"]
    rows, errors = [], []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            errors.append("docker inventory returned malformed JSON")
    return rows, errors


def _port_is_free(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def preflight(cfg: Mapping[str, Any], runner: Runner = _run,
              port_probe: Callable[[int], bool] = _port_is_free) -> dict[str, Any]:
    """Read-only collision and external-activity audit."""
    inventory, errors = _docker_inventory(runner)
    by_name = {str(row.get("Names", "")): row for row in inventory}
    desired_names = {str(cfg["name_prefix"]) + str(row["id"]) for row in cfg["instances"]}
    external_active = []
    owned_active = []
    label_token = f"{cfg['label_key']}={cfg['label_value']}"
    for row in inventory:
        running = str(row.get("State", "")).lower() == "running" or str(row.get("Status", "")).lower().startswith("up")
        if not running:
            continue
        labels = str(row.get("Labels", ""))
        name = str(row.get("Names", ""))
        if label_token in labels:
            owned_active.append(name)
        elif any(token in (name + " " + labels).lower() for token in cfg.get("external_experiment_markers", [])):
            external_active.append(name)
    checks = []
    for row in cfg["instances"]:
        name = str(cfg["name_prefix"]) + str(row["id"])
        existing = by_name.get(name)
        labels = str(existing.get("Labels", "")) if existing else ""
        owned = existing is not None and label_token in labels
        if existing is not None and not owned:
            errors.append(f"name collision with non-owned container: {name}")
        datadir = Path(row["datadir"])
        if datadir.exists() and not owned:
            errors.append(f"datadir already exists and is not tied to an owned container: {datadir}")
        port_free = port_probe(int(row["port"]))
        if not port_free and not owned:
            errors.append(f"port is occupied: {row['port']}")
        checks.append({"name": name, "owned_existing": owned, "port_free": port_free,
                       "datadir_exists": datadir.exists()})
    if external_active and bool(cfg.get("block_on_external_experiments", True)):
        errors.append("active external experiments detected: " + ", ".join(sorted(external_active)))
    warnings = []
    if external_active:
        warnings.append("active external experiments detected; no external resource will be modified: " + ", ".join(sorted(external_active)))
    return {"ok": not errors, "errors": errors, "warnings": warnings, "instances": checks,
            "owned_active": sorted(owned_active), "external_active": sorted(external_active),
            "inventory_count": len(inventory), "desired_names": sorted(desired_names)}


def build_plan(cfg: Mapping[str, Any], source_datadir: Path | None = None) -> dict[str, Any]:
    commands, clones = [], []
    for row in cfg["instances"]:
        name = str(cfg["name_prefix"]) + str(row["id"])
        datadir = Path(row["datadir"])
        if source_datadir is not None:
            clones.append({"source": str(source_datadir), "target": str(datadir),
                           "source_mode": "read_only", "requires": "--execute --allow-clone"})
        commands.append([
            "docker", "run", "-d", "--name", name,
            "--label", f"{cfg['label_key']}={cfg['label_value']}",
            "--cpuset-cpus", str(row["cpuset"]), "--memory", str(row["memory"]),
            "--blkio-weight", str(row["blkio_weight"]), "--pids-limit", str(row["pids_limit"]),
            "--publish", f"127.0.0.1:{row['port']}:3306",
            "--mount", f"type=bind,src={datadir},dst=/var/lib/mysql",
            str(cfg.get("image", "mysql:8.0")),
        ])
    return {"mode": "dry_run", "ownership_label": f"{cfg['label_key']}={cfg['label_value']}",
            "clone_operations": clones, "container_commands": commands}


def _validate_source(source: Path, cfg: Mapping[str, Any]) -> Path:
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("source datadir must be an existing directory")
    target_root = Path(cfg["target_root"]).resolve(strict=False)
    if source == target_root or target_root in source.parents:
        raise ValueError("source datadir must be outside the target root")
    return source


def execute_plan(cfg: Mapping[str, Any], plan: Mapping[str, Any], *, allow_clone: bool,
                 source_datadir: Path | None, runner: Runner = _run) -> None:
    """Execute a preflighted plan. Callers must explicitly opt in at the CLI."""
    if source_datadir is not None and not allow_clone:
        raise RuntimeError("cloning requires --allow-clone in addition to --execute")
    if source_datadir is not None:
        source = _validate_source(source_datadir, cfg)
        for clone in plan["clone_operations"]:
            target = Path(clone["target"])
            if target.exists():
                raise RuntimeError(f"refusing to overwrite clone target: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            result = runner(["sudo", "-n", "cp", "-a", "--reflink=auto", str(source), str(target)])
            if result.returncode:
                raise RuntimeError(f"clone failed for {target}: {result.stderr.strip()}")
    else:
        for row in cfg["instances"]:
            Path(row["datadir"]).mkdir(parents=True, exist_ok=False)
    for command in plan["container_commands"]:
        result = runner(command)
        if result.returncode:
            raise RuntimeError(f"container start failed: {result.stderr.strip()}")


def remove_owned(name: str, cfg: Mapping[str, Any], runner: Runner = _run) -> None:
    """Remove one container only after an exact ownership-label inspection."""
    if not name.startswith(str(cfg["name_prefix"])):
        raise RuntimeError("refusing name outside the dedicated prefix")
    result = runner(["docker", "inspect", "--format", "{{json .Config.Labels}}", name])
    if result.returncode:
        raise RuntimeError("cannot inspect container ownership")
    labels = json.loads(result.stdout)
    if labels.get(str(cfg["label_key"])) != str(cfg["label_value"]):
        raise RuntimeError("refusing to remove a container not owned by this tool")
    removed = runner(["docker", "rm", "-f", name])
    if removed.returncode:
        raise RuntimeError(removed.stderr.strip())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--source-datadir", type=Path)
    parser.add_argument("--execute", action="store_true", help="explicitly permit state changes")
    parser.add_argument("--allow-clone", action="store_true", help="permit source datadir cloning; also requires --execute")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    source = _validate_source(args.source_datadir, cfg) if args.source_datadir else None
    audit = preflight(cfg)
    plan = build_plan(cfg, source)
    output = {"preflight": audit, "plan": plan}
    print(json.dumps(output, indent=2))
    if not args.execute:
        return 0 if audit["ok"] else 2
    if not audit["ok"]:
        raise SystemExit("preflight failed; no changes made")
    execute_plan(cfg, plan, allow_clone=args.allow_clone, source_datadir=source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
