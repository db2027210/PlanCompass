#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.bonect_vm_wave_reset import rewrite_domain_disk_source, selected_workers


def main() -> None:
    xml = """<domain><devices><disk type='file' device='disk'><source file='/old.qcow2'/><target dev='vda'/></disk></devices></domain>"""
    updated = rewrite_domain_disk_source(xml, "/new-live.qcow2")
    assert "/new-live.qcow2" in updated
    assert "/old.qcow2" not in updated
    pool = {
        "workers": [
            {
                "enabled": True,
                "worker_id": "w1",
                "vm_name": "bonect-replay-v87-1",
                "scenarios": ["cpu"],
                "storage_pool": "pool",
                "clean_volume": "clean.qcow2",
                "live_volume": "worker-live.qcow2",
            }
        ]
    }
    assert len(selected_workers(pool, "cpu", 4)) == 1
    unsafe = {"workers": [{**pool["workers"][0], "vm_name": "zzk"}]}
    try:
        selected_workers(unsafe, "cpu", 4)
    except ValueError as exc:
        assert "refuses non-Bonect domain" in str(exc)
    else:
        raise AssertionError("unsafe domain was not rejected")
    print("bonect VM wave reset tests passed", flush=True)


if __name__ == "__main__":
    main()
