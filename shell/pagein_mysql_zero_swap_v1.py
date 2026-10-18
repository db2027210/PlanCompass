#!/usr/bin/env python3
"""Page in only swapped readable pages of one identity-bound mysqld process."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path


class PageInError(RuntimeError):
    pass


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def start_ticks(pid):
    fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
    return int(fields[19])


def cgroup_swap_bytes(memory_cgroup):
    rows = {}
    for line in (memory_cgroup / "memory.stat").read_text().splitlines():
        key, value = line.split()
        rows[key] = int(value)
    if "swap" not in rows:
        raise PageInError("memory.stat swap counter is absent")
    return rows["swap"]


def set_swappiness_zero(memory_cgroup):
    path = memory_cgroup / "memory.swappiness"
    before = int(path.read_text().strip())
    if before != 0:
        descriptor = os.open(str(path), os.O_WRONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            if os.write(descriptor, b"0\n") != 2:
                raise PageInError("short memory.swappiness write")
        finally:
            os.close(descriptor)
    after = int(path.read_text().strip())
    if after != 0:
        raise PageInError("memory.swappiness remains nonzero")
    return before, after


def swapped_readable_ranges(smaps_text):
    header = None
    result = []
    for line in smaps_text.splitlines():
        match = re.match(r"^([0-9a-f]+)-([0-9a-f]+)\s+(\S+)", line)
        if match:
            header = (int(match.group(1), 16), int(match.group(2), 16), match.group(3), line)
        elif line.startswith("Swap:") and header is not None:
            swap_kib = int(line.split()[1])
            if swap_kib and header[2].startswith("r"):
                result.append((*header, swap_kib))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--expected-cmdline-sha256", required=True)
    parser.add_argument("--expected-container-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-scan-bytes", type=int, default=536870912)
    parser.add_argument("--max-swapped-pages", type=int, default=4096)
    args = parser.parse_args()
    if os.geteuid() != 0 or args.output.exists() or args.output.is_symlink():
        raise PageInError("root and no-overwrite output are required")
    if re.fullmatch(r"[0-9a-f]{64}", args.expected_cmdline_sha256) is None:
        raise PageInError("expected cmdline hash differs")
    if re.fullmatch(r"[0-9a-f]{64}", args.expected_container_id) is None:
        raise PageInError("expected container ID differs")
    proc = Path("/proc") / str(args.pid)
    comm = (proc / "comm").read_text().strip()
    cmdline = (proc / "cmdline").read_bytes()
    initial_ticks = start_ticks(args.pid)
    if comm != "mysqld" or sha_bytes(cmdline) != args.expected_cmdline_sha256:
        raise PageInError("mysqld live identity differs")
    memory_cgroup = Path("/sys/fs/cgroup/memory/docker") / args.expected_container_id
    if not memory_cgroup.is_dir():
        raise PageInError("exact MySQL memory cgroup is absent")
    ranges = swapped_readable_ranges((proc / "smaps").read_text())
    scan_bytes = sum(end - start for start, end, _, _, _ in ranges)
    before = cgroup_swap_bytes(memory_cgroup)
    if before <= 0 or scan_bytes > args.max_scan_bytes:
        raise PageInError("swap or bounded scan precondition differs")
    swappiness_before, swappiness_after = set_swappiness_zero(memory_cgroup)
    page_size = os.sysconf("SC_PAGE_SIZE")
    pagemap = os.open(str(proc / "pagemap"), os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    memory = os.open(str(proc / "mem"), os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    paged = []
    try:
        for start, end, permissions, header, swap_kib in ranges:
            for address in range(start, end, page_size):
                raw = os.pread(pagemap, 8, (address // page_size) * 8)
                if len(raw) != 8:
                    raise PageInError("short pagemap read")
                entry = int.from_bytes(raw, "little")
                if entry & (1 << 62):
                    if len(paged) >= args.max_swapped_pages:
                        raise PageInError("swapped-page bound exceeded")
                    if len(os.pread(memory, 1, address)) != 1:
                        raise PageInError("short process-memory read")
                    paged.append({"address": hex(address), "mapping": header,
                                  "mapping_swap_kib": swap_kib,
                                  "permissions": permissions})
    finally:
        os.close(memory); os.close(pagemap)
    after = None
    for _ in range(50):
        if (start_ticks(args.pid) != initial_ticks
                or sha_bytes((proc / "cmdline").read_bytes()) != args.expected_cmdline_sha256):
            raise PageInError("mysqld identity changed during page-in")
        after = cgroup_swap_bytes(memory_cgroup)
        if after == 0:
            break
        time.sleep(0.1)
    evidence = {
        "schema_version": "mysql_tpch_identity_bound_zero_swap_pagein_v1",
        "status": "PASS_ZERO_SWAP_PAGED_IN" if after == 0 else "FAIL_SWAP_REMAINS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "target": {"pid": args.pid, "comm": comm,
                   "start_ticks": initial_ticks,
                   "cmdline_sha256": args.expected_cmdline_sha256,
                   "container_id": args.expected_container_id,
                   "memory_cgroup": str(memory_cgroup)},
        "swap_bytes_before": before,
        "swap_bytes_after": after,
        "memory_swappiness_before": swappiness_before,
        "memory_swappiness_after": swappiness_after,
        "memory_swappiness_scope": "exact_target_mysql_cgroup_only",
        "readable_swapped_mappings": len(ranges),
        "scanned_virtual_bytes": scan_bytes,
        "paged_in_pages": paged,
        "database_restarted": False,
        "datadir_modified_by_remediator": False,
        "workload_started": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }
    raw = canonical(evidence)
    descriptor = os.open(str(args.output), os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(descriptor, raw); os.fsync(descriptor)
    finally:
        os.close(descriptor)
    print(json.dumps({"status": evidence["status"], "pid": args.pid,
                      "swap_before": before, "swap_after": after,
                      "paged_in_pages": len(paged),
                      "evidence_sha256": sha_bytes(raw)}, sort_keys=True))
    if after != 0:
        raise PageInError("swap remains after bounded page-in")


if __name__ == "__main__":
    try:
        main()
    except (PageInError, OSError, ValueError, IndexError) as exc:
        print("NOT_READY: " + str(exc), file=__import__("sys").stderr)
        raise SystemExit(2)
