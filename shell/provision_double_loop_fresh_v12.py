#!/usr/bin/env python3
"""Provision four run-owned VMs without mutating any existing domain.

The script deliberately clones only inactive libvirt XML plus a read-only
prepared backing image.  It aborts before creating anything when a target name,
MAC, IP, overlay path, or explicitly pinned host CPU is already occupied.
"""

from __future__ import annotations

import os
import subprocess
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set


BASE_IMAGE = Path("/dev/shm/double_loop_vm_fast/prepared-flat.qcow2")
ARTIFACT_DIR = Path(
    "/home/anonymous/program/resource_adaptive/config/generated/"
    "double_loop_fresh_v12_20260717/infra"
)


@dataclass(frozen=True)
class Profile:
    scenario: str
    source_domain: str
    domain: str
    ip: str
    mac: str
    overlay: Path
    vcpu_pins: List[int]
    emulator_pin: int


PROFILES = [
    Profile(
        scenario="cpu",
        source_domain="double-loop-v4-cpu",
        domain="double-loop-fresh-v12-cpu",
        ip="192.0.2.15",
        mac="52:54:00:bf:8a:01",
        overlay=Path("/mnt/double_loop_fresh_v12_ram/cpu.qcow2"),
        vcpu_pins=list(range(480, 491)),
        emulator_pin=504,
    ),
    Profile(
        scenario="memory",
        source_domain="double-loop-v4-memory",
        domain="double-loop-fresh-v12-memory",
        ip="192.0.2.16",
        mac="52:54:00:bf:8a:02",
        overlay=Path("/mnt/double_loop_fresh_v12_ram/memory.qcow2"),
        vcpu_pins=list(range(491, 504)),
        emulator_pin=505,
    ),
    Profile(
        scenario="io",
        source_domain="double-loop-v4-io",
        domain="double-loop-fresh-v12-io",
        ip="192.0.2.17",
        mac="52:54:00:bf:8a:03",
        overlay=Path("/mnt/double_loop_fresh_v12_ram/io.qcow2"),
        vcpu_pins=list(range(256, 269)),
        emulator_pin=506,
    ),
    Profile(
        scenario="none",
        source_domain="double-loop-v4-none",
        domain="double-loop-fresh-v12-none",
        ip="192.0.2.18",
        mac="52:54:00:bf:8a:04",
        overlay=Path(
            "/disk3/resource_adaptive_vm_pools/double_loop_fresh_v12/none.qcow2"
        ),
        vcpu_pins=[*range(269, 300), 310],
        emulator_pin=507,
    ),
]


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def virsh(*args: str) -> str:
    return run("virsh", *args).stdout


def expand_cpuset(value: str) -> Set[int]:
    result: Set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lower, upper = part.split("-", 1)
            result.update(range(int(lower), int(upper) + 1))
        else:
            result.add(int(part))
    return result


def explicitly_pinned_cpus() -> Dict[int, str]:
    occupied: Dict[int, str] = {}
    domains = [name for name in virsh("list", "--name").splitlines() if name]
    for domain in domains:
        root = ET.fromstring(virsh("dumpxml", domain))
        for node in root.findall("./cputune/vcpupin"):
            cpuset = str(node.get("cpuset") or "")
            # An all-host mask is scheduling freedom, not an exclusive pin.
            if cpuset in {"0-511", "0-511,^0"}:
                continue
            for cpu in expand_cpuset(cpuset):
                occupied[cpu] = domain
        emulator = root.find("./cputune/emulatorpin")
        if emulator is not None:
            for cpu in expand_cpuset(str(emulator.get("cpuset") or "")):
                occupied[cpu] = f"{domain}:emulator"
    return occupied


