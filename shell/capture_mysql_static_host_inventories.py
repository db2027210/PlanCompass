#!/usr/bin/env python3
"""Capture the three live hosts used by the five MySQL-only formal lanes."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REMOTE_CAPTURE = ROOT / "shell/remote_capture_mysql_static_inventory.py"
OUTPUT = (
    ROOT
    / "history/mysql_sgd_env_agents_20260723"
    / "static_isolation_20260724T0134"
)
HOSTS = {
    "host01": ("anonymous@192.0.2.3", "node01"),
    "host02": ("anonymous@192.0.2.4", "node02"),
    "host04": ("anonymous@192.0.2.7", "node04"),
}


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    data = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
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


def main() -> int:
    source = REMOTE_CAPTURE.read_bytes()
    summary: dict[str, Any] = {
        "schema_version": "mysql_static_inventory_capture_v1",
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "hosts": {},
    }
    failed = False
    for host_id, (target, expected_hostname) in HOSTS.items():
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                target,
                "python3",
                "-",
            ],
            input=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
        row: dict[str, Any] = {
            "target": target,
            "returncode": result.returncode,
            "stderr": result.stderr.decode("utf-8", "replace").strip(),
        }
        try:
            inventory = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            inventory = {}
        hostname_ok = inventory.get("hostname") == expected_hostname
        row["hostname_ok"] = hostname_ok
        row["process_count"] = len(inventory.get("processes") or [])
        row["container_count"] = len(inventory.get("running_containers") or [])
        row["passed"] = result.returncode == 0 and hostname_ok
        if row["passed"]:
            atomic_write(OUTPUT / f"{host_id}.inventory.json", inventory)
        else:
            failed = True
        summary["hosts"][host_id] = row
    atomic_write(OUTPUT / "capture_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
