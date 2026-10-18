#!/usr/bin/env python3
"""Wait for v92 C, freeze final points, and launch four-scene replay."""

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
C_CAMPAIGN = HISTORY / "v1194_to_c_client_primary_four_scene_20260903"
C_MANIFEST = C_CAMPAIGN / "launch_manifest.json"
SOURCE_AUTHORITY = HISTORY / (
    "v1192_to_c_client_primary_live_authority_20260903/authority"
)
AUTHORITY_RUN_ID = "v1195_to_final_replay_live_authority_20260904"
REPLAY_CAMPAIGN_ID = "v1196_mysql_abc_final_counterbalanced_20260904"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_abc_final_counterbalanced_20260904_v1/"
    "replay_contract.json"
)
OUTPUT = HISTORY / "v1195_to_final_replay_handoff_20260904"
MANIFEST = OUTPUT / "handoff_manifest.json"


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


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh C-to-replay handoff root required: {OUTPUT}")
    OUTPUT.mkdir(parents=True, mode=0o700)
    manifest: dict[str, Any] = {
        "schema_version": "mysql_c_to_final_counterbalanced_handoff_v1",
        "status": "WAITING_FOR_C",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "c_campaign": str(C_CAMPAIGN),
        "replay_campaign_id": REPLAY_CAMPAIGN_ID,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    atomic(MANIFEST, manifest)
    while True:
        if not C_MANIFEST.is_file():
            time.sleep(30)
            continue
        c = load(C_MANIFEST)
        manifest.update({
            "updated_at": now(),
            "c_status": c.get("status"),
            "c_running_scenes": c.get("running_scenes"),
            "c_manifest_sha256": sha(C_MANIFEST),
        })
        atomic(MANIFEST, manifest)
        if c.get("status") == "FAILED_CLOSED":
            manifest.update({"status": "BLOCKED_BY_C_FAILURE", "updated_at": now()})
            atomic(MANIFEST, manifest)
            return 4
        if c.get("status") == "COMPLETE":
            break
        time.sleep(30)

    python = sys.executable
    manifest.update({"status": "REFRESHING_REPLAY_AUTHORITY", "updated_at": now()})
    atomic(MANIFEST, manifest)
    run([
        python,
        "scripts/refresh_mysql_client_primary_authority_after_xp_reset_v2.py",
        "--run-id", AUTHORITY_RUN_ID,
        "--source-authority-root", str(SOURCE_AUTHORITY),
    ])
    authority = HISTORY / AUTHORITY_RUN_ID / "authority"
    run([
        python,
        "scripts/build_mysql_abc_final_counterbalanced_contract_v1.py",
        "--authority-root", str(authority),
    ])
    manifest.update({
        "status": "LAUNCHING_FINAL_REPLAY",
        "updated_at": now(),
        "authority_root": str(authority),
        "contract": str(CONTRACT),
        "contract_sha256": sha(CONTRACT),
    })
    atomic(MANIFEST, manifest)
    run([
        python,
        "scripts/launch_mysql_abc_final_counterbalanced_replay_v1.py",
        "--campaign-id", REPLAY_CAMPAIGN_ID,
        "--contract", str(CONTRACT),
    ])
    manifest.update({
        "status": "COMPLETE",
        "updated_at": now(),
        "replay_campaign": str(HISTORY / REPLAY_CAMPAIGN_ID),
    })
    atomic(MANIFEST, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
