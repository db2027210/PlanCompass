#!/usr/bin/env python3
"""Provision the isolated 18-run formal experiment matrix without reusing workers."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = Path("/disk2/resource_adaptive_experiment_matrix_20260718")
DEFAULT_PREPARED = DEFAULT_ROOT / "vm_bases/prepared-v15.qcow2"
SUDO_MIN_TIMEOUT_S = 300.0


@dataclass(frozen=True)
class VmSpec:
    exp_id: str
    repeat_id: int
    benchmark: str
    target: str
    name: str
    mac: str
    cpu: int
    memory_mib: int
    read_mb: float
    write_mb: float
    overlay_root: str = "/disk2/resource_adaptive_experiment_matrix_20260718/vm_overlays"

    @property
    def overlay(self) -> Path:
        return Path(self.overlay_root) / f"{self.name}.qcow2"


def vm_specs() -> List[VmSpec]:
    rows = [
        ("A13", 1, "hybench_ap", "memory", "ra-matrix-v15-hyap-mem-r1", 13, 576, 800, 800),
        ("A14", 1, "job", "memory", "ra-matrix-v15-job-mem-r1", 13, 608, 800, 800),
        ("A15", 1, "tpch", "memory", "ra-matrix-v15-tpch-mem-r1", 13, 960, 800, 800),
        ("A16", 1, "hybench_tp", "cpu", "ra-matrix-v15-hytp-cpu-r1", 1, 8192, 800, 800),
        ("A17", 1, "tpcc", "cpu", "ra-matrix-v15-tpcc-cpu-r1", 1, 8192, 800, 800),
        ("A18", 1, "hybench_xp", "io", "ra-matrix-v15-hyxp-io-r1", 13, 8192, 4.5, 0.1),
        ("E13", 1, "hybench_ap", "none", "ra-matrix-v15-hyap-none-r1", 16, 12288, 800, 800),
        ("E14", 1, "job", "none", "ra-matrix-v15-job-none-r1", 16, 12288, 800, 800),
        ("E15", 1, "tpch", "none", "ra-matrix-v15-tpch-none-r1", 16, 12288, 800, 800),
        ("E16", 1, "hybench_tp", "none", "ra-matrix-v15-hytp-none-r1", 16, 12288, 800, 800),
        ("E17", 1, "tpcc", "none", "ra-matrix-v15-tpcc-none-r1", 16, 12288, 800, 800),
        ("E18", 1, "hybench_xp", "none", "ra-matrix-v15-hyxp-none-r1", 16, 12288, 800, 800),
        ("B4", 1, "hybench_tp", "cpu", "ra-matrix-v15-b4-cpu-r1", 1, 8192, 800, 800),
        ("B4", 2, "hybench_tp", "cpu", "ra-matrix-v15-b4-cpu-r2", 1, 8192, 800, 800),
        ("B4", 3, "hybench_tp", "cpu", "ra-matrix-v15-b4-cpu-r3", 1, 8192, 800, 800),
        ("B5", 1, "hybench_ap", "memory", "ra-matrix-v15-b5-mem-r1", 13, 576, 800, 800),
        ("B5", 2, "hybench_ap", "memory", "ra-matrix-v15-b5-mem-r2", 13, 576, 800, 800),
        ("B5", 3, "hybench_ap", "memory", "ra-matrix-v15-b5-mem-r3", 13, 576, 800, 800),
        ("B6", 1, "hybench_xp", "io", "ra-matrix-v15-b6-io-r1", 13, 8192, 4.5, 0.1),
        ("B6", 2, "hybench_xp", "io", "ra-matrix-v15-b6-io-r2", 13, 8192, 4.5, 0.1),
        ("B6", 3, "hybench_xp", "io", "ra-matrix-v15-b6-io-r3", 13, 8192, 4.5, 0.1),
    ]
    result: List[VmSpec] = []
    for index, row in enumerate(rows, start=1):
        exp_id, repeat_id, benchmark, target, name, cpu, memory_mib, read_mb, write_mb = row
        # B4/B5/B6 each need three total observations including their matching
        # A anchor (A16/A13/A18).  Preserve the historical MAC indexes while
        # omitting the redundant B repeat-1 guests from the formal matrix.
        if exp_id in {"B4", "B5", "B6"} and repeat_id == 1:
            continue
        overlay_root = (
            "/disk3/resource_adaptive_experiment_matrix_20260718/vm_overlays"
            if exp_id == "B6" and repeat_id in {1, 3}
            else "/disk2/resource_adaptive_experiment_matrix_20260718/vm_overlays"
        )
        result.append(
            VmSpec(
                exp_id=exp_id,
                repeat_id=repeat_id,
                benchmark=benchmark,
                target=target,
                name=name,
                mac=f"52:54:00:bf:8d:{index:02x}",
                cpu=cpu,
                memory_mib=memory_mib,
                read_mb=read_mb,
                write_mb=write_mb,
                overlay_root=overlay_root,
            )
        )
    return result


def run(command: List[str], *, timeout: float = 300.0, check: bool = True) -> subprocess.CompletedProcess[str]:
    # A congested sudo/PAM control plane has taken more than two minutes even
    # for read-only commands.  Killing a timed-out sudo child from its original
    # unprivileged parent raises PermissionError and strands the VM worker.
    # Keep ordinary command timeouts unchanged, but never give a sudo command a
    # deadline shorter than the observed recovery envelope.
    effective_timeout = (
        max(float(timeout), SUDO_MIN_TIMEOUT_S)
        if command and command[0] == "sudo"
        else float(timeout)
    )
    result = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=effective_timeout,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed exit={result.returncode}: {' '.join(command)}\n{result.stdout}")
    return result


def domain_xml_exists(name: str) -> bool:
    result = run(
        ["sudo", "-n", "test", "-e", f"/etc/libvirt/qemu/{name}.xml"],
        timeout=10,
        check=False,
    )
    return result.returncode == 0


def domain_running(name: str) -> bool:
    """Check the exact namespaced runtime without asking a busy libvirtd."""
    pid_path = f"/run/libvirt/qemu/{name}.pid"
    result = run(
        [
            "sudo",
            "-n",
            "sh",
            "-c",
            f"test -r {pid_path} && kill -0 $(cat {pid_path})",
        ],
        timeout=10,
        check=False,
    )
    return result.returncode == 0


def dhcp_rows() -> List[Dict[str, Any]]:
    # The dnsmasq status file is world-readable on the experiment host.  A
    # privileged cat here used to strand workers whenever sudo/PAM was busy:
    # after timeout an unprivileged Python parent cannot SIGKILL its root
    # child.  Keep this readiness lookup entirely unprivileged.
    status_path = Path("/var/lib/libvirt/dnsmasq/virbr0.status")
    try:
        raw = status_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot read DHCP status without privilege: {status_path}: {exc}") from exc
    value = json.loads(raw)
    return list(value) if isinstance(value, list) else []


def audit(
    specs: List[VmSpec],
    prepared: Path,
    adopt_pilot: bool,
    adopt_existing: bool,
) -> Dict[str, Any]:
    if not prepared.is_file():
        raise FileNotFoundError(f"prepared backing image does not exist: {prepared}")
    leases = dhcp_rows()
    lease_macs = {str(row.get("mac-address") or "").lower() for row in leases}
    errors: List[str] = []
    adopted: List[str] = []
    for index, spec in enumerate(specs):
        running = domain_running(spec.name)
        expected_existing = (adopt_pilot and index == 0) or (
            adopt_existing and running
        )
        domain_exists = domain_xml_exists(spec.name)
        overlay_exists = spec.overlay.exists()
        if expected_existing:
            if not running or not overlay_exists:
                errors.append(f"adoption missing running domain or overlay: {spec.name}")
            else:
                adopted.append(spec.name)
        elif domain_exists:
            errors.append(
                f"collision for {spec.name}: domain={domain_exists}, overlay={overlay_exists}"
            )
        # A prior interrupted submission may have created only its disposable
        # overlay and DHCP lease.  In resume mode both belong to this exact
        # namespaced spec and are safe to reuse; a running/domain collision is
        # still rejected above unless explicitly adopted.
        elif overlay_exists and not adopt_existing:
            errors.append(
                f"collision for {spec.name}: domain={domain_exists}, overlay={overlay_exists}"
            )
        if not expected_existing and spec.mac.lower() in lease_macs and not adopt_existing:
            errors.append(f"MAC already has DHCP lease: {spec.mac}")
    if errors:
        raise RuntimeError("pre-launch isolation audit failed:\n" + "\n".join(errors))
    return {
        "prepared_image": str(prepared),
        "spec_count": len(specs),
        "adopt_pilot": adopt_pilot,
        "adopt_existing": adopt_existing,
        "existing_pilot": specs[0].name if adopt_pilot else None,
        "adopted_running_domains": adopted,
        "collisions": [],
        "external_domains_modified": False,
        "external_containers_modified": False,
    }


def provision_one(spec: VmSpec, prepared: Path) -> Dict[str, Any]:
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
            ]
        )
    else:
        info = run(
            ["sudo", "-n", "qemu-img", "info", "--output=json", str(spec.overlay)]
        )
        backing = str(json.loads(info.stdout).get("full-backing-filename") or "")
        if Path(backing).resolve() != prepared.resolve():
            raise RuntimeError(
                f"resume overlay has unexpected backing image: {spec.overlay}: {backing}"
            )
    read_bps = spec.read_mb * 1024 * 1024
    write_bps = spec.write_mb * 1024 * 1024
    disk = (
        f"path={spec.overlay},format=qcow2,bus=virtio,cache=none,discard=unmap,"
        f"iotune.read_bytes_sec={read_bps},iotune.write_bytes_sec={write_bps}"
    )
    command = [
        "sudo",
        "-n",
        "virt-install",
        "--connect",
        "qemu:///system",
        "--name",
        spec.name,
        "--memory",
        str(spec.memory_mib),
        "--vcpus",
        f"{spec.cpu},sockets=1,cores={spec.cpu},threads=1",
        "--cpu",
        "host-passthrough",
        "--disk",
        disk,
        "--network",
        f"bridge=virbr0,model=virtio,mac={spec.mac}",
        "--os-variant",
        "ubuntu20.04",
        "--import",
        "--graphics",
        "none",
        # These experiment domains are isolated, disposable overlays.  The
        # host AppArmor driver serializes policy generation and can block for
        # minutes when several large backing-chain domains are submitted at
        # once.  Avoid that dynamic label for this dedicated namespace; disk
        # paths remain explicit and no external domain is modified.
        "--seclabel",
        "type=none",
        "--noautoconsole",
        "--check",
        "path_in_use=off",
        "--wait",
        "0",
    ]
    output = run(command, timeout=900).stdout
    return {"name": spec.name, "status": "submitted", "virt_install_output": output}


def ip_for_mac(mac: str) -> str:
    matches = [
        row
        for row in dhcp_rows()
        if str(row.get("mac-address") or "").lower() == mac.lower()
    ]
    for row in sorted(
        matches,
        key=lambda item: int(item.get("expiry-time") or 0),
        reverse=True,
    ):
        if row.get("ip-address"):
            return str(row["ip-address"])
    return ""


def wait_ready(specs: List[VmSpec], timeout_s: float) -> Dict[str, Dict[str, Any]]:
    pending = {spec.name: spec for spec in specs}
    ready: Dict[str, Dict[str, Any]] = {}
    deadline = time.time() + timeout_s
    next_progress = 0.0
    while pending and time.time() < deadline:
        for name, spec in list(pending.items()):
            ip = ip_for_mac(spec.mac)
            if not ip:
                continue
            ssh = run(
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "ConnectTimeout=3",
                    f"anonymous@{ip}",
                    "sudo -n true && systemctl is-active resource-adaptive-postgresql",
                ],
                timeout=10,
                check=False,
            )
            if ssh.returncode != 0 or "active" not in ssh.stdout:
                continue
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
            if psql.returncode != 0:
                continue
            ready[name] = {
                "ip": ip,
                "ssh_sudo": True,
                "postgres": psql.stdout.strip(),
                "ready_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            pending.pop(name)
            print(f"[ready] {name} ip={ip} postgres={psql.stdout.strip()}", flush=True)
        if pending and time.time() >= next_progress:
            print(
                f"[ready] completed={len(ready)}/{len(specs)} pending={len(pending)}",
                flush=True,
            )
            next_progress = time.time() + 30.0
        if pending:
            time.sleep(5.0)
    if pending:
        raise RuntimeError("VM readiness timeout: " + ",".join(sorted(pending)))
    return ready


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--prepared", type=Path, default=DEFAULT_PREPARED)
    # Domain definition/start is intentionally serial: libvirt/AppArmor is the
    # bottleneck here.  Once created, all VMs and all benchmark runs execute in
    # parallel.
    parser.add_argument("--launch-workers", type=int, default=1)
    parser.add_argument("--ready-timeout-s", type=float, default=1800.0)
    parser.add_argument("--adopt-pilot", action="store_true")
    parser.add_argument(
        "--adopt-existing",
        action="store_true",
        help="resume exact running ra-matrix-v15 domains and disposable overlays",
    )
    args = parser.parse_args()
    specs = vm_specs()
    audit_row = audit(
        specs,
        args.prepared.resolve(),
        args.adopt_pilot,
        args.adopt_existing,
    )
    write_json(args.root / "prelaunch_isolation_audit.json", audit_row)
    to_create = [spec for spec in specs if not domain_running(spec.name)]
    submitted: List[Dict[str, Any]] = [
        {"name": spec.name, "status": "submitted", "adopted_running": True}
        for spec in specs
        if domain_running(spec.name)
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.launch_workers)) as executor:
        futures = {
            executor.submit(provision_one, spec, args.prepared.resolve()): spec
            for spec in to_create
        }
        for future in concurrent.futures.as_completed(futures):
            spec = futures[future]
            try:
                row = future.result()
                submitted.append(row)
                print(f"[submitted] {spec.name}", flush=True)
            except Exception as exc:
                row = {"name": spec.name, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
                submitted.append(row)
                print(f"[submit-failed] {spec.name}: {row['error']}", flush=True)
    failed = [row for row in submitted if row["status"] != "submitted"]
    write_json(args.root / "vm_submission.json", {"rows": submitted})
    if failed:
        raise RuntimeError(f"{len(failed)} VM submissions failed; see vm_submission.json")
    ready = wait_ready(specs, args.ready_timeout_s)
    manifest = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "prepared_image": str(args.prepared.resolve()),
        "vm_count": len(specs),
        "all_ready": len(ready) == len(specs),
        "vms": [
            {**asdict(spec), "overlay": str(spec.overlay), **ready[spec.name]}
            for spec in specs
        ],
    }
    write_json(args.root / "vm_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
