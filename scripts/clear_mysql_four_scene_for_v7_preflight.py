#!/usr/bin/env python3
"""Stop and remove exactly the eight successor containers; preserve data dirs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import prepare_mysql_four_scene_v4 as prep


FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v7"
    / "four_scene_effective_resource_successor.json"
)
NON_XP_PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_adopt_v6_20260809T182437p0800/prepared_binding.json"
)
XP_PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "xp_reset_timeout_recovery_v7_20260809T193600p0800/prepared_binding.json"
)
XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")
    freeze = load(FREEZE)
    old = load(NON_XP_PREPARED)
    xp = load(XP_PREPARED)
    if (
        freeze.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v7"
        or old.get("status") != "PREPARED"
        or xp.get("status") != "PREPARED"
        or xp.get("freeze_sha256") != sha(FREEZE)
        or any((xp.get("accepted_rounds") or {}).values())
    ):
        raise RuntimeError("v7 cleanup authority differs")
    old_workers = {row["worker_id"]: row for row in old["workers"]}
    xp_workers = {row["worker_id"]: row for row in xp["workers"]}
    frozen = {row["worker_id"]: row for row in freeze["workers"]}
    expected = {
        worker_id: (xp_workers if worker_id in XP_IDS else old_workers)[worker_id]
        for worker_id in frozen
    }
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    removed = []
    for worker_id in sorted(frozen):
        worker = frozen[worker_id]
        prior = expected[worker_id]
        name = worker["container"]
        inspect = prep._docker_json("inspect", name)
        mounts = inspect.get("Mounts") or []
        mysql_mounts = [row for row in mounts if row.get("Destination") == "/var/lib/mysql"]
        expected_datadir = Path(str(freeze["storage"]["runtime_root"])) / worker_id / "datadir"
        labels = (inspect.get("Config") or {}).get("Labels") or {}
        if (
            inspect.get("Id") != prior.get("container_id")
            or labels.get("ra.owner") != "mysql_four_scene_v5"
            or labels.get("ra.worker_id") != worker_id
            or len(mysql_mounts) != 1
            or mysql_mounts[0].get("Source") != str(expected_datadir)
        ):
            raise RuntimeError(f"{worker_id} cleanup target identity differs")
        if (inspect.get("State") or {}).get("Running") is True:
            active = prep._run([
                "docker", "exec", name, "mysql",
                "--defaults-file=/run/ra-credentials/selected.cnf", "-NBe",
                "SELECT COUNT(*) FROM information_schema.processlist "
                "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' AND USER<>'event_scheduler'",
            ], timeout=90).stdout.strip()
            if active != "0":
                raise RuntimeError(f"{worker_id} still has active sessions")
            prep._run(["docker", "stop", "--time", "120", name], timeout=240)
        prep._run(["docker", "rm", name], timeout=120)
        if not expected_datadir.is_dir():
            raise RuntimeError(f"{worker_id} preserved datadir is absent")
        removed.append({
            "worker_id": worker_id,
            "container": name,
            "container_id": prior["container_id"],
            "datadir_preserved": str(expected_datadir),
        })
        print(json.dumps({"worker_id": worker_id, "container_removed": True}), flush=True)
    document = {
        "schema_version": "mysql_four_scene_v7_preflight_cleanup_v1",
        "status": "PASS",
        "completed_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "non_xp_predecessor_path": str(NON_XP_PREPARED),
        "non_xp_predecessor_sha256": sha(NON_XP_PREPARED),
        "xp_predecessor_path": str(XP_PREPARED),
        "xp_predecessor_sha256": sha(XP_PREPARED),
        "removed_containers": removed,
        "data_directories_removed": False,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
    }
    binding = output / "cleanup_binding.json"
    binding.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    binding.with_name(binding.name + ".sha256").write_text(
        f"{sha(binding)}  {binding.name}\n", encoding="ascii"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
