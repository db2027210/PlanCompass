#!/usr/bin/env python3
"""Bind an exec chain to one NUMA node before loading the target runtime."""

from __future__ import annotations

import argparse
import ctypes
import os
import sys


class Bitmask(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_ulong),
        ("maskp", ctypes.POINTER(ctypes.c_ulong)),
    ]


def library() -> ctypes.CDLL:
    value = ctypes.CDLL("libnuma.so.1", use_errno=True)
    value.numa_bitmask_alloc.argtypes = [ctypes.c_uint]
    value.numa_bitmask_alloc.restype = ctypes.POINTER(Bitmask)
    value.numa_bitmask_clearall.argtypes = [ctypes.POINTER(Bitmask)]
    value.numa_bitmask_clearall.restype = ctypes.POINTER(Bitmask)
    value.numa_bitmask_setbit.argtypes = [ctypes.POINTER(Bitmask), ctypes.c_uint]
    value.numa_bitmask_setbit.restype = ctypes.POINTER(Bitmask)
    value.numa_bitmask_isbitset.argtypes = [
        ctypes.POINTER(Bitmask),
        ctypes.c_uint,
    ]
    value.numa_bitmask_isbitset.restype = ctypes.c_int
    value.numa_bitmask_free.argtypes = [ctypes.POINTER(Bitmask)]
    value.numa_bitmask_free.restype = None
    value.numa_set_membind.argtypes = [ctypes.POINTER(Bitmask)]
    value.numa_set_membind.restype = None
    value.numa_get_membind.argtypes = []
    value.numa_get_membind.restype = ctypes.POINTER(Bitmask)
    return value


def bind(node: int) -> None:
    numa = library()
    if numa.numa_available() != 0:
        raise RuntimeError("libnuma reports that NUMA is unavailable")
    maximum = int(numa.numa_max_node())
    if node < 0 or node > maximum:
        raise RuntimeError(f"NUMA node {node} is outside 0..{maximum}")
    requested = numa.numa_bitmask_alloc(maximum + 1)
    if not requested:
        raise RuntimeError("libnuma could not allocate a node mask")
    try:
        numa.numa_bitmask_clearall(requested)
        numa.numa_bitmask_setbit(requested, node)
        numa.numa_set_membind(requested)
    finally:
        numa.numa_bitmask_free(requested)
    observed = numa.numa_get_membind()
    if not observed:
        raise RuntimeError("libnuma could not read back the memory policy")
    try:
        nodes = {
            candidate
            for candidate in range(maximum + 1)
            if numa.numa_bitmask_isbitset(observed, candidate)
        }
    finally:
        numa.numa_bitmask_free(observed)
    if nodes != {node}:
        raise RuntimeError(f"NUMA memory-policy readback differs: {sorted(nodes)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise SystemExit("an exec command is required after --")
    bind(args.node)
    os.execv(command[0], command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
