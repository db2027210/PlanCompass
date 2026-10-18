#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from host02_job_xp_mutex import Host02JobXpMutex


def probe(scenario: str, phase: str) -> dict[str, object]:
    names = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], check=True,
        text=True, stdout=subprocess.PIPE,
    ).stdout.splitlines()
    pressure_names = [name for name in names if name != "ra_pg5_job_node02_v1_a2"
                      and ("job" in name.lower() or "xp" in name.lower())]
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "probe_id": f"job_strict_explain_{phase}",
        "job_pressure_count": len([name for name in pressure_names if "job" in name.lower()]),
        "xp_pressure_count": len([name for name in pressure_names if "xp" in name.lower()]),
        "running_non_pg_pressure_containers": pressure_names,
    }


def main() -> int:
    run_root = Path(sys.argv[1]).resolve()
    command = [str(run_root / "run.sh"), str(run_root)]
    with Host02JobXpMutex(
        scenario="job_memory", pressure_probe=probe,
        lock_path=Path("/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/host02_control/job_xp_pressure.lock"),
        journal_path=Path("/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/host02_control/job_xp_lease_journal.jsonl"),
        owner_id="job_strict_raw_explain_20260723",
    ):
        subprocess.run(command, check=True)
    print(json.dumps({"status": "PASS", "mutex_released": True, "r0": False, "sgd": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
