#!/usr/bin/env python3
"""Rebuild the 18 owned matrix-run VMs as resettable seeded QEMU guests.

The immutable per-VM seed is booted once so machine-id/DHCP identity is stable.
Every benchmark replay then replaces only a small work overlay backed by that
seed, avoiding a 10 GiB SQL template copy for every Top-K validation.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Dict, List

try:
    from .provision_double_loop_matrix_direct_qemu import (
        DIRECT_ROOT,
        direct_pid_path,
        direct_running,
        launch_direct,
        read_root_file,
        tap_name,
        wait_ready,
    )
    from .provision_double_loop_matrix_vms import (
        DEFAULT_PREPARED,
        DEFAULT_ROOT,
        VmSpec,
        domain_running,
        run,
        vm_specs,
        write_json,
    )
except ImportError:
    from provision_double_loop_matrix_direct_qemu import (
        DIRECT_ROOT,
        direct_pid_path,
        direct_running,
        launch_direct,
        read_root_file,
        tap_name,
        wait_ready,
    )
    from provision_double_loop_matrix_vms import (
        DEFAULT_PREPARED,
        DEFAULT_ROOT,
        VmSpec,
        domain_running,
        run,
        vm_specs,
        write_json,
    )


def seed_root(spec: VmSpec) -> str:
    # Keep seed and disposable work layers in the same experiment namespace.
    # This also lets a conflict-free retry move an owned batch to a new disk
    # without silently retaining a backing layer in the busy old directory.
    return str(Path(spec.overlay_root).resolve().parent / "vm_seeds")


def seed_spec(spec: VmSpec) -> VmSpec:
    # Database loading is seed construction, not the measured workload.  Give
    # it enough CPU/RAM and unrestricted storage so low-memory/low-IO target
    # profiles do not fail while creating indexes or immutable templates.  The
    # work VM is still launched later with the exact measured profile.
    return replace(
        spec,
        overlay_root=seed_root(spec),
        cpu=max(spec.cpu, 16),
        memory_mib=max(spec.memory_mib, 12288),
        read_mb=800,
        write_mb=800,
    )


def database_bootstrap_audit_path(spec: VmSpec) -> Path:
    """Durable proof that a generic benchmark seed contains both databases."""
    return Path(seed_root(spec)) / f"{spec.name}.database_bootstrap.json"


def expected_vm_uuid(spec: VmSpec) -> str:
    index = int(spec.mac.rsplit(":", 1)[-1], 16)
    return f"52000000-bf8d-4000-8000-{index:012d}"


def bootstrap_generic_database(
    spec: VmSpec,
    ip: str,
    root: Path,
    *,
    template_only: bool = False,
) -> Dict[str, Any]:
    """Load JOB/TPC-H/TPC-C into the live seed and create reset templates."""
    if spec.benchmark not in {"job", "tpch", "tpcc"}:
        return {"name": spec.name, "status": "not_required"}
    runtime_dir = root / "seed_database_bootstrap" / spec.name
    runtime_dir.mkdir(parents=True, exist_ok=True)
    seed = seed_spec(spec)
    prepare = [
        sys.executable,
        "-u",
        str(Path(__file__).resolve().parent / "prepare_double_loop_matrix_runtime.py"),
        "--benchmark",
        spec.benchmark,
        "--target",
        spec.target,
        "--vm-name",
        spec.name,
        "--exp-id",
        spec.exp_id,
        "--repeat-id",
        str(spec.repeat_id),
        "--mac",
        spec.mac,
        "--seed-overlay",
        str(seed.overlay),
        "--work-overlay",
        str(spec.overlay),
        "--host",
        ip,
        "--output-dir",
        str(runtime_dir),
        "--cpu-cores",
        str(spec.cpu),
        "--mem-gb",
        str(spec.memory_mib / 1024.0),
        "--read-mb",
        str(spec.read_mb),
        "--write-mb",
        str(spec.write_mb),
    ]
    run(prepare, timeout=300)
    runtime = json.loads((runtime_dir / "runtime_manifest.json").read_text(encoding="utf-8"))
    audit_path = runtime_dir / "database_bootstrap_audit.json"
    command = [
        sys.executable,
        "-u",
        str(Path(__file__).resolve().parent / "provision_generic_benchmark_database.py"),
        "--benchmark",
        spec.benchmark,
        "--host",
        ip,
        "--port",
        "25432",
        "--user",
        "postgres",
        "--password",
        'CHANGE_ME',
        "--database",
        str(runtime["database"]),
        "--template-database",
        str(runtime["template_database"]),
        "--audit-path",
        str(audit_path),
        "--vm-name",
        spec.name,
        "--expected-vm-uuid",
        expected_vm_uuid(spec),
        "--endpoint-lease-dir",
        str(root / "seed_database_bootstrap" / "leases"),
        "--ssh-target",
        f"anonymous@{ip}",
        "--load-timeout-s",
        "14400",
    ]
    if spec.benchmark in {"tpch", "tpcc"}:
        command.extend(
            [
                "--benchbase-root",
                "/home/anonymous/benchbase-postgres",
                "--benchbase-config",
                str(runtime_dir / f"{spec.benchmark}.xml"),
                "--java-xmx",
                "4g",
            ]
        )
    if template_only:
        command.append("--template-only")
    run(command, timeout=18000)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("status") != "ok":
        raise RuntimeError(f"generic database bootstrap did not pass: {audit_path}")
    durable_audit_path = root / "seed_database_bootstrap" / f"{spec.name}.database_bootstrap.json"
    write_json(durable_audit_path, audit)
    return {
        "name": spec.name,
        "status": "ok",
        "benchmark": spec.benchmark,
        "database": runtime["database"],
        "template_database": runtime["template_database"],
        "audit_path": str(durable_audit_path),
    }


def process_active(pid: int) -> bool:
    """Check a QEMU PID without sending a privileged signal."""
    try:
        status = Path(f"/proc/{int(pid)}/status").read_text(
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError, ValueError):
        return False
    state_line = next(
        (line for line in status.splitlines() if line.startswith("State:")),
        "",
    )
    parts = state_line.split()
    state = parts[1].upper() if len(parts) >= 2 else ""
    return state not in {"Z", "X"}


def stop_owned_guest(spec: VmSpec, *, stop_libvirt: bool = False) -> Dict[str, Any]:
    mode = "stopped"
    if direct_running(spec) or direct_running(seed_spec(spec)):
        path = direct_pid_path(spec)
        pid = int(read_root_file(path))
        run(["sudo", "-n", "kill", "-TERM", str(pid)], check=False)
        deadline = time.time() + 60.0
        while time.time() < deadline:
            if not process_active(pid):
                break
            time.sleep(1.0)
        else:
            run(["sudo", "-n", "kill", "-KILL", str(pid)], check=False)
        mode = "direct_qemu"
    if stop_libvirt:
        run(
            ["sudo", "-n", "virsh", "-c", "qemu:///system", "destroy", spec.name],
            timeout=180,
            check=False,
        )
        run(
            ["sudo", "-n", "virsh", "-c", "qemu:///system", "undefine", spec.name],
            timeout=180,
            check=False,
        )
        mode = "libvirt"
    return {"name": spec.name, "previous_runtime": mode}


def running_libvirt_names(specs: List[VmSpec]) -> set[str]:
    """Inspect qemu command lines without calling a congested libvirtd/sudo."""
    process_list = subprocess.run(
        ["ps", "-eo", "cmd"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        check=False,
    ).stdout
    return {
        spec.name
        for spec in specs
        if f"guest={spec.name}," in process_list
    }


def unlink_owned(path: Path) -> None:
    run(["sudo", "-n", "unlink", str(path)], check=False)


def bootstrap_seed(spec: VmSpec, prepared: Path) -> Dict[str, Any]:
    seed = seed_spec(spec)
    unlink_owned(seed.overlay)
    unlink_owned(spec.overlay)
    return launch_direct(seed, prepared)


def graceful_poweroff(spec: VmSpec, ip: str) -> None:
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=5",
        f"anonymous@{ip}",
        "sudo -n systemctl stop resource-adaptive-postgresql; sudo -n sync; sudo -n poweroff",
    ]
    run(command, timeout=30, check=False)
    path = direct_pid_path(spec)
    pid = int(read_root_file(path))
    deadline = time.time() + 60.0
    while time.time() < deadline:
        if not process_active(pid):
            return
        time.sleep(2.0)
    run(["sudo", "-n", "kill", "-TERM", str(pid)], check=False)
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if not process_active(pid):
            return
        time.sleep(0.5)
    run(["sudo", "-n", "kill", "-KILL", str(pid)], check=False)
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if not process_active(pid):
            return
        time.sleep(0.25)
    raise RuntimeError(f"seed QEMU PID remained alive after SIGKILL: {spec.name} pid={pid}")


def launch_work(spec: VmSpec) -> Dict[str, Any]:
    seed = seed_spec(spec).overlay
    run(["sudo", "-n", "chmod", "0444", str(seed)])
    unlink_owned(spec.overlay)
    return launch_direct(spec, seed)


def validate_seed(spec: VmSpec) -> Dict[str, Any]:
    """Run an unprivileged preflight before stronger boot/DB checks.

    The qcow2 backing file is below a root-only prepared-image directory, so
    even a read-only ``qemu-img check`` enters sudo/PAM.  That control path can
    block and strand the worker.  QEMU boot, UUID, PostgreSQL readiness and
    database/template verification are authoritative; this preflight only
    requires a non-empty regular seed file.
    """
    path = seed_spec(spec).overlay
    if not path.is_file():
        raise FileNotFoundError(f"seed overlay is missing: {path}")
    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        raise RuntimeError(f"seed overlay is empty: {path}")
    return {
        "name": spec.name,
        "path": str(path),
        "size_bytes": size_bytes,
        "preflight": "nonempty_regular_file",
        "authoritative_checks": [
            "qemu_boot",
            "vm_uuid",
            "postgres_ready",
            "database_and_template_semantics",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--prepared", type=Path, default=DEFAULT_PREPARED)
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated experiment ids, exp:repeat ids, or exact VM names",
    )
    parser.add_argument(
        "--overlay-root",
        type=Path,
        help="place all selected work/seed layers in this isolated directory",
    )
    parser.add_argument(
        "--memory-mib",
        type=int,
        help="override guest memory for the selected owned VM batch",
    )
    parser.add_argument(
        "--cpu-cores",
        type=int,
        help="override guest CPU cores for the selected owned VM batch",
    )
    parser.add_argument(
        "--read-mb",
        type=float,
        help="override guest read bandwidth MiB/s for the selected owned VM batch",
    )
    parser.add_argument(
        "--write-mb",
        type=float,
        help="override guest write bandwidth MiB/s for the selected owned VM batch",
    )
    parser.add_argument(
        "--resume-from-seeds",
        action="store_true",
        help="validate the already powered-off seed images, then create and boot work layers",
    )
    parser.add_argument(
        "--repair-generic-template",
        action="store_true",
        help="boot an already loaded generic seed, rebuild only its immutable template, then launch work",
    )
    args = parser.parse_args()
    specs = vm_specs()
    selected = {item.strip().lower() for item in args.only.split(",") if item.strip()}
    if selected:
        specs = [
            spec
            for spec in specs
            if {
                spec.exp_id.lower(),
                f"{spec.exp_id.lower()}:{spec.repeat_id}",
                spec.name.lower(),
            }
            & selected
        ]
        if not specs:
            raise RuntimeError(f"--only selected no matrix VM: {sorted(selected)}")
    if args.overlay_root is not None:
        specs = [replace(spec, overlay_root=str(args.overlay_root.resolve())) for spec in specs]
    if args.memory_mib is not None:
        if args.memory_mib < 384:
            raise ValueError("--memory-mib must be at least 384")
        specs = [replace(spec, memory_mib=args.memory_mib) for spec in specs]
    if args.cpu_cores is not None:
        if args.cpu_cores < 1:
            raise ValueError("--cpu-cores must be positive")
        specs = [replace(spec, cpu=args.cpu_cores) for spec in specs]
    if args.read_mb is not None:
        if args.read_mb <= 0:
            raise ValueError("--read-mb must be positive")
        specs = [replace(spec, read_mb=args.read_mb) for spec in specs]
    if args.write_mb is not None:
        if args.write_mb <= 0:
            raise ValueError("--write-mb must be positive")
        specs = [replace(spec, write_mb=args.write_mb) for spec in specs]
    prepared = args.prepared.resolve()
    root = args.root.resolve()
    # Resume consumes already-built per-VM seeds and must not touch the
    # privileged prepared-image directory at all.  On a fresh build the
    # privileged qemu-img invocation below is the authoritative existence
    # check; an extra ``sudo test`` can hang independently and cannot be
    # killed by this unprivileged parent after a timeout.
    if args.resume_from_seeds and args.repair_generic_template:
        raise ValueError("--resume-from-seeds and --repair-generic-template are mutually exclusive")
    if not args.resume_from_seeds and not args.repair_generic_template:
        try:
            prepared_missing = not prepared.is_file()
        except PermissionError:
            prepared_missing = False
        if prepared_missing:
            raise FileNotFoundError(f"prepared image is missing: {prepared}")
    audit = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "owned_namespace": "ra-matrix-v15-* / ra8dXX",
        "spec_count": len(specs),
        "external_domains_modified": False,
        "external_containers_modified": False,
        "old_overlay_disposition": "unlinked_after_exact_owned_guest_stop",
    }
    write_json(root / "seeded_rebuild_audit.json", audit)

    if args.resume_from_seeds:
        # A previous invocation can be interrupted after all seed guests have
        # shut down but before work layers are created.  Resume only when no
        # matrix guest is live, then independently validate every seed.
        live_work = [spec.name for spec in specs if direct_running(spec)]
        live_libvirt = sorted(running_libvirt_names(specs))
        if live_work or live_libvirt:
            raise RuntimeError(
                "cannot resume with work/libvirt guests live: "
                + ",".join(sorted(set(live_work + live_libvirt)))
            )
        live_seeds = [seed_spec(spec) for spec in specs if direct_running(seed_spec(spec))]
        if live_seeds:
            print(f"[resume] gracefully freezing {len(live_seeds)} live seeds", flush=True)
            seed_ready = wait_ready(live_seeds, 900.0)
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                futures = {
                    executor.submit(graceful_poweroff, spec, seed_ready[spec.name]["ip"]): spec
                    for spec in live_seeds
                }
                for future in concurrent.futures.as_completed(futures):
                    spec = futures[future]
                    future.result()
                    print(f"[seed-frozen-resume] {spec.name}", flush=True)
        rows: List[Dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(validate_seed, spec): spec for spec in specs}
            for future in concurrent.futures.as_completed(futures):
                spec = futures[future]
                rows.append(future.result())
                print(f"[seed-checked] {spec.name}", flush=True)
        write_json(root / "seeded_rebuild_seed_checks.json", {"rows": rows})
    elif args.repair_generic_template:
        invalid = [spec.name for spec in specs if spec.benchmark not in {"job", "tpch", "tpcc"}]
        if invalid:
            raise RuntimeError("template repair is only valid for generic benchmarks: " + ",".join(invalid))
        live = [spec.name for spec in specs if direct_running(spec)]
        live.extend(sorted(running_libvirt_names(specs)))
        if live:
            raise RuntimeError("cannot repair generic seed with live work/libvirt guest: " + ",".join(live))
        seed_checks = [validate_seed(spec) for spec in specs]
        write_json(root / "seeded_rebuild_seed_checks_before_repair.json", {"rows": seed_checks})
        for spec in specs:
            launch_direct(seed_spec(spec), prepared)
            print(f"[repair-seed-boot] {spec.name}", flush=True)
        seed_ready = wait_ready([seed_spec(spec) for spec in specs], 1800.0)
        database_rows = []
        for spec in specs:
            database_rows.append(
                bootstrap_generic_database(
                    spec,
                    seed_ready[spec.name]["ip"],
                    root,
                    template_only=True,
                )
            )
            print(f"[repair-template-ready] {spec.name}", flush=True)
        write_json(root / "seeded_rebuild_database_template_repair.json", {"rows": database_rows})
        for spec in specs:
            graceful_poweroff(seed_spec(spec), seed_ready[spec.name]["ip"])
            print(f"[repair-seed-frozen] {spec.name}", flush=True)
        seed_checks = [validate_seed(spec) for spec in specs]
        write_json(root / "seeded_rebuild_seed_checks_after_repair.json", {"rows": seed_checks})
    else:
        # Stop only the exact matrix namespace.  Parallel direct guests stop
        # independently; libvirt calls are scoped to the five exact owned names.
        stop_rows: List[Dict[str, Any]] = []
        libvirt_names = running_libvirt_names(specs)
        libvirt_specs = [spec for spec in specs if spec.name in libvirt_names]
        direct_specs = [spec for spec in specs if spec not in libvirt_specs]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            for row in executor.map(stop_owned_guest, direct_specs):
                stop_rows.append(row)
                print(f"[stopped] {row['name']} previous={row['previous_runtime']}", flush=True)
        # Shared libvirt/AppArmor teardown is intentionally serial.  It affects
        # only these exact five owned names and avoids the host-wide queue observed
        # during the first parallel submission.
        for spec in libvirt_specs:
            row = stop_owned_guest(spec, stop_libvirt=True)
            stop_rows.append(row)
            print(f"[stopped] {row['name']} previous={row['previous_runtime']}", flush=True)
        write_json(root / "seeded_rebuild_stopped.json", {"rows": stop_rows})

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(bootstrap_seed, spec, prepared): spec for spec in specs}
            for future in concurrent.futures.as_completed(futures):
                spec = futures[future]
                future.result()
                print(f"[seed-boot] {spec.name}", flush=True)

        seed_ready = wait_ready([seed_spec(spec) for spec in specs], 1800.0)
        database_rows: List[Dict[str, Any]] = []
        # Database creation is intentionally limited to a small parallel fanout:
        # JOB index creation and BenchBase loading are host/disk intensive.
        generic_specs = [spec for spec in specs if spec.benchmark in {"job", "tpch", "tpcc"}]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(
                    bootstrap_generic_database,
                    spec,
                    seed_ready[spec.name]["ip"],
                    root,
                ): spec
                for spec in generic_specs
            }
            for future in concurrent.futures.as_completed(futures):
                spec = futures[future]
                database_rows.append(future.result())
                print(f"[seed-database-ready] {spec.name}", flush=True)
        write_json(root / "seeded_rebuild_database_bootstrap.json", {"rows": database_rows})
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            futures = {
                executor.submit(graceful_poweroff, seed_spec(spec), seed_ready[spec.name]["ip"]): spec
                for spec in specs
            }
            for future in concurrent.futures.as_completed(futures):
                spec = futures[future]
                future.result()
                print(f"[seed-frozen] {spec.name}", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(launch_work, spec): spec for spec in specs}
        for future in concurrent.futures.as_completed(futures):
            spec = futures[future]
            future.result()
            print(f"[work-boot] {spec.name}", flush=True)

    ready = wait_ready(specs, 1800.0)
    manifest = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "prepared_image": str(prepared),
        "vm_count": len(specs),
        "all_ready": len(ready) == len(specs),
        "reset_mode": "immutable_per_vm_seed_plus_replaceable_work_overlay",
        "vms": [
            {
                **asdict(spec),
                "overlay": str(spec.overlay),
                "seed_overlay": str(seed_spec(spec).overlay),
                "runtime_mode": "direct_qemu_seeded",
                "tap": tap_name(spec),
                **ready[spec.name],
            }
            for spec in specs
        ],
    }
    write_json(root / "vm_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
