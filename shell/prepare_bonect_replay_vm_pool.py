#!/usr/bin/env python3
"""Create libvirt replay workers from an offline VM disk.

The source VM must be shut off. The script creates one sparse immutable qcow2
base and linked qcow2 overlays, defines clones with unique UUID/MAC values, and
starts them. It never overwrites an existing image or libvirt domain.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Sequence
import uuid
import xml.etree.ElementTree as ET


def run_command(cmd: Sequence[str], *, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd),
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def virsh(*args: str) -> str:
    return run_command(["sudo", "virsh", *args]).stdout or ""


def source_disk(vm_name: str) -> Path:
    output = virsh("domblklist", vm_name, "--details")
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[1:3] == ["disk", "vda"]:
            return Path(fields[3])
    raise RuntimeError(f"could not find vda source for {vm_name}")


def domain_exists(name: str) -> bool:
    proc = subprocess.run(
        ["sudo", "virsh", "dominfo", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def clone_xml(source_xml: str, name: str, overlay: Path, mac: str) -> str:
    root = ET.fromstring(source_xml)
    name_node = root.find("name")
    if name_node is None:
        raise RuntimeError("source domain XML has no name")
    name_node.text = name
    uuid_node = root.find("uuid")
    if uuid_node is not None:
        uuid_node.text = str(uuid.uuid4())
    for disk in root.findall("./devices/disk"):
        target = disk.find("target")
        if target is not None and target.get("dev") == "vda":
            source = disk.find("source")
            if source is None:
                raise RuntimeError("source vda has no source element")
            source.set("file", str(overlay))
            for backing in list(disk.findall("backingStore")):
                disk.remove(backing)
    interface = root.find("./devices/interface")
    if interface is None:
        raise RuntimeError("source domain XML has no network interface")
    mac_node = interface.find("mac")
    if mac_node is None:
        mac_node = ET.SubElement(interface, "mac")
    mac_node.set("address", mac)
    target_node = interface.find("target")
    if target_node is not None:
        interface.remove(target_node)
    for node in root.findall(".//*[@index]"):
        node.attrib.pop("index", None)
    for seclabel in list(root.findall("seclabel")):
        root.remove(seclabel)
    return ET.tostring(root, encoding="unicode")


def deterministic_mac(index: int) -> str:
    return f"52:54:00:bf:87:{index:02x}"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def prepare(args: argparse.Namespace) -> int:
    state = virsh("domstate", args.source_vm).strip().lower()
    if state != "shut off":
        raise RuntimeError(
            f"source VM {args.source_vm} must be shut off, current state={state!r}"
        )
    storage_dir = Path(args.storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    base_path = storage_dir / f"{args.source_vm}-replay-base.qcow2"
    sparsify_tmp_dir = storage_dir / "tmp"
    sparsify_tmp_dir.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "sudo",
            "setfacl",
            "-m",
            f"g:{args.libvirt_gid}:x",
            str(storage_dir.parent),
        ]
    )
    run_command(["sudo", "chgrp", str(args.libvirt_gid), str(storage_dir)])
    run_command(["sudo", "chmod", "0770", str(storage_dir)])
    source_path = source_disk(args.source_vm)
    names = [f"{args.name_prefix}{index}" for index in range(1, args.count + 1)]
    for name in names:
        if domain_exists(name) and not args.resume:
            raise RuntimeError(f"refusing to replace existing domain={name}")
        overlay = storage_dir / f"{name}.qcow2"
        if overlay.exists() and not args.resume:
            raise RuntimeError(f"refusing to replace existing overlay={overlay}")
    if not base_path.exists():
        print(f"converting offline source {source_path} -> {base_path}", flush=True)
        if args.copy_mode == "virt-sparsify":
            run_command(
                [
                    "sudo",
                    "env",
                    f"TMPDIR={sparsify_tmp_dir}",
                    "virt-sparsify",
                    "--compress",
                    "--check-tmpdir",
                    "ignore",
                    str(source_path),
                    str(base_path),
                ],
                capture=False,
            )
        else:
            run_command(
                [
                    "sudo",
                    "qemu-img",
                    "convert",
                    "-p",
                    "-O",
                    "qcow2",
                    "-S",
                    "4k",
                    str(source_path),
                    str(base_path),
                ],
                capture=False,
            )
    run_command(["sudo", "chgrp", str(args.libvirt_gid), str(base_path)])
    run_command(["sudo", "chmod", "0440", str(base_path)])
    source_xml = virsh("dumpxml", args.source_vm, "--inactive")
    workers: List[Dict[str, Any]] = []
    for index, name in enumerate(names, start=1):
        overlay = storage_dir / f"{name}.qcow2"
        exists_domain = domain_exists(name)
        exists_overlay = overlay.exists()
        if exists_domain != exists_overlay:
            raise RuntimeError(
                f"partial clone state for {name}: domain={exists_domain}, overlay={exists_overlay}"
            )
        if exists_domain and not args.resume:
            raise RuntimeError(f"refusing to replace existing domain={name}")
        if not exists_overlay:
            run_command(
                [
                    "sudo",
                    "qemu-img",
                    "create",
                    "-f",
                    "qcow2",
                    "-F",
                    "qcow2",
                    "-b",
                    str(base_path),
                    str(overlay),
                ]
            )
        run_command(["sudo", "chgrp", str(args.libvirt_gid), str(overlay)])
        run_command(["sudo", "chmod", "0660", str(overlay)])
        mac = deterministic_mac(index)
        if not exists_domain:
            xml_text = clone_xml(source_xml, name, overlay, mac)
            with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as handle:
                handle.write(xml_text)
                xml_path = Path(handle.name)
            try:
                virsh("define", str(xml_path))
            finally:
                xml_path.unlink(missing_ok=True)
        if virsh("domstate", name).strip().lower() == "shut off":
            virsh("start", name)
        workers.append(
            {
                "worker_id": name,
                "vm_name": name,
                "mac": mac,
                "overlay": str(overlay),
                "backing_base": str(base_path),
                "disk_isolation_id": "",
                "disk_isolation_verified": False,
                "scenarios": ["cpu", "memory", "none"],
            }
        )
        print(f"defined and started {name} mac={mac}", flush=True)
    manifest = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "source_vm": args.source_vm,
        "source_disk": str(source_path),
        "base_image": str(base_path),
        "workers": workers,
    }
    write_json(storage_dir / "vm_pool_manifest.json", manifest)
    print(f"wrote {storage_dir / 'vm_pool_manifest.json'}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-vm", default="artifact-ubuntu20")
    parser.add_argument("--storage-dir", required=True)
    parser.add_argument("--name-prefix", default="bonect-replay-v87-")
    parser.add_argument("--count", type=int, default=4)
    parser.add_argument(
        "--copy-mode",
        choices=["virt-sparsify", "qemu-img"],
        default="virt-sparsify",
    )
    parser.add_argument("--libvirt-gid", type=int, default=108)
    parser.add_argument("--resume", action="store_true")
    return prepare(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
