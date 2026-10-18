#!/usr/bin/env python3
"""Set unique machine IDs in shut-off Bonect replay VM overlays."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import time
from typing import List, Sequence


def run(cmd: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd),
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def virsh(*args: str) -> str:
    return run(["virsh", *args]).stdout.strip()


def update_one(vm_name: str, overlay: Path, nbd: str, mount_root: Path) -> None:
    state = virsh("domstate", vm_name).lower()
    if state != "shut off":
        raise RuntimeError(f"{vm_name} must be shut off, state={state!r}")
    if not overlay.exists():
        raise FileNotFoundError(overlay)
    machine_id = virsh("domuuid", vm_name).replace("-", "").lower()
    if len(machine_id) != 32:
        raise RuntimeError(f"invalid libvirt UUID for {vm_name}: {machine_id!r}")

    connected = False
    activated = False
    mounted = False
    mount_root.mkdir(parents=True, exist_ok=True)
    try:
        run(["qemu-nbd", "--connect", nbd, str(overlay)])
        connected = True
        run(["partprobe", nbd])
        run(["udevadm", "settle"])
        pv_path = f"{nbd}p3"
        deadline = time.time() + 30.0
        while not Path(pv_path).exists() and time.time() < deadline:
            time.sleep(0.5)
        if not Path(pv_path).exists():
            raise RuntimeError(f"LVM partition did not appear: {pv_path}")
        run(["vgscan", "--cache", "--mknodes"])
        run(["vgchange", "-ay", "ubuntu-vg"])
        activated = True
        lv_path = Path("/dev/mapper/ubuntu--vg-ubuntu--lv")
        if not lv_path.exists():
            raise RuntimeError(f"root LV did not appear for {vm_name}")
        run(["mount", str(lv_path), str(mount_root)])
        mounted = True
        machine_id_path = mount_root / "etc/machine-id"
        previous = machine_id_path.read_text(encoding="utf-8").strip()
        machine_id_path.write_text(machine_id + "\n", encoding="utf-8")
        run(["sync"])
        print(
            f"{vm_name}: machine-id {previous} -> {machine_id}",
            flush=True,
        )
    finally:
        if mounted:
            run(["umount", str(mount_root)], check=False)
        if activated:
            run(["vgchange", "-an", "ubuntu-vg"], check=False)
        if connected:
            run(["qemu-nbd", "--disconnect", nbd], check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-dir", required=True)
    parser.add_argument("--name-prefix", default="bonect-replay-v87-")
    parser.add_argument("--count", type=int, default=4)
    parser.add_argument("--nbd", default="/dev/nbd0")
    args = parser.parse_args()
    run(["modprobe", "nbd", "max_part=16"])
    storage_dir = Path(args.storage_dir)
    mount_root = Path("/tmp/bonect-replay-machine-id-root")
    for index in range(1, args.count + 1):
        name = f"{args.name_prefix}{index}"
        update_one(
            name,
            storage_dir / f"{name}.qcow2",
            args.nbd,
            mount_root,
        )
    print("all replay VM machine IDs are unique", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
