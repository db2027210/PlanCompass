from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def write_heartbeat(path: Path, *, status: str, current_task: str,
                    mutex_state: str, last_command: str,
                    last_evidence: str, next_action: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "current_task": current_task,
        "mutex_state": mutex_state,
        "last_command": last_command,
        "last_evidence": last_evidence,
        "next_action": next_action,
    }
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


if __name__ == "__main__":
    if len(sys.argv) >= 8:
        write_heartbeat(
            Path(sys.argv[1]),
            status=sys.argv[2],
            current_task=sys.argv[3],
            mutex_state=sys.argv[4],
            last_command=sys.argv[5],
            last_evidence=sys.argv[6],
            next_action=sys.argv[7],
        )
        raise SystemExit(0)
    write_heartbeat(
        Path(sys.argv[1]),
        status="fail_closed_static_preflight",
        current_task="xp_startup_evidence",
        mutex_state="campaign_lock_present_pressure_lock_free_unverified_owner",
        last_command="contract_parse_and_config_contract_tests",
        last_evidence="local_static_preflight_pending",
        next_action="run_no_database_capture_contract_tests_then_retry_remote_owner_check",
    )
