#!/usr/bin/env python3
"""Seal the repeatable v18 XP/I/O envelope qualification as a reusable release."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18/four_scene_effective_resource_successor.json"
RESET1 = ROOT / "history/mysql_four_scene_20260809/xp_reset_v18_750k_stability_candidate_20260813T1520p0800/reset_binding.json"
RESET2 = ROOT / "history/mysql_four_scene_20260809/xp_reset_v18_between_stability_waves_20260813T1550p0800/reset_binding.json"
WAVE1 = ROOT / "history/mysql_four_scene_20260809/xp_io_v18_stability_pilot_wave1_20260813T1538p0800/pilot_result.json"
WAVE2 = ROOT / "history/mysql_four_scene_20260809/xp_io_v18_stability_pilot_wave2_20260813T1552p0800/pilot_result.json"
OUTPUT = ROOT / "artifacts/mysql_xp_io_v18_calibration_release_20260813/release.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"JSON object required: {path}")
    return value


def ref(path: Path) -> dict:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def main() -> int:
    if OUTPUT.exists():
        raise SystemExit(f"refusing to replace release: {OUTPUT}")
    freeze, reset1, reset2, wave1, wave2 = map(load, (FREEZE, RESET1, RESET2, WAVE1, WAVE2))
    xp = {row["worker_id"]: row for row in freeze["workers"] if row["scene"] == "hybench_xp_io"}
    if (
        freeze.get("schema_version") != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v18"
        or set(xp) != {"xp_io_w1", "xp_io_w2"}
        or any(int(row["io_write_bps"]) != 750000 for row in xp.values())
        or any(reset.get("status") != "PASS" or reset.get("freeze_sha256") != sha(FREEZE) for reset in (reset1, reset2))
        or any(wave.get("status") != "PASS" for wave in (wave1, wave2))
        or any(wave.get("formal_evidence_eligible") is not False or wave.get("accepted_rounds_credit") != 0 for wave in (wave1, wave2))
        or any(
            gate.get("coverage_complete") is not True
            or float(gate.get("qps") or 0) <= 0
            or float(gate.get("io_fraction") or 0) < 0.8
            or gate.get("container_id_unchanged") is not True
            or gate.get("container_pid_unchanged") is not True
            or gate.get("running_after") is not True
            for wave in (wave1, wave2) for gate in wave["gates"].values()
        )
    ):
        raise SystemExit("v18 calibration release authority differs")
    signature_payload = {
        "formal_scope": "mysql_only",
        "scene": "hybench_xp_io",
        "native_workload": freeze["native_xp_contract"],
        "workers": {
            key: {
                field: xp[key][field] for field in (
                    "container", "host_port", "database_cpuset", "database_cpuset_mems",
                    "client_cpuset", "client_cpuset_mems", "cpu_quota_cores",
                    "memory_bytes", "swap_bytes", "pids_max", "io_read_bps",
                    "io_write_bps", "innodb_buffer_pool_bytes",
                )
            }
            for key in sorted(xp)
        },
        "storage": freeze["storage"],
        "xp_innodb_io_profile": freeze["xp_innodb_io_profile"],
    }
    signature = hashlib.sha256(
        json.dumps(signature_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    document = {
        "schema_version": "mysql_xp_io_calibration_release_v1",
        "status": "RELEASED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "accepted_rounds_credit": 0,
        "purpose": "reuse v18 XP/I/O envelope qualification without replaying two calibration waves",
        "freeze": ref(FREEZE),
        "fresh_resets": [ref(RESET1), ref(RESET2)],
        "real_replays": [ref(WAVE1), ref(WAVE2)],
        "native_xp_runtime_sha256": wave1["native_xp_runtime_sha256"],
        "compatibility_signature_payload": signature_payload,
        "compatibility_signature_sha256": signature,
        "qualification": {
            "independent_fresh_waves": 2,
            "all_native_coverage_complete": True,
            "minimum_io_fraction": min(float(g["io_fraction"]) for w in (wave1, wave2) for g in w["gates"].values()),
            "all_container_id_and_pid_stable": True,
            "all_mysql_running_after": True,
        },
        "reuse_policy": {
            "require_exact_compatibility_signature": True,
            "require_freeze_sha256": sha(FREEZE),
            "require_native_runtime_sha256": wave1["native_xp_runtime_sha256"],
            "require_live_cpu_memory_swap_pids_cpuset_numa_and_blkio_readback": True,
            "require_exact_container_allowlist_20260809": True,
            "forbid_container_names_containing_0708": True,
            "on_mismatch": "fresh_calibration_fail_closed",
            "formal_dynamic_non_interference_gate_still_required": True,
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=False)
    OUTPUT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({"path": str(OUTPUT), "sha256": sha(OUTPUT), "compatibility_signature": signature}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
