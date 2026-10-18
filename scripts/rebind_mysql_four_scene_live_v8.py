#!/usr/bin/env python3
"""Bind the live eight-container data plane to the v11 freeze."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260810_v12"
    / "four_scene_effective_resource_successor.json"
)
V11_FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v11"
    / "four_scene_effective_resource_successor.json"
)
PREFLIGHT = ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_v23.json"
NON_XP_PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_adopt_v7_20260809T194100p0800/prepared_binding.json"
)
XP_PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "xp_restore_after_failed_formal_v15_retry1_20260810T143400p0800/prepared_binding.json"
)
XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}
FREEZE_SCHEMA = "mysql_disk2_four_scene_two_worker_effective_resource_successor_v12"
PREDECESSOR_FREEZE_SCHEMA = (
    "mysql_disk2_four_scene_two_worker_effective_resource_successor_v11"
)
OUTPUT_SCHEMA = "mysql_four_scene_live_rebind_v12"
AUTHORITY_VERSION = "v12"
XP_ALLOWED_STATUSES = {"PREPARED"}
PREFLIGHT_ALLOWED_STATUSES = {"PASS"}
PREFLIGHT_FORMAL_LAUNCH_AUTHORIZED = True


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
    preflight = load(PREFLIGHT)
    non_xp = load(NON_XP_PREPARED)
    xp = load(XP_PREPARED)
    if (
        freeze.get("schema_version") != FREEZE_SCHEMA
        or load(V11_FREEZE).get("schema_version") != PREDECESSOR_FREEZE_SCHEMA
        or (freeze.get("predecessor") or {}).get("sha256") != sha(V11_FREEZE)
        or preflight.get("status") not in PREFLIGHT_ALLOWED_STATUSES
        or preflight.get("freeze_sha256") != sha(FREEZE)
        or float(preflight.get("observed_seconds", 0)) < 60
        or non_xp.get("status") != "PREPARED"
        or xp.get("status") not in XP_ALLOWED_STATUSES
        or xp.get("freeze_sha256") != sha(FREEZE)
    ):
        raise RuntimeError(f"{AUTHORITY_VERSION} live rebind authority differs")
    frozen = {row["worker_id"]: row for row in freeze["workers"]}
    non_xp_rows = {row["worker_id"]: row for row in non_xp["workers"]}
    xp_rows = {row["worker_id"]: row for row in xp["workers"]}
    xp_io_profile = freeze.get("xp_innodb_io_profile") or {}
    rows = []
    for worker_id, worker in frozen.items():
        prior = (xp_rows if worker_id in XP_IDS else non_xp_rows)[worker_id]
        inspect = json.loads(subprocess.run(
            ["docker", "inspect", worker["container"]], capture_output=True,
            text=True, check=True, timeout=60,
        ).stdout)[0]
        host = inspect.get("HostConfig") or {}
        expected_init = (
            bool(xp_io_profile.get("container_init"))
            if worker_id in XP_IDS and xp_io_profile else host.get("Init")
        )
        if (
            inspect.get("Id") != prior.get("container_id")
            or (inspect.get("State") or {}).get("Running") is not True
            or host.get("CpusetCpus") != worker["database_cpuset"]
            or host.get("CpusetMems") != worker["database_cpuset_mems"]
            or host.get("NanoCpus")
            != int(round(float(worker["cpu_quota_cores"]) * 1_000_000_000))
            or host.get("Memory") != int(worker["memory_bytes"])
            or host.get("PidsLimit") != int(worker["pids_max"])
            or host.get("Init") != expected_init
        ):
            raise RuntimeError(
                f"{worker_id} {AUTHORITY_VERSION} rebind live identity differs"
            )
        mysql_readback = subprocess.run(
            [
                "docker", "exec", worker["container"], "mysql",
                "--defaults-extra-file=/run/ra-credentials/selected.cnf",
                "-Nse", "SELECT @@GLOBAL.innodb_buffer_pool_size,"
                "@@GLOBAL.innodb_use_native_aio,@@GLOBAL.innodb_flush_method;",
            ], capture_output=True, text=True, check=True, timeout=60,
        ).stdout.strip().split("\t")
        if len(mysql_readback) != 3:
            raise RuntimeError(f"{worker_id} InnoDB readback cardinality differs")
        buffer_pool = int(mysql_readback[0])
        native_aio = mysql_readback[1] == "1"
        flush_method = mysql_readback[2]
        if buffer_pool != int(worker["innodb_buffer_pool_bytes"]):
            raise RuntimeError(
                f"{worker_id} {AUTHORITY_VERSION} buffer-pool readback differs"
            )
        if worker_id in XP_IDS and xp_io_profile and (
            native_aio is not bool(xp_io_profile.get("innodb_use_native_aio"))
            or flush_method.lower()
            != str(xp_io_profile.get("innodb_flush_method", "")).lower()
        ):
            raise RuntimeError(f"{worker_id} frozen InnoDB I/O readback differs")
        rows.append({
            **prior, **worker, "innodb_buffer_pool_bytes": buffer_pool,
            "innodb_use_native_aio": native_aio,
            "innodb_flush_method": flush_method,
            "container_init": host.get("Init"),
        })
    rows.sort(key=lambda row: row["worker_id"])
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    document = {
        "schema_version": OUTPUT_SCHEMA,
        "status": "PREPARED",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "preflight_path": str(PREFLIGHT),
        "preflight_sha256": sha(PREFLIGHT),
        "preflight_formal_launch_authorized": PREFLIGHT_FORMAL_LAUNCH_AUTHORIZED,
        "predecessor_prepared_path": str(NON_XP_PREPARED),
        "predecessor_prepared_sha256": sha(NON_XP_PREPARED),
        "xp_reset_prepared_path": str(XP_PREPARED),
        "xp_reset_prepared_sha256": sha(XP_PREPARED),
        "resource_envelope_change": freeze["successor_resource_envelope_change"],
        "workers": rows,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
    }
    binding = output / "prepared_binding.json"
    binding.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    binding.with_name(binding.name + ".sha256").write_text(
        f"{sha(binding)}  {binding.name}\n", encoding="ascii"
    )
    print(json.dumps({"status": "PREPARED", "binding": str(binding)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
