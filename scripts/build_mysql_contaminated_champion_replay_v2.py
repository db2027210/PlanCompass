#!/usr/bin/env python3
"""Freeze the zero-I/O champion replay against its restored live authority."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_contaminated_champion_replay_20260829_v1/champion_replay_contract.json"
OUT = ROOT / "config/formal/frozen_mysql_contaminated_champion_replay_20260829_v2"
CONTRACT = OUT / "champion_replay_contract.json"
AUTHORITY_ROOT = ROOT / "history/mysql_four_scene_20260809/v1069_contaminated_champion_live_authority_resume_20260829"


def sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bind(path: Path) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def main() -> int:
    if OUT.exists() or OUT.is_symlink():
        raise RuntimeError(f"fresh replay contract required: {OUT}")
    live_path = AUTHORITY_ROOT / "live_authority.json"
    live = json.loads(live_path.read_text(encoding="utf-8"))
    if live.get("status") != "PASS" or live.get("formal_launch_authorized") is not True:
        raise RuntimeError("restored live authority is not launchable")
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    document.update({
        "schema_version": "mysql_contaminated_champion_replay_contract_v2",
        "created_at": datetime.now().astimezone().isoformat(),
        "predecessor_contract": bind(SOURCE),
        "authority": {
            "manifest_path": str(live_path), "manifest_sha256": sha(live_path),
            "prepared_path": live["artifacts"]["prepared"],
            "prepared_sha256": live["artifacts_sha256"]["prepared"],
            "preflight_path": live["artifacts"]["preflight"],
            "preflight_sha256": live["artifacts_sha256"]["preflight"],
            "accounts_path": live["artifacts"]["accounts"],
            "accounts_sha256": live["artifacts_sha256"]["accounts"],
        },
        "resource_freeze_by_scene": live["resource_freeze_by_scene"],
    })
    document.pop("resource_freeze", None)
    document["assignments_by_scene"]["hybench_xp_io"] = {
        "xp_io_w1": "b_io", "xp_io_w2": "c_io",
    }
    document["source_bindings"]["runner"] = bind(
        ROOT / "scripts/run_mysql_contaminated_champion_replay_v1.py"
    )
    document["source_bindings"]["live_authority_restorer"] = bind(
        ROOT / "scripts/restore_mysql_contaminated_champion_slots_v1.py"
    )
    document["source_bindings"]["live_authority_restore_resume"] = bind(
        ROOT / "scripts/resume_restore_mysql_contaminated_champion_slots_v6.py"
    )
    OUT.mkdir(parents=True, exist_ok=False)
    temporary = CONTRACT.with_name(f".{CONTRACT.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, CONTRACT)
    CONTRACT.with_name(CONTRACT.name + ".sha256").write_text(
        f"{sha(CONTRACT)}  {CONTRACT.name}\n", encoding="ascii",
    )
    print(json.dumps({"status": "PASS", "contract": str(CONTRACT), "sha256": sha(CONTRACT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
