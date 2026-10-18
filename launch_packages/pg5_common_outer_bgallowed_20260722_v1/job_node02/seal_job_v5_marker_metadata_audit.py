#!/usr/bin/env python3
"""Seal the p44/p45 display-only initial-probe metadata discrepancy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict


OLD_REPLAY_SHA256 = (
    "93ce1ee7e09ef31517c04dfe77f1ccefd2bf15478ef5553b6b49cdc32726ea22"
)
NEW_REPLAY_SHA256 = (
    "974634b25e4c421ef7674c357f19438a5c43441c498f44534cf97b86dca162e4"
)
STABILITY_V5_SHA256 = (
    "4b90d0126806ac16031fafbfe905d3aa3d693ddf28b64717eb348fb0911d4692"
)
MEASUREMENT_PROTOCOL_SHA256 = (
    "efe26b93be11a2d36fce798f268a986c0f8c4a2fc50b898bed28dd8ac1458cb1"
)
FINAL_BINDING_MANIFEST_SHA256 = (
    "f329c4eabcc495597db8990d58c17a48bf833ed04cc98106d3064ed3d0b70045"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_sha(path: Path, expected: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"required file is missing: {path}")
    actual = sha256(path)
    if actual != expected:
        raise RuntimeError(f"SHA mismatch: {path}: {actual} != {expected}")


def write_json_new(path: Path, value: Any) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to overwrite sealed output: {path}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    path.chmod(0o444)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane-root", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    args = parser.parse_args()
    lane = args.lane_root.resolve()
    attempt = lane / "history/job/outer/attempt_5_formal_tsgd_v1"
    frozen = lane / "bundle/attempt8_frozen"
    launch = lane / "launch_outer_attempt4_formal_dbqps_v1_retry3"
    old_replay = (
        attempt
        / "v5_transition_archive/formal_job_outer_replay_pre_v5_marker_93ce1ee7.py"
    )
    new_replay = launch / "formal_job_outer_replay.py"
    final_binding = attempt / "job_tsgd_v5_core_043b_resume_after_p43_manifest_v2.json"
    require_sha(old_replay, OLD_REPLAY_SHA256)
    require_sha(new_replay, NEW_REPLAY_SHA256)
    require_sha(frozen / "stability_gate_contract.json", STABILITY_V5_SHA256)
    require_sha(
        frozen / "measurement_protocol.json", MEASUREMENT_PROTOCOL_SHA256
    )
    require_sha(final_binding, FINAL_BINDING_MANIFEST_SHA256)
    timing = json.loads(
        (frozen / "measurement_protocol.json").read_text(encoding="utf-8")
    )["timing"]
    exact_timing = {
        "minimum_measurement_seconds": int(timing["minimum_measurement_seconds"]),
        "initial_probe_seconds": int(timing["initial_probe_seconds"]),
        "decision_step_seconds": int(timing["decision_step_seconds"]),
        "maximum_measurement_seconds": int(timing["maximum_measurement_seconds"]),
    }
    if exact_timing != {
        "minimum_measurement_seconds": 180,
        "initial_probe_seconds": 180,
        "decision_step_seconds": 30,
        "maximum_measurement_seconds": 600,
    }:
        raise RuntimeError(f"unexpected frozen v5 timing: {exact_timing}")

    affected: Dict[int, Dict[str, Any]] = {}
    with args.history.resolve().open("rb") as handle:
        for raw_line in handle:
            if not raw_line.strip():
                continue
            record = json.loads(raw_line)
            marker = (
                ((record.get("final_validation") or {}).get("resource_stats") or {})
                .get("candidate_measurement_protocol")
            )
            if not isinstance(marker, dict):
                continue
            ordinal = marker.get("replay_ordinal")
            if ordinal not in {44, 45}:
                continue
            stability = marker.get("resource_stability") or {}
            if (
                marker.get("initial_probe_seconds") != 300
                or marker.get("minimum_measurement_seconds") != 180
                or marker.get("effective_measurement_seconds") != 180
                or marker.get("measurement_termination_reason") != "stable_windows"
                or stability.get("passed") is not True
                or stability.get("earliest_stable_stop_seconds") != 180
            ):
                raise RuntimeError(f"unexpected affected marker evidence for p{ordinal}")
            point_result_path = Path(str(marker.get("point_result_path") or ""))
            point_result = json.loads(point_result_path.read_text(encoding="utf-8"))
            point_stability = (
                (point_result.get("measurement") or {}).get("resource_stability") or {}
            )
            if (
                point_result.get("status") != "complete"
                or point_stability.get("passed") is not True
                or point_stability.get("earliest_stable_stop_seconds") != 180
            ):
                raise RuntimeError(f"p{ordinal} actual stability evidence is invalid")
            affected[int(ordinal)] = {
                "point_ordinal": int(ordinal),
                "point_result_path": str(point_result_path),
                "point_result_sha256": sha256(point_result_path),
                "history_record_line_sha256": hashlib.sha256(raw_line).hexdigest(),
                "displayed_initial_probe_seconds_in_marker": 300,
                "actual_frozen_initial_probe_seconds": 180,
                "actual_effective_measurement_seconds": 180,
                "actual_earliest_stable_stop_seconds": 180,
                "actual_resource_stability_passed": True,
                "measurement_termination_reason": "stable_windows",
                "validity_preserved": True,
            }
    if set(affected) != {44, 45}:
        raise RuntimeError(f"missing p44/p45 marker evidence: {sorted(affected)}")

    audit_path = attempt / "p44_p45_marker_metadata_discrepancy_audit_v1.json"
    audit = {
        "schema_version": 1,
        "status": "sealed_display_only_metadata_discrepancy_validity_unchanged",
        "affected_points": [affected[44], affected[45]],
        "discrepancy": {
            "field": "candidate_measurement_protocol.initial_probe_seconds",
            "displayed_old_value": 300,
            "authoritative_frozen_value": 180,
            "scope": "bridge marker display field only",
            "did_not_control_measurement": True,
            "did_not_control_stability_decision": True,
            "did_not_change_valid_batch_membership": True,
        },
        "authoritative_v5": {
            "stability_gate_contract_sha256": STABILITY_V5_SHA256,
            "measurement_protocol_sha256": MEASUREMENT_PROTOCOL_SHA256,
            "timing": exact_timing,
        },
        "code_transition": {
            "old_bridge_archive_path": str(old_replay),
            "old_bridge_sha256": OLD_REPLAY_SHA256,
            "corrected_bridge_path": str(new_replay),
            "corrected_bridge_sha256": NEW_REPLAY_SHA256,
            "first_corrected_point_ordinal": 46,
            "corrected_marker_reads_frozen_timing": True,
        },
        "p44_and_p45_remain_valid": True,
        "retroactive_measurement_change": False,
    }
    write_json_new(audit_path, audit)
    audit_sha = sha256(audit_path)

    manifest_path = attempt / "job_tsgd_v5_marker_patch_manifest_v3.json"
    manifest = {
        "schema_version": 3,
        "status": "sealed_v5_marker_corrected_from_p46",
        "final_core_binding_manifest_path": str(final_binding),
        "final_core_binding_manifest_sha256": FINAL_BINDING_MANIFEST_SHA256,
        "metadata_discrepancy_audit_path": str(audit_path),
        "metadata_discrepancy_audit_sha256": audit_sha,
        "formal_replay_bridge_path": str(new_replay),
        "formal_replay_bridge_sha256": NEW_REPLAY_SHA256,
        "first_corrected_point_ordinal": 46,
        "valid_points_before_correction": [44, 45],
        "valid_initial_batch_count_at_seal": 2,
        "current_point_at_seal": 46,
        "counter_state_at_seal": {
            "global": 46,
            "formal_search": 4,
            "accepted": 0,
            "startup_baselines": 1,
        },
        "initial_batch_requires_five_valid_replays": True,
        "acceptance_before_five_valid_replays_forbidden": True,
    }
    for name, expected in {
        "replay_counter.txt": 46,
        "formal_search_replay_counter.txt": 4,
        "formal_search_accepted_counter.txt": 0,
        "formal_startup_baseline_counter.txt": 1,
    }.items():
        actual = int((attempt / name).read_text(encoding="utf-8").strip())
        if actual != expected:
            raise RuntimeError(f"counter drift while sealing marker audit: {name}")
    write_json_new(manifest_path, manifest)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "audit_path": str(audit_path),
                "audit_sha256": audit_sha,
                "manifest_path": str(manifest_path),
                "manifest_sha256": sha256(manifest_path),
                "affected_points": [44, 45],
                "first_corrected_point": 46,
                "valid_initial_batch": "2/5",
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
