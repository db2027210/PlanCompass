#!/usr/bin/env python3
"""Write the continued-campaign final audit after low-frequency monitoring."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
INTERVAL_SECONDS = 300


def main() -> int:
    if len(sys.argv) != 2:
        raise RuntimeError("usage: finalizer CONTINUED_CAMPAIGN_PREFIX")
    prefix = sys.argv[1]
    campaign = HISTORY / prefix
    summary_path = campaign / "completion_monitor_summary.json"
    audit_path = campaign / "final_completion_audit.json"
    while True:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("all_terminal") is True:
            if summary.get("all_complete") is not True:
                raise RuntimeError("continued campaign reached a non-complete terminal state")
            alive = [
                int(row["pid"])
                for row in (summary.get("scenes") or {}).values()
                if Path(f"/proc/{int(row['pid'])}").exists()
            ]
            if alive:
                time.sleep(5)
                continue
            if audit_path.exists():
                return 0
            return subprocess.run([
                sys.executable,
                str(ROOT / "scripts/write_mysql_algorithm_c_continued_final_completion_audit_v2.py"),
                prefix,
            ], cwd=ROOT, check=False).returncode
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())

