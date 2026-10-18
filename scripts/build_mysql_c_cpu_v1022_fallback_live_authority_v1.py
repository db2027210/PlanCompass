#!/usr/bin/env python3
"""Build fresh CPU-C authority from the completed v1022 fallback reset."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_c_xp4_scene_handoff_v1 as handoff
from scripts import mysql_cross_project_wave_coordination_v17 as gate_v17
from scripts import reset_mysql_xp_data_planes_mixed_four_worker_v4 as reset


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = (
    HISTORY
    / "v1022_to_c_cpu_outer17_v84_reset_serial_resume_20260828_xp_cpu_xp_cpu"
)
STATE = SOURCE / "run_state.json"
RESET_BINDING = (
    SOURCE
    / "outer_018/fallback_plan_change/xp_resets/"
    "outer_018_fallback_plan_change_1/reset_binding.json"
)
FREEZE = (
    ROOT
    / "config/formal/frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3"
    / "four_scene_effective_resource_successor.json"
)
OUTPUT = HISTORY / "v1029_c_cpu_v1022_fallback_live_authority_20260828"
SCENE = "hybench_xp_cpu"
EXPECTED = {f"xp_cpu_w{i}" for i in range(1, 5)}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh authority output required: {OUTPUT}")
    state = load(STATE)
    binding = load(RESET_BINDING)
    freeze = load(FREEZE)
    rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in binding.get("workers") or []
        if row.get("worker_id") in EXPECTED
    }
    frozen = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in freeze.get("workers") or []
        if row.get("worker_id") in EXPECTED
    }
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("error") != "RuntimeError: xp_cpu_w3 quarantine target already exists"
        or int(state.get("outer_attempt") or 0) != 17
        or int((state.get("accepted_rounds") or {}).get(SCENE) or 0) != 6
        or binding.get("status") != "PASS"
        or binding.get("freeze_sha256") != handoff.sha(FREEZE)
        or set(rows) != EXPECTED
        or set(frozen) != EXPECTED
    ):
        raise RuntimeError("CPU-C v1022 fallback authority boundary differs")
    values = {
        "AUTHORITY_VERSION": "algorithm_c_v86_cpu_v1022_fallback_authority",
        "OUTPUT_SCHEMA": "mysql_algorithm_c_v86_cpu_fallback_authority_v1",
        "XP_IDS": set(EXPECTED),
        "XP_CONTAINER_ALLOWLIST": {
            worker_id: str(frozen[worker_id]["container"])
            for worker_id in sorted(EXPECTED)
        },
        "RESET_SAME_FREEZE": True,
    }
    reset._impl.update(copy.deepcopy(values))
    reset.configure(freeze=FREEZE, prepared=RESET_BINDING)
    for worker_id in sorted(EXPECTED):
        row = rows[worker_id]
        inspected = reset._impl["_docker_json"]("inspect", str(row["container"]))
        live = inspected.get("State") or {}
        mounts = {
            str(item.get("Destination") or ""): str(item.get("Source") or "")
            for item in inspected.get("Mounts") or []
        }
        defaults = Path(str(row["credential_defaults"]))
        benchmark = Path(str(row["benchmark_defaults"]))
        sessions = reset._impl["_host_mysql"](
            row, defaults,
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
            "AND USER<>'event_scheduler'", timeout=30,
        ).stdout.strip()
        identity = reset._impl["_host_mysql"](
            row, benchmark,
            "SELECT CURRENT_USER(),DATABASE(),"
            "(SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='hybench_sf10x');", timeout=30,
        ).stdout.strip().split("\t")
        if (
            inspected.get("Id") != row.get("container_id")
            or live.get("Running") is not True
            or mounts.get("/var/lib/mysql") != str(row["datadir"])
            or not defaults.is_file()
            or not benchmark.is_file()
            or sessions != "0"
            or len(identity) != 3
            or not identity[0].startswith("ra_benchmark@")
            or identity[2] != "8"
        ):
            raise RuntimeError(f"{worker_id} CPU-C live authority differs")
        row["kernel_cgroup_resources"] = reset._impl["prep"]._kernel_resource_readback(
            inspected, row
        )
        row["blkio_authority"] = reset._impl["prep"]._enforce_blkio(
            inspected, freeze, row
        )
        row["passwords_emitted"] = False
    handoff.dynamic_gate = gate_v17
    artifacts = handoff.authority_artifacts(
        scene=SCENE,
        freeze_path=FREEZE,
        freeze=freeze,
        live_rows=rows,
        output=OUTPUT / "authority",
    )
    document = {
        "schema_version": "mysql_algorithm_c_v86_cpu_fallback_live_authority_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": SCENE,
        "source_state": {"path": str(STATE), "sha256": handoff.sha(STATE)},
        "source_reset_binding": {
            "path": str(RESET_BINDING), "sha256": handoff.sha(RESET_BINDING)
        },
        "candidate_measurements_replayed": 0,
        "accepted_or_rejected_credit": 0,
        "algorithm_a_role": "independent_posthoc_check_only",
        "algorithm_a_feedback_to_b_or_c": False,
        "artifacts": {key: str(path) for key, path in artifacts.items()},
    }
    handoff.atomic(OUTPUT / "authority_manifest.json", document)
    print(json.dumps(document, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
