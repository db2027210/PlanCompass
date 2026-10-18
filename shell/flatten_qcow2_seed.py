#!/usr/bin/env python3
"""Create and validate an immutable qcow2 seed without a backing chain."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict


def qemu_img_info(path: Path) -> Dict[str, Any]:
    completed = subprocess.run(
        ["qemu-img", "info", "-U", "--output=json", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"qemu-img info failed: {completed.stdout}")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise ValueError("qemu-img info did not return a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--coroutines",
        type=int,
        default=8,
        choices=range(1, 17),
        metavar="N",
        help="qemu-img conversion coroutines (1-16; default: 8)",
    )
    parser.add_argument(
        "--out-of-order",
        action="store_true",
        help="allow out-of-order target writes for faster sparse conversion",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="show qemu-img conversion progress",
    )
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    temporary = output.with_name(output.name + ".building")
    if not source.is_file():
        raise FileNotFoundError(f"source seed is missing: {source}")
    if output.exists():
        info = qemu_img_info(output)
        if info.get("backing-filename"):
            raise RuntimeError(f"existing output still has a backing file: {output}")
        print(f"validated existing flat seed: {output}", flush=True)
        return 0
    if temporary.exists():
        raise FileExistsError(f"refusing to overwrite incomplete conversion: {temporary}")

    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "qemu-img",
        "convert",
        "-U",
        "-m",
        str(args.coroutines),
    ]
    if args.out_of_order:
        command.append("-W")
    if args.progress:
        command.append("-p")
    command.extend([
        "-O",
        "qcow2",
        "-o",
        "cluster_size=2097152,lazy_refcounts=on",
        str(source),
        str(temporary),
    ])
    print("starting flat seed conversion", flush=True)
    subprocess.run(command, check=True)
    info = qemu_img_info(temporary)
    if info.get("format") != "qcow2":
        raise RuntimeError(f"unexpected output format: {info.get('format')}")
    if info.get("backing-filename"):
        raise RuntimeError(f"converted seed still has a backing file: {temporary}")
    if int(info.get("virtual-size") or 0) <= 0:
        raise RuntimeError(f"converted seed has invalid virtual size: {temporary}")
    os.replace(temporary, output)
    print(
        json.dumps(
            {
                "output": str(output),
                "virtual_size": info.get("virtual-size"),
                "actual_size": info.get("actual-size"),
                "backing_filename": info.get("backing-filename"),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
