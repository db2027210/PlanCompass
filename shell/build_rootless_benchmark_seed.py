#!/usr/bin/env python3
"""Build one validated PostgreSQL benchmark seed in a rootless QEMU VM."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict

from double_loop_rootless_vm_task_worker import (
    process_active,
    run,
    stop_vm,
    wait_ready,
    write_json,
)


ROOT = Path(__file__).resolve().parents[1]
DATABASES = {
    "job": ("imdbload", "template_imdbload"),
    "tpch": ("benchbase_tpch", "template_benchbase_tpch"),
    "tpcc": ("benchbase_tpcc", "template_benchbase_tpcc"),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", choices=tuple(DATABASES), required=True)
    parser.add_argument("--base-seed", type=Path, required=True)
    parser.add_argument("--output-seed", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--ssh-port", type=int, required=True)
    parser.add_argument("--postgres-port", type=int, required=True)
    parser.add_argument("--cpu-cores", type=int, default=12)
    parser.add_argument("--memory-mib", type=int, default=12288)
    parser.add_argument("--benchbase-batch-size", type=int, default=128)
    parser.add_argument(
        "--benchbase-root",
        type=Path,
        default=Path("/home/anonymous/benchbase-postgres"),
    )
    parser.add_argument("--tpch-lineitem-partitions", type=int, default=1)
    parser.add_argument("--load-timeout-s", type=float, default=18000.0)
    parser.add_argument("--kernel", type=Path)
    parser.add_argument("--initrd", type=Path)
    parser.add_argument(
        "--kernel-append",
        default=(
            "root=/dev/mapper/ubuntu--vg-ubuntu--lv rw fstab=no "
            "module_blacklist=ramoops,chromeos_pstore,efi_pstore,pstore_blk,pstore_zone "
            "console=ttyS0,115200n8 earlyprintk=serial"
        ),
    )
    args = parser.parse_args()
    if args.benchbase_batch_size < 1:
        parser.error("--benchbase-batch-size must be positive")
    if args.benchmark in {"tpch", "tpcc"} and not args.benchbase_root.is_dir():
        parser.error(f"--benchbase-root is not a directory: {args.benchbase_root}")
    if args.tpch_lineitem_partitions < 1:
        parser.error("--tpch-lineitem-partitions must be positive")
    if args.benchmark != "tpch" and args.tpch_lineitem_partitions != 1:
        parser.error("--tpch-lineitem-partitions is only valid for TPC-H")
    if (args.kernel is None) != (args.initrd is None):
        parser.error("--kernel and --initrd must be provided together")
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    output = args.output_seed.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise RuntimeError(f"refusing to overwrite existing seed: {output}")
    building = output.with_suffix(output.suffix + ".building")
    if building.exists():
        raise RuntimeError(f"stale building overlay requires review: {building}")
    vm_name = f"ra-rootless-seed-{args.benchmark}"
    vm_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"resource-adaptive/seed/{args.benchmark}"))
    qmp = Path(f"/tmp/ra-rootless-{os.getuid()}-seed-{args.benchmark}.qmp")
    pidfile = root / "qemu.pid"
    serial = root / "serial.log"
    qemu_pid: Any = None
    audit: Dict[str, Any] = {
        "status": "failed",
        "benchmark": args.benchmark,
        "vm_name": vm_name,
        "uuid": vm_uuid,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "direct_kernel_boot": bool(args.kernel),
        "benchbase_root": str(args.benchbase_root.resolve()),
        "benchbase_batch_size": args.benchbase_batch_size,
        "tpch_lineitem_partitions": args.tpch_lineitem_partitions,
    }
    benchbase_jar = args.benchbase_root.resolve() / "benchbase.jar"
    if args.benchmark in {"tpch", "tpcc"}:
        if not benchbase_jar.is_file():
            raise FileNotFoundError(benchbase_jar)
        audit["benchbase_jar_sha256"] = sha256_file(benchbase_jar)
    try:
        run(
            [
                "qemu-img", "create", "-f", "qcow2", "-F", "qcow2",
                "-b", str(args.base_seed.resolve()), str(building),
            ],
            timeout=120,
        )
        qemu = [
            "qemu-system-x86_64", "-name", vm_name,
            "-machine", "pc-q35-4.2,accel=kvm", "-cpu", "host",
            "-m", str(args.memory_mib),
            "-smp", f"{args.cpu_cores},sockets=1,cores={args.cpu_cores},threads=1",
            "-no-user-config", "-nodefaults", "-display", "none", "-daemonize",
            "-pidfile", str(pidfile), "-uuid", vm_uuid, "-boot", "strict=on",
            "-device", "pcie-root-port,port=0x8,chassis=1,id=pci.1,bus=pcie.0,multifunction=on,addr=0x1",
            "-device", "pcie-root-port,port=0x9,chassis=2,id=pci.2,bus=pcie.0,addr=0x1.0x1",
            "-device", "pcie-root-port,port=0xa,chassis=3,id=pci.3,bus=pcie.0,addr=0x1.0x2",
            "-drive", f"file={building},if=none,id=drive0,format=qcow2,cache=writeback,discard=unmap",
            "-device", "virtio-blk-pci,drive=drive0,bus=pci.3,addr=0x0,bootindex=1",
            "-netdev",
            f"user,id=net0,hostfwd=tcp:127.0.0.1:{args.ssh_port}-:22,"
            f"hostfwd=tcp:127.0.0.1:{args.postgres_port}-:25432",
            "-device", "virtio-net-pci,netdev=net0,bus=pci.1,addr=0x0",
            "-object", "rng-random,id=objrng,filename=/dev/urandom",
            "-device", "virtio-rng-pci,rng=objrng",
            "-qmp", f"unix:{qmp},server,nowait", "-serial", f"file:{serial}",
        ]
        if args.kernel is not None and args.initrd is not None:
            kernel = args.kernel.resolve()
            initrd = args.initrd.resolve()
            if not kernel.is_file():
                raise FileNotFoundError(f"kernel not found: {kernel}")
            if not initrd.is_file():
                raise FileNotFoundError(f"initrd not found: {initrd}")
            qemu.extend(
                [
                    "-kernel", str(kernel),
                    "-initrd", str(initrd),
                    "-append", str(args.kernel_append),
                ]
            )
        run(qemu, timeout=120)
        qemu_pid = int(pidfile.read_text(encoding="utf-8").strip())
        readiness = wait_ready(
            {"slot": {"ssh_port": args.ssh_port, "postgres_port": args.postgres_port}},
            vm_uuid,
        )
        runtime = root / "runtime"
        run(
            [
                sys.executable,
                str(ROOT / "shell/prepare_double_loop_matrix_runtime.py"),
                "--benchmark", args.benchmark, "--target", "none",
                "--vm-name", vm_name, "--host", "127.0.0.1",
                "--ssh-port", str(args.ssh_port), "--port", str(args.postgres_port),
                "--output-dir", str(runtime), "--cpu-cores", str(args.cpu_cores),
                "--mem-gb", str(args.memory_mib / 1024.0),
                "--read-mb", "800", "--write-mb", "800",
            ],
            timeout=300,
        )
        if args.benchmark in {"tpch", "tpcc"}:
            benchbase_config = runtime / f"{args.benchmark}.xml"
            tree = ET.parse(benchbase_config)
            batch_size = tree.getroot().find("batchsize")
            if batch_size is None:
                raise RuntimeError(
                    f"BenchBase config has no batchsize element: {benchbase_config}"
                )
            batch_size.text = str(args.benchbase_batch_size)
            tree.write(benchbase_config, encoding="UTF-8", xml_declaration=True)
        database, template = DATABASES[args.benchmark]
        command = [
            sys.executable, "-u", str(ROOT / "shell/provision_generic_benchmark_database.py"),
            "--benchmark", args.benchmark, "--host", "127.0.0.1",
            "--port", str(args.postgres_port), "--user", "postgres",
            "--password", 'CHANGE_ME', "--database", database,
            "--template-database", template,
            "--audit-path", str(root / "database_bootstrap_audit.json"),
            "--vm-name", vm_name, "--expected-vm-uuid", vm_uuid,
            "--endpoint-lease-dir", str(root / "leases"),
            "--ssh-target", "anonymous@127.0.0.1", "--ssh-port", str(args.ssh_port),
            "--load-timeout-s", str(args.load_timeout_s),
        ]
        if args.benchmark in {"tpch", "tpcc"}:
            command.extend(
                [
                    "--benchbase-root", str(args.benchbase_root.resolve()),
                    "--benchbase-config", str(runtime / f"{args.benchmark}.xml"),
                    "--java-xmx", "4g",
                    "--tpch-lineitem-partitions", str(args.tpch_lineitem_partitions),
                ]
            )
        if args.benchmark == "tpch":
            command.append("--fast-seed-load")
        run(command, timeout=args.load_timeout_s + 900.0)
        bootstrap = json.loads(
            (root / "database_bootstrap_audit.json").read_text(encoding="utf-8")
        )
        if bootstrap.get("status") != "ok":
            raise RuntimeError("database bootstrap audit did not pass")
        audit.update({"status": "loaded", "readiness": readiness, "bootstrap": bootstrap})
    except Exception as exc:  # noqa: BLE001 - durable seed-build boundary
        audit.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        write_json(root / "rootless_seed_build_audit.json", audit)
        raise
    finally:
        stop_vm(qemu_pid, qmp)
    check = subprocess.run(
        ["qemu-img", "check", str(building)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=600,
        check=False,
    )
    if check.returncode != 0:
        raise RuntimeError(f"qemu-img check failed: {check.stdout}")
    os.replace(building, output)
    os.chmod(output, 0o444)
    audit.update({"status": "ok", "output_seed": str(output), "qemu_img_check": check.stdout})
    write_json(root / "rootless_seed_build_audit.json", audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