def preflight() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("run this provisioner with sudo")
    if not BASE_IMAGE.is_file():
        raise FileNotFoundError(BASE_IMAGE)

    existing_domains = set(virsh("list", "--all", "--name").splitlines())
    network_root = ET.fromstring(virsh("net-dumpxml", "default"))
    existing_macs = {
        str(node.get("mac") or "").lower()
        for node in network_root.findall("./ip/dhcp/host")
    }
    existing_ips = {
        str(node.get("ip") or "")
        for node in network_root.findall("./ip/dhcp/host")
    }
    occupied_cpus = explicitly_pinned_cpus()

    requested_cpus: Set[int] = set()
    for profile in PROFILES:
        if profile.domain in existing_domains:
            raise RuntimeError(f"target domain already exists: {profile.domain}")
        if profile.overlay.exists():
            raise RuntimeError(f"target overlay already exists: {profile.overlay}")
        if profile.mac.lower() in existing_macs:
            raise RuntimeError(f"target MAC already reserved: {profile.mac}")
        if profile.ip in existing_ips:
            raise RuntimeError(f"target IP already reserved: {profile.ip}")
        ping = run("ping", "-c", "1", "-W", "1", profile.ip, check=False)
        if ping.returncode == 0:
            raise RuntimeError(f"target IP responds before provisioning: {profile.ip}")
        profile_cpus = set(profile.vcpu_pins) | {profile.emulator_pin}
        overlap = profile_cpus & requested_cpus
        if overlap:
            raise RuntimeError(f"new profiles overlap on CPUs: {sorted(overlap)}")
        requested_cpus.update(profile_cpus)
        conflicts = {
            cpu: occupied_cpus[cpu]
            for cpu in sorted(profile_cpus)
            if cpu in occupied_cpus
        }
        if conflicts:
            raise RuntimeError(
                f"host CPUs already explicitly occupied for {profile.domain}: {conflicts}"
            )


def create_overlay(profile: Profile) -> None:
    profile.overlay.parent.mkdir(parents=True, exist_ok=True)
    run(
        "qemu-img",
        "create",
        "-f",
        "qcow2",
        "-F",
        "qcow2",
        "-b",
        str(BASE_IMAGE),
        str(profile.overlay),
    )


def define_domain(profile: Profile) -> Path:
    root = ET.fromstring(virsh("dumpxml", "--inactive", profile.source_domain))
    name = root.find("./name")
    domain_uuid = root.find("./uuid")
    if name is None or domain_uuid is None:
        raise RuntimeError(f"source XML is missing name/uuid: {profile.source_domain}")
    name.text = profile.domain
    domain_uuid.text = str(uuid.uuid4())

    vcpu_nodes = root.findall("./cputune/vcpupin")
    if len(vcpu_nodes) != len(profile.vcpu_pins):
        raise RuntimeError(
            f"source vCPU count mismatch for {profile.domain}: "
            f"xml={len(vcpu_nodes)} requested={len(profile.vcpu_pins)}"
        )
    for node, cpu in zip(vcpu_nodes, profile.vcpu_pins):
        node.set("cpuset", str(cpu))
    emulator = root.find("./cputune/emulatorpin")
    if emulator is None:
        cputune = root.find("./cputune")
        if cputune is None:
            raise RuntimeError(f"source XML is missing cputune: {profile.source_domain}")
        emulator = ET.SubElement(cputune, "emulatorpin")
    emulator.set("cpuset", str(profile.emulator_pin))

    disk_source = root.find("./devices/disk[@device='disk']/source")
    disk = root.find("./devices/disk[@device='disk']")
    interface_mac = root.find("./devices/interface/mac")
    if disk is None or disk_source is None or interface_mac is None:
        raise RuntimeError(f"source XML is missing disk/MAC: {profile.source_domain}")
    disk_source.set("file", str(profile.overlay))
    driver = disk.find("./driver")
    if driver is None:
        raise RuntimeError(f"source XML is missing disk driver: {profile.source_domain}")
    driver.set("discard", "unmap")
    # A live external snapshot on the source domain can make dumpxml --inactive
    # expose an explicit old chain.  The new overlay header is authoritative;
    # never inherit any source-domain backingStore nodes.
    for backing_store in list(disk.findall("./backingStore")):
        disk.remove(backing_store)
    interface_mac.set("address", profile.mac)

    for seclabel in list(root.findall("./seclabel")):
        root.remove(seclabel)
    xml_path = ARTIFACT_DIR / f"{profile.domain}.xml"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(xml_path, encoding="unicode", xml_declaration=False)
    virsh("define", str(xml_path))
    return xml_path


def reserve_ip(profile: Profile) -> None:
    host_xml = (
        f"<host mac='{profile.mac}' name='{profile.domain}' ip='{profile.ip}'/>"
    )
    virsh(
        "net-update",
        "default",
        "add-last",
        "ip-dhcp-host",
        host_xml,
        "--live",
        "--config",
    )


def main() -> None:
    preflight()
    for profile in PROFILES:
        create_overlay(profile)
        xml_path = define_domain(profile)
        reserve_ip(profile)
        print(
            f"provisioned {profile.domain} ip={profile.ip} "
            f"overlay={profile.overlay} xml={xml_path}",
            flush=True,
        )
    for profile in PROFILES:
        virsh("start", profile.domain)
        print(f"started {profile.domain}", flush=True)


if __name__ == "__main__":
    main()
