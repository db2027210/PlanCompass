#!/usr/bin/env python3
"""Resume the formal matrix with direct KVM guests when libvirtd is congested.

Only the dedicated ``ra-matrix-v15-*`` namespace, its disposable qcow2
overlays, and taps named ``ra8dXX`` are created.  Existing external libvirt
domains and containers are inspected but never changed.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .provision_double_loop_matrix_vms import (
        DEFAULT_PREPARED,
        DEFAULT_ROOT,
        VmSpec,
        dhcp_rows,
        domain_running,
        run,
        vm_specs,
        write_json,
    )
except ImportError:
    from provision_double_loop_matrix_vms import (
        DEFAULT_PREPARED,
        DEFAULT_ROOT,
        VmSpec,
        dhcp_rows,
        domain_running,
        run,
        vm_specs,
        write_json,
    )


DIRECT_ROOT = DEFAULT_ROOT / "direct_qemu"

# Audited from the dedicated virbr0 lease table on 2026-07-18.  These entries
# belong only to the A/E matrix MAC namespace and remain stable across seeded
# restarts.  Using them avoids a privileged lease-file read in every five-second
# readiness poll.  B repeat MACs intentionally remain dynamic until first boot.
STATIC_MATRIX_IPS = {
    "52:54:00:bf:8d:01": "192.0.2.19",
    "52:54:00:bf:8d:02": "192.0.2.20",
    "52:54:00:bf:8d:03": "192.0.2.21",
    "52:54:00:bf:8d:04": "192.0.2.22",
    "52:54:00:bf:8d:05": "192.0.2.23",
    "52:54:00:bf:8d:06": "192.0.2.24",
    "52:54:00:bf:8d:07": "192.0.2.25",
    "52:54:00:bf:8d:08": "192.0.2.26",
    "52:54:00:bf:8d:09": "192.0.2.27",
    "52:54:00:bf:8d:0a": "192.0.2.28",
    "52:54:00:bf:8d:0b": "192.0.2.29",
    "52:54:00:bf:8d:0c": "192.0.2.30",
}


def direct_pid_path(spec: VmSpec) -> Path:
    return DIRECT_ROOT / f"{spec.name}.pid"


def read_root_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except PermissionError:
        pass
    return run(["sudo", "-n", "cat", str(path)], timeout=10).stdout.strip()


def direct_running(spec: VmSpec) -> bool:
    path = direct_pid_path(spec)
    if not path.exists():
        return False
    try:
        pid = int(read_root_file(path))
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="replace")
        state_line = next((line for line in status.splitlines() if line.startswith("State:")), "")
        state = state_line.split()[1].upper() if len(state_line.split()) >= 2 else ""
        if state in {"Z", "X"}:
            return False
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", errors="replace"
        )
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return False
    return spec.name in command and str(spec.overlay) in command


def runtime_mode(spec: VmSpec) -> Optional[str]:
    if direct_running(spec):
        return "direct_qemu"
    # Avoid asking libvirtd or reading /run/libvirt while its AppArmor cleanup
    # queue is congested.  The qemu command line is authoritative for a live
    # libvirt guest and is readable without privilege.
    process_list = subprocess.run(
        ["ps", "-eo", "cmd"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        check=False,
    ).stdout
    if f"guest={spec.name}," in process_list:
        return "libvirt"
    return None


def tap_name(spec: VmSpec) -> str:
    return "ra8d" + spec.mac.rsplit(":", 1)[-1].lower()


def ensure_overlay(spec: VmSpec, prepared: Path) -> None:
    run(["sudo", "-n", "install", "-d", "-m", "0755", spec.overlay_root])
    if not spec.overlay.exists():
        run(
            [
                "sudo",
                "-n",
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-F",
                "qcow2",
                "-b",
                str(prepared),
                str(spec.overlay),
            ],
            timeout=120,
        )
    info = json.loads(
        run(
            ["sudo", "-n", "qemu-img", "info", "--output=json", str(spec.overlay)],
            timeout=120,
        ).stdout
    )
    backing = Path(str(info.get("full-backing-filename") or "")).resolve()
    if backing != prepared.resolve():
        raise RuntimeError(
            f"unexpected backing image for {spec.name}: {backing} != {prepared.resolve()}"
        )


def launch_direct(spec: VmSpec, prepared: Path) -> Dict[str, Any]:
    if runtime_mode(spec):
        return {"name": spec.name, "status": "adopted", "runtime_mode": runtime_mode(spec)}
    ensure_overlay(spec, prepared)
    tap = tap_name(spec)
    tap_check = run(["sudo", "-n", "ip", "link", "show", "dev", tap], check=False)
    if tap_check.returncode != 0:
        run(["sudo", "-n", "ip", "tuntap", "add", "dev", tap, "mode", "tap"])
        run(["sudo", "-n", "ip", "link", "set", "dev", tap, "master", "virbr0"])
    run(["sudo", "-n", "ip", "link", "set", "dev", tap, "up"])
    read_bps = int(float(spec.read_mb) * 1024 * 1024)
    write_bps = int(float(spec.write_mb) * 1024 * 1024)
    index = int(spec.mac.rsplit(":", 1)[-1], 16)
    pid_path = direct_pid_path(spec)
    qmp_path = DIRECT_ROOT / f"{spec.name}.qmp"
    serial_path = DIRECT_ROOT / f"{spec.name}.serial.log"
    # A stopped direct guest leaves its Unix listener path behind.  It belongs
    # to this exact namespaced VM and must be cleared before a seeded restart.
    run(["sudo", "-n", "unlink", str(qmp_path)], check=False)
    backing_chain = run(
        [
            "sudo",
            "-n",
            "qemu-img",
            "info",
            "--backing-chain",
            "--output=json",
            str(spec.overlay),
        ],
        timeout=30,
    ).stdout
    # O_DIRECT cannot open a tmpfs backing image.  A RAM-resident, read-only
    # prepared image therefore requires QEMU writeback caching for the small
    # disposable disk layer.  Normal disk-backed workers retain cache=none.
    cache_mode = "writeback" if '"filename": "/dev/shm/' in backing_chain else "none"
    drive = (
        f"file={spec.overlay},if=none,id=drive0,format=qcow2,cache={cache_mode},"
        f"discard=unmap,throttling.bps-read={read_bps},"
        f"throttling.bps-write={write_bps}"
    )
    command = [
        "sudo",
        "-n",
        "qemu-system-x86_64",
        "-name",
        spec.name,
        "-machine",
        "pc-q35-4.2,accel=kvm",
        "-cpu",
        "host",
        "-m",
        str(spec.memory_mib),
        "-smp",
        f"{spec.cpu},sockets=1,cores={spec.cpu},threads=1",
        "-no-user-config",
        "-nodefaults",
        "-display",
        "none",
        "-daemonize",
        "-pidfile",
        str(pid_path),
        "-uuid",
        f"52000000-bf8d-4000-8000-{index:012d}",
        "-boot",
        "strict=on",
        "-device",
        "pcie-root-port,port=0x8,chassis=1,id=pci.1,bus=pcie.0,multifunction=on,addr=0x1",
        "-device",
        "pcie-root-port,port=0x9,chassis=2,id=pci.2,bus=pcie.0,addr=0x1.0x1",
        "-device",
        "pcie-root-port,port=0xa,chassis=3,id=pci.3,bus=pcie.0,addr=0x1.0x2",
        "-drive",
        drive,
        "-device",
        "virtio-blk-pci,drive=drive0,bus=pci.3,addr=0x0,bootindex=1",
        "-netdev",
        f"tap,id=net0,ifname={tap},script=no,downscript=no",
        "-device",
        f"virtio-net-pci,netdev=net0,mac={spec.mac},bus=pci.1,addr=0x0",
        "-object",
        "rng-random,id=objrng,filename=/dev/urandom",
        "-device",
        "virtio-rng-pci,rng=objrng",
        "-qmp",
        f"unix:{qmp_path},server,nowait",
        "-serial",
        f"file:{serial_path}",
    ]
    run(command, timeout=120)
    run(["sudo", "-n", "chmod", "0644", str(pid_path)])
    if not direct_running(spec):
        raise RuntimeError(f"direct QEMU exited immediately: {spec.name}")
    return {
        "name": spec.name,
        "status": "submitted",
        "runtime_mode": "direct_qemu",
        "tap": tap,
        "pid": int(read_root_file(pid_path)),
        "read_bps": read_bps,
        "write_bps": write_bps,
        "cache_mode": cache_mode,
    }


def candidate_ips(spec: VmSpec, leases: List[Dict[str, Any]]) -> List[str]:
    static_ip = STATIC_MATRIX_IPS.get(spec.mac.lower())
    matches = [
        row
        for row in leases
        if str(row.get("mac-address") or "").lower() == spec.mac.lower()
    ]
    matches.sort(key=lambda row: int(row.get("expiry-time") or 0), reverse=True)
    dynamic_ips = [
        str(row.get("ip-address") or "")
        for row in matches
        if row.get("ip-address")
    ]
    return list(dict.fromkeys(([static_ip] if static_ip else []) + dynamic_ips))


def probe_ready(spec: VmSpec, ips: List[str]) -> Optional[Dict[str, Any]]:
    for ip in ips:
        try:
            ssh = run(
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "UserKnownHostsFile=/dev/null",
                    "-o",
                    "ConnectTimeout=3",
                    f"anonymous@{ip}",
                    (
                        "sudo -n true && "
                        "(systemctl is-active resource-adaptive-postgresql || "
                        "systemctl is-active double-loop-postgresql || "
                        "systemctl is-active postgresql) && "
                        "awk '/^MemTotal:/ {print $2}' /proc/meminfo"
                    ),
                ],
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired:
            continue
        if ssh.returncode != 0 or "active" not in ssh.stdout:
            continue
        mem_total_lines = [
            line.strip()
            for line in ssh.stdout.splitlines()
            if line.strip().isdigit()
        ]
        if not mem_total_lines:
            continue
        memory_capacity_gib = int(mem_total_lines[-1]) / (1024.0 * 1024.0)
        try:
            psql = run(
                [
                    "psql",
                    f"postgresql://postgres:CHANGE_ME@{ip}:25432/postgres",
                    "-X",
                    "-Atc",
                    "select current_setting('server_version')",
                ],
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired:
            continue
        if psql.returncode == 0:
            return {
                "ip": ip,
                "ssh_sudo": True,
                "postgres": psql.stdout.strip(),
                "memory_capacity_gib": memory_capacity_gib,
                "ready_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
    return None


def wait_ready(specs: List[VmSpec], timeout_s: float) -> Dict[str, Dict[str, Any]]:
    pending = {spec.name: spec for spec in specs}
    ready: Dict[str, Dict[str, Any]] = {}
    deadline = time.time() + timeout_s
    next_progress = 0.0
    while pending and time.time() < deadline:
        # Read the shared dnsmasq lease file once.  Running one sudo/cat per
        # readiness thread can saturate sudo/PAM under 21-way startup and a
        # timed-out unprivileged parent cannot SIGKILL its root child.
        leases: List[Dict[str, Any]] = []
        needs_dynamic_lease = any(
            spec.mac.lower() not in STATIC_MATRIX_IPS
            for spec in pending.values()
        )
        if needs_dynamic_lease:
            try:
                leases = dhcp_rows()
            except (RuntimeError, subprocess.SubprocessError) as exc:
                print(f"[ready] DHCP lease read retry: {type(exc).__name__}: {exc}", flush=True)
                time.sleep(5.0)
                continue
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(pending))) as executor:
            futures = {
                executor.submit(probe_ready, spec, candidate_ips(spec, leases)): name
                for name, spec in pending.items()
            }
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                row = future.result()
                if row is not None:
                    ready[name] = row
        for name in ready:
            pending.pop(name, None)
        if time.time() >= next_progress:
            print(f"[ready] completed={len(ready)}/{len(specs)} pending={len(pending)}", flush=True)
            next_progress = time.time() + 30.0
        if pending:
            time.sleep(5.0)
    if pending:
        raise RuntimeError("VM readiness timeout: " + ",".join(sorted(pending)))
    return ready


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--prepared", type=Path, default=DEFAULT_PREPARED)
    parser.add_argument("--launch-workers", type=int, default=8)
    parser.add_argument("--ready-timeout-s", type=float, default=1800.0)
    args = parser.parse_args()
    specs = vm_specs()
    args.root.mkdir(parents=True, exist_ok=True)
    run(["sudo", "-n", "install", "-d", "-m", "0755", str(DIRECT_ROOT)])

    # Exact namespace audit: existing target guests are adopted, while MAC/tap
    # collisions outside an adopted target remain fatal.
    lease_macs = {str(row.get("mac-address") or "").lower() for row in dhcp_rows()}
    audit_rows: List[Dict[str, Any]] = []
    for spec in specs:
        mode = runtime_mode(spec)
        tap_exists = run(
            ["sudo", "-n", "ip", "link", "show", "dev", tap_name(spec)],
            check=False,
        ).returncode == 0
        if tap_exists and mode != "direct_qemu":
            raise RuntimeError(f"tap collision outside adopted direct guest: {tap_name(spec)}")
        if spec.mac.lower() in lease_macs and mode is None and not spec.overlay.exists():
            raise RuntimeError(f"unexpected MAC collision for uncreated spec: {spec.mac}")
        audit_rows.append(
            {
                "name": spec.name,
                "mac": spec.mac,
                "runtime_mode_before": mode,
                "overlay_exists": spec.overlay.exists(),
                "tap_exists": tap_exists,
            }
        )
    write_json(
        args.root / "direct_prelaunch_isolation_audit.json",
        {
            "rows": audit_rows,
            "external_domains_modified": False,
            "external_containers_modified": False,
        },
    )

    submissions: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.launch_workers)) as executor:
        futures = {
            executor.submit(launch_direct, spec, args.prepared.resolve()): spec
            for spec in specs
        }
        for future in concurrent.futures.as_completed(futures):
            spec = futures[future]
            row = future.result()
            submissions.append(row)
            print(f"[{row['status']}] {spec.name} mode={row['runtime_mode']}", flush=True)
    write_json(args.root / "vm_submission.json", {"rows": submissions})

    ready = wait_ready(specs, args.ready_timeout_s)
    manifest = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "prepared_image": str(args.prepared.resolve()),
        "vm_count": len(specs),
        "all_ready": len(ready) == len(specs),
        "vms": [
            {
                **asdict(spec),
                "overlay": str(spec.overlay),
                "runtime_mode": runtime_mode(spec),
                **ready[spec.name],
            }
            for spec in specs
        ],
    }
    write_json(args.root / "vm_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
