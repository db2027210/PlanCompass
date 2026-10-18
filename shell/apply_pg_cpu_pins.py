#!/usr/bin/env python3
"""Apply exclusive CPU pins for PostgreSQL matrix VMs and Docker workers.

Reads config/pg_cpu_pin_map_20260720.json (or --map), then:
  1. taskset live QEMU processes matched by -name
  2. docker update --cpuset-cpus for listed postgres workers
  3. prints a verification table

Root-owned QEMU may require passwordless sudo -n taskset.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAP = ROOT / "config" / "pg_cpu_pin_map_20260720.json"


def expand_cpuset(value: str) -> Set[int]:
    result: Set[int] = set()
    for part in str(value or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lower, upper = part.split("-", 1)
            result.update(range(int(lower), int(upper) + 1))
        else:
            result.add(int(part))
    return result


def load_map(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run(cmd: List[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=check,
    )


def qemu_processes() -> List[Tuple[int, str, str]]:
    """Return (pid, name, cmdline) for qemu-system processes."""
    rows: List[Tuple[int, str, str]] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        if not raw or b"qemu-system" not in raw:
            continue
        cmd = raw.replace(b"\0", b" ").decode("utf-8", errors="replace")
        match = re.search(r"-name\s+(\S+)", cmd)
        name = match.group(1) if match else ""
        # libvirt style: guest=postgre,debug-threads=on
        if name.startswith("guest="):
            name = name.split("=", 1)[1].split(",", 1)[0]
        rows.append((pid, name, cmd))
    return rows


def current_affinity(pid: int) -> str:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("Cpus_allowed_list:"):
                return line.split(":", 1)[1].strip()
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        pass
    completed = run(["taskset", "-cp", str(pid)])
    if completed.returncode == 0 and ":" in completed.stdout:
        return completed.stdout.rsplit(":", 1)[-1].strip()
    return "?"


def set_affinity(pid: int, cpuset: str) -> Tuple[bool, str]:
    """Set process affinity; try plain taskset then sudo -n."""
    before = current_affinity(pid)
    for cmd in (
        ["taskset", "-cp", cpuset, str(pid)],
        ["sudo", "-n", "taskset", "-cp", cpuset, str(pid)],
    ):
        completed = run(cmd)
        after = current_affinity(pid)
        if completed.returncode == 0 and expand_cpuset(after) == expand_cpuset(cpuset):
            return True, f"ok before={before} after={after} via={' '.join(cmd[:2])}"
        # taskset may report success while permission denied on set; re-check set equality
        if expand_cpuset(after) == expand_cpuset(cpuset):
            return True, f"already before={before} after={after}"
    after = current_affinity(pid)
    return False, f"failed before={before} after={after} wanted={cpuset}"


def apply_vm_pins(pin_map: Dict[str, Any], dry_run: bool) -> List[Dict[str, Any]]:
    wanted = {
        str(row["name"]): str(row["cpuset_cpus"])
        for row in pin_map.get("vms") or []
        if row.get("name") and row.get("cpuset_cpus")
    }
    results: List[Dict[str, Any]] = []
    live = {name: pid for pid, name, _ in qemu_processes() if name}
    for name, cpuset in wanted.items():
        pid = live.get(name)
        row: Dict[str, Any] = {
            "kind": "vm",
            "name": name,
            "pid": pid,
            "cpuset_cpus": cpuset,
        }
        if pid is None:
            row["status"] = "not_running"
            results.append(row)
            continue
        if dry_run:
            row["status"] = "dry_run"
            row["current"] = current_affinity(pid)
            results.append(row)
            continue
        ok, detail = set_affinity(pid, cpuset)
        row["status"] = "ok" if ok else "error"
        row["detail"] = detail
        results.append(row)
    return results


def apply_docker_pins(pin_map: Dict[str, Any], dry_run: bool) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    existing = set(
        run(["docker", "ps", "-a", "--format", "{{.Names}}"]).stdout.splitlines()
    )
    for row in pin_map.get("docker") or []:
        name = str(row.get("name") or "")
        cpuset = str(row.get("cpuset_cpus") or "")
        item: Dict[str, Any] = {
            "kind": "docker",
            "name": name,
            "cpuset_cpus": cpuset,
        }
        if not name or not cpuset:
            item["status"] = "skip"
            results.append(item)
            continue
        if name not in existing:
            item["status"] = "missing"
            results.append(item)
            continue
        if dry_run:
            inspect = run(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{.HostConfig.CpusetCpus}}|{{.State.Status}}",
                    name,
                ]
            )
            item["status"] = "dry_run"
            item["current"] = inspect.stdout.strip()
            results.append(item)
            continue
        completed = run(["docker", "update", f"--cpuset-cpus={cpuset}", name])
        inspect = run(
            [
                "docker",
                "inspect",
                "-f",
                "{{.HostConfig.CpusetCpus}}|{{.State.Status}}",
                name,
            ]
        )
        actual = inspect.stdout.strip().split("|", 1)[0]
        ok = completed.returncode == 0 and expand_cpuset(actual) == expand_cpuset(cpuset)
        item["status"] = "ok" if ok else "error"
        item["detail"] = inspect.stdout.strip()
        if completed.returncode != 0:
            item["error"] = completed.stdout.strip()
        results.append(item)
    return results


def validate_no_overlap(pin_map: Dict[str, Any]) -> List[str]:
    """Return overlap diagnostics for exclusive VM + slot + docker banks."""
    owners: Dict[int, List[str]] = {}
    labels: List[Tuple[str, str]] = []
    for row in pin_map.get("vms") or []:
        labels.append((f"vm:{row['name']}", str(row["cpuset_cpus"])))
    for row in pin_map.get("scheduler_slots") or []:
        # Pair slots may intentionally alias the same bank (vm41/vm44, ...).
        if "Shares bank" in str(row.get("note") or ""):
            continue
        labels.append((f"slot:{row['slot_id']}", str(row["cpuset_cpus"])))
    for row in pin_map.get("docker") or []:
        # docker may intentionally share banks across mutually exclusive roles
        if "Shares bank" in str(row.get("note") or "") or "do not co-run" in str(row.get("note") or ""):
            continue
        labels.append((f"docker:{row['name']}", str(row["cpuset_cpus"])))
    for label, cpuset in labels:
        for cpu in expand_cpuset(cpuset):
            owners.setdefault(cpu, []).append(label)
    issues: List[str] = []
    for cpu, names in sorted(owners.items()):
        unique = sorted(set(names))
        if len(unique) > 1:
            issues.append(f"cpu {cpu}: {', '.join(unique)}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--vms-only", action="store_true")
    parser.add_argument("--docker-only", action="store_true")
    parser.add_argument("--check-overlap", action="store_true")
    args = parser.parse_args()

    pin_map = load_map(args.map)
    if args.check_overlap:
        issues = validate_no_overlap(pin_map)
        if issues:
            print("OVERLAP DETECTED:", flush=True)
            for issue in issues[:50]:
                print(f"  {issue}", flush=True)
            print(f"total overlap cpus: {len(issues)}", flush=True)
            return 2
        print("no exclusive-bank overlaps", flush=True)
        return 0

    results: List[Dict[str, Any]] = []
    if not args.docker_only:
        results.extend(apply_vm_pins(pin_map, args.dry_run))
    if not args.vms_only:
        results.extend(apply_docker_pins(pin_map, args.dry_run))

    width = max((len(str(r.get("name") or "")) for r in results), default=10)
    print(
        f"{'kind':6} {'name'.ljust(width)} {'status':10} cpuset",
        flush=True,
    )
    errors = 0
    for row in results:
        status = str(row.get("status"))
        if status == "error":
            errors += 1
        extra = row.get("detail") or row.get("current") or ""
        print(
            f"{row.get('kind','?'):6} {str(row.get('name')).ljust(width)} "
            f"{status:10} {row.get('cpuset_cpus')} {extra}",
            flush=True,
        )

    print(
        "\nScheduler slot reservations (applied at rootless VM launch):",
        flush=True,
    )
    for slot in pin_map.get("scheduler_slots") or []:
        print(
            f"  {slot['slot_id']}: qemu={slot['cpuset_cpus']} "
            f"client={slot.get('client_cpuset_cpus')} max_vcpus={slot.get('max_vcpus')}",
            flush=True,
        )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
