#!/usr/bin/env python3
"""Validate one immutable TPC-H seed before the formal scheduler may use it."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List

from double_loop_rootless_vm_task_worker import run, stop_vm, wait_ready, write_json


def psql_rows(
    port: int,
    database: str,
    sql: str,
    *,
    password: str,
) -> List[List[str]]:
    env = dict(os.environ)
    env["PGPASSWORD"] = password
    completed = subprocess.run(
        [
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            "127.0.0.1",
            "-p",
            str(port),
            "-U",
            "postgres",
            "-d",
            database,
            "-At",
            "-F",
            "|",
            "-c",
            sql,
        ],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=1800,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"psql validation failed: {completed.stdout}")
    return [line.split("|") for line in completed.stdout.splitlines() if line]


def validate_database(args: argparse.Namespace) -> Dict[str, Any]:
    expected = {
        "region": 5,
        "nation": 25,
        "supplier": 10_000 * args.scale_factor,
        "customer": 150_000 * args.scale_factor,
        "part": 200_000 * args.scale_factor,
        "partsupp": 800_000 * args.scale_factor,
        "orders": 1_500_000 * args.scale_factor,
    }
    row_counts = {
        table: int(
            psql_rows(
                args.postgres_port,
                args.database,
                f"SELECT count(*) FROM {table};",
                password=args.password,
            )[0][0]
        )
        for table in (*expected, "lineitem")
    }
    lineitem_range = [
        int(expected["orders"] * 3.9),
        int(expected["orders"] * 4.1),
    ]
    mismatches: Dict[str, Any] = {
        table: {"expected": count, "actual": row_counts[table]}
        for table, count in expected.items()
        if row_counts[table] != count
    }
    if not lineitem_range[0] <= row_counts["lineitem"] <= lineitem_range[1]:
        mismatches["lineitem"] = {
            "expected_range": lineitem_range,
            "actual": row_counts["lineitem"],
        }
    persistence = psql_rows(
        args.postgres_port,
        args.database,
        "SELECT relpersistence FROM pg_class WHERE oid='lineitem'::regclass;",
        password=args.password,
    )[0][0]
    settings = {
        row[0]: {"setting": row[1], "unit": row[2]}
        for row in psql_rows(
            args.postgres_port,
            args.database,
            "SELECT name,setting,coalesce(unit,'') FROM pg_settings "
            "WHERE name IN ('synchronous_commit','max_wal_size','checkpoint_timeout') "
            "ORDER BY name;",
            password=args.password,
        )
    }
    template_row = psql_rows(
        args.postgres_port,
        "postgres",
        "SELECT datistemplate,datallowconn FROM pg_database "
        f"WHERE datname='{args.template_database}';",
        password=args.password,
    )
    if persistence != "p":
        mismatches["lineitem_persistence"] = {"expected": "p", "actual": persistence}
    setting_expected = {
        "synchronous_commit": ("on", ""),
        "max_wal_size": ("1024", "MB"),
        "checkpoint_timeout": ("300", "s"),
    }
    for name, (setting, unit) in setting_expected.items():
        actual = settings.get(name, {})
        if actual.get("setting") != setting or actual.get("unit") != unit:
            mismatches[name] = {
                "expected": {"setting": setting, "unit": unit},
                "actual": actual,
            }
    if template_row != [["t", "f"]]:
        mismatches["template_database"] = {
            "expected": [["t", "f"]],
            "actual": template_row,
        }
    verification = {
        "scale_factor": args.scale_factor,
        "row_counts": row_counts,
        "fixed_expected_counts": expected,
        "lineitem_expected_range": lineitem_range,
        "lineitem_persistence": persistence,
        "restored_settings": settings,
        "template_flags": template_row,
        "mismatches": mismatches,
        "passed": not mismatches,
    }
    if mismatches:
        raise RuntimeError(f"TPC-H seed validation failed: {mismatches}")
    return verification


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--marker", type=Path, required=True)
    parser.add_argument("--ssh-port", type=int, required=True)
    parser.add_argument("--postgres-port", type=int, required=True)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--initrd", type=Path, required=True)
    parser.add_argument("--cpu-cores", type=int, default=8)
    parser.add_argument("--memory-mib", type=int, default=8192)
    parser.add_argument("--scale-factor", type=int, default=3)
    parser.add_argument("--database", default="benchbase_tpch")
    parser.add_argument("--template-database", default="template_benchbase_tpch")
    parser.add_argument("--password", default='CHANGE_ME')
    args = parser.parse_args()
    if args.scale_factor < 1:
        parser.error("--scale-factor must be positive")
    seed = args.seed.resolve()
    if not seed.is_file():
        raise FileNotFoundError(seed)
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = args.marker.resolve()
    if marker.exists():
        raise RuntimeError(f"refusing to overwrite validation marker: {marker}")
    overlay = root / "validation-overlay.qcow2"
    if overlay.exists():
        raise RuntimeError(f"stale validation overlay requires review: {overlay}")
    vm_name = "ra-rootless-seed-validate-tpch"
    vm_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, "resource-adaptive/seed/validate/tpch"))
    qmp = Path(f"/tmp/ra-rootless-{os.getuid()}-validate-tpch.qmp")
    pidfile = root / "qemu.pid"
    serial = root / "serial.log"
    audit: Dict[str, Any] = {
        "status": "failed",
        "seed": str(seed),
        "seed_size_bytes": seed.stat().st_size,
        "seed_mode": oct(seed.stat().st_mode & 0o777),
        "vm_name": vm_name,
        "uuid": vm_uuid,
    }
    qemu_pid: Any = None
    try:
        if seed.stat().st_mode & 0o222:
            raise RuntimeError(f"seed is not immutable: mode={audit['seed_mode']}")
        run(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-F",
                "qcow2",
                "-b",
                str(seed),
                str(overlay),
            ],
            timeout=120,
        )
        run(
            [
                "qemu-system-x86_64",
                "-name",
                vm_name,
                "-machine",
                "pc-q35-4.2,accel=kvm",
                "-cpu",
                "host",
                "-m",
                str(args.memory_mib),
                "-smp",
                f"{args.cpu_cores},sockets=1,cores={args.cpu_cores},threads=1",
                "-no-user-config",
                "-nodefaults",
                "-display",
                "none",
                "-daemonize",
                "-pidfile",
                str(pidfile),
                "-uuid",
                vm_uuid,
                "-boot",
                "strict=on",
                "-device",
                "pcie-root-port,port=0x8,chassis=1,id=pci.1,bus=pcie.0,multifunction=on,addr=0x1",
                "-device",
                "pcie-root-port,port=0xa,chassis=3,id=pci.3,bus=pcie.0,addr=0x1.0x2",
                "-drive",
                f"file={overlay},if=none,id=drive0,format=qcow2,cache=writeback",
                "-device",
                "virtio-blk-pci,drive=drive0,bus=pci.3,addr=0x0,bootindex=1",
                "-netdev",
                f"user,id=net0,hostfwd=tcp:127.0.0.1:{args.ssh_port}-:22,"
                f"hostfwd=tcp:127.0.0.1:{args.postgres_port}-:25432",
                "-device",
                "virtio-net-pci,netdev=net0,bus=pcie.0,addr=0x2",
                "-object",
                "rng-random,id=objrng,filename=/dev/urandom",
                "-device",
                "virtio-rng-pci,rng=objrng",
                "-qmp",
                f"unix:{qmp},server,nowait",
                "-serial",
                f"file:{serial}",
                "-kernel",
                str(args.kernel.resolve()),
                "-initrd",
                str(args.initrd.resolve()),
                "-append",
                "root=/dev/mapper/ubuntu--vg-ubuntu--lv rw fstab=no "
                "module_blacklist=ramoops,chromeos_pstore,efi_pstore,pstore_blk,pstore_zone "
                "console=ttyS0,115200n8 earlyprintk=serial",
            ],
            timeout=120,
        )
        qemu_pid = int(pidfile.read_text(encoding="utf-8").strip())
        audit["readiness"] = wait_ready(
            {"slot": {"ssh_port": args.ssh_port, "postgres_port": args.postgres_port}},
            vm_uuid,
        )
        audit["database"] = validate_database(args)
        audit["status"] = "database_validated"
    except Exception as exc:  # noqa: BLE001 - durable validation boundary
        audit["error"] = f"{type(exc).__name__}: {exc}"
        write_json(root / "seed_validation_audit.json", audit)
        raise
    finally:
        stop_vm(qemu_pid, qmp)
    check = subprocess.run(
        ["qemu-img", "check", str(seed)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=600,
        check=False,
    )
    if check.returncode != 0:
        raise RuntimeError(f"qemu-img check failed: {check.stdout}")
    audit.update({"status": "ok", "qemu_img_check": check.stdout})
    audit_path = root / "seed_validation_audit.json"
    write_json(audit_path, audit)
    write_json(
        marker,
        {
            "status": "ok",
            "seed": str(seed),
            "validation_audit": str(audit_path),
            "row_counts": audit["database"]["row_counts"],
        },
    )
    overlay.unlink(missing_ok=True)
    print(json.dumps(audit, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
