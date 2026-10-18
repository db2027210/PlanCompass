#!/usr/bin/env python3
"""Wait for the final replay campaign and build the final report."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CAMPAIGN = HISTORY / "v1196_mysql_abc_final_counterbalanced_20260904"
CAMPAIGN_MANIFEST = CAMPAIGN / "launch_manifest.json"
OUTPUT = HISTORY / "v1197_mysql_abc_final_report_handoff_20260904"
MANIFEST = OUTPUT / "finalize_manifest.json"


def now() -> str:
    return datetime.now().astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh finalizer root required: {OUTPUT}")
    OUTPUT.mkdir(parents=True, mode=0o700)
    manifest: dict[str, Any] = {
        "schema_version": "mysql_abc_final_report_handoff_v1",
        "status": "WAITING_FOR_FINAL_REPLAY",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
    }
    atomic(MANIFEST, manifest)
    while True:
        if not CAMPAIGN_MANIFEST.is_file():
            time.sleep(30)
            continue
        campaign = load(CAMPAIGN_MANIFEST)
        manifest.update({
            "updated_at": now(),
            "campaign_status": campaign.get("status"),
            "campaign_running_scenes": campaign.get("running_scenes"),
            "campaign_manifest_sha256": sha(CAMPAIGN_MANIFEST),
        })
        atomic(MANIFEST, manifest)
        if campaign.get("status") == "FAILED_CLOSED":
            manifest.update({"status": "BLOCKED_BY_REPLAY_FAILURE", "updated_at": now()})
            atomic(MANIFEST, manifest)
            return 4
        if campaign.get("status") == "COMPLETE":
            break
        time.sleep(30)
    manifest.update({"status": "BUILDING_FINAL_REPORT", "updated_at": now()})
    atomic(MANIFEST, manifest)
    subprocess.run(
        [sys.executable, "scripts/build_mysql_abc_final_report_v1.py"],
        cwd=ROOT, check=True,
    )
    manifest.update({"status": "AUDITING_FINAL_EVIDENCE", "updated_at": now()})
    atomic(MANIFEST, manifest)
    subprocess.run(
        [sys.executable, "scripts/audit_mysql_abc_final_completion_v1.py"],
        cwd=ROOT, check=True,
    )
    audit = HISTORY / (
        "v1197_mysql_abc_final_completion_audit_20260904/"
        "completion_audit.json"
    )
    manifest.update({
        "status": "COMPLETE",
        "updated_at": now(),
        "summary": str(
            HISTORY / "v1197_mysql_abc_final_report_20260904/final_summary.json"
        ),
        "report": str(ROOT / "reports/mysql_abc_final_results_20260904.md"),
        "completion_audit": str(audit),
        "completion_audit_sha256": sha(audit),
    })
    atomic(MANIFEST, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
