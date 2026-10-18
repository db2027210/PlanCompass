#!/usr/bin/env python3
"""Safely replace XP v18 with v19 at the cont012/cont013 batch boundary."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Dict, List


OLD_PID = 851594
CAMPAIGN = Path(
    "/home/anonymous/program/resource_adaptive/"
    "history/xp_final_algorithm_fresh10_v26_20260731T1935"
)
STATE = CAMPAIGN / "state.json"
CONT012 = CAMPAIGN / "batches/cont012"
SENTINEL_DIR = CAMPAIGN / "batches/cont013"
SENTINEL = SENTINEL_DIR / "selection.json"
NEW_BUNDLE = Path(
    "/tmp/pg_xp_unattended_20260730_1235/"
    "bundle_fix_v19_two_observation"
)
EXPECTED_SENTINEL_SHA256 = (
    "a99e9e179e8492323faad3d166f44bc9514b2476e7814e8f3b98fbaa03489195"
)
EXPECTED_MANIFEST_SHA256 = (
    "4c59b0372888317bd590123feace97127a4775986f55367b96799791328a1712"
)
STATIC_RESUME_ARGV = [
    "/home/anonymous/miniconda3/envs/py310/bin/python3",
    "-u",
    "/home/anonymous/program/resource_adaptive/tools/resume_xp_fresh0_fixed_contract_target10_v28.py",
    "--resume-existing",
    "--bundle-root",
    "/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v18_discovery_headroom",
    "--selection",
    "/home/anonymous/program/resource_adaptive/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/selection.json",
    "--replay",
    "/home/anonymous/program/resource_adaptive/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/replay_summary.json",
    "--training-state",
    "/home/anonymous/program/resource_adaptive/history/xp_autonomous_formal_target10_v1_20260731/state.json",
    "--campaign-root",
    str(CAMPAIGN),
    "--formal-manifest",
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245/formal_manifest.json",
    "--authority-counter",
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245/state/formal_search_accepted_counter.txt",
    "--authority-history",
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245/optimizer_history_2026-07-29_225003.jsonl",
    "--canonical-state",
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_canonical_after_unreserved_call_20260729T1448/canonical_state.json",
    "--canonical-manifest",
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_canonical_after_unreserved_call_20260729T1448/manifest.json",
    "--acceptance-manifest",
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_cont1_accepted4_cap5_feedback_v1_20260729T1729/formal_manifest.json",
    "--query-snapshot",
    "/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v18_discovery_headroom/authority_refresh_accepted4_v5_20260731/query_snapshot_v3.json",
    "--restore-script",
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/restore_with_physical_io_reapply_attempt25.sh",
]


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command_line(pid: int) -> List[str]:
    deadline = time.monotonic() + 30
    path = Path(f"/proc/{pid}/cmdline")
    while time.monotonic() < deadline:
        try:
            values = [
                item.decode("utf-8")
                for item in path.read_bytes().split(b"\0")
                if item
            ]
        except FileNotFoundError:
            values = []
        if values:
            return values
        try:
            process_state = Path(f"/proc/{pid}/stat").read_text(
                encoding="utf-8"
            ).split()[2]
        except (FileNotFoundError, IndexError):
            process_state = ""
        if process_state == "Z":
            return list(STATIC_RESUME_ARGV)
        time.sleep(1)
    raise RuntimeError("cannot capture old XP controller command line")


def process_alive(pid: int) -> bool:
    try:
        if Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[2] == "Z":
            return False
    except (FileNotFoundError, IndexError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def main() -> int:
    argv = command_line(OLD_PID)
    if "resume_xp_fresh0_fixed_contract_target10_v28.py" not in argv[2]:
        raise RuntimeError("unexpected old XP controller executable")
    bundle_index = argv.index("--bundle-root") + 1
    if not argv[bundle_index].endswith("bundle_fix_v18_discovery_headroom"):
        raise RuntimeError("old XP controller is not using pinned v18 bundle")
    handover_audit_path = CAMPAIGN / "v19_two_observation_handover.json"
    already_handed_over = not SENTINEL.exists() and handover_audit_path.is_file()
    existing_audit: Dict[str, Any] = {}
    if already_handed_over:
        existing_audit = load(handover_audit_path)
        quarantine = Path(str(existing_audit.get("quarantined_sentinel") or ""))
        if not (
            int(existing_audit.get("accepted_count", -1)) == 1
            and existing_audit.get("new_bundle_manifest_sha256")
            == EXPECTED_MANIFEST_SHA256
            and int(existing_audit.get("workload_calls_added_by_handover", -1)) == 0
            and quarantine.is_dir()
            and sha256(quarantine / "selection.json") == EXPECTED_SENTINEL_SHA256
        ):
            raise RuntimeError("existing XP v19 handover audit mismatch")
    elif sha256(SENTINEL) != EXPECTED_SENTINEL_SHA256:
        raise RuntimeError("XP handover sentinel SHA mismatch")
    if sha256(NEW_BUNDLE / "bundle_manifest.json") != EXPECTED_MANIFEST_SHA256:
        raise RuntimeError("XP v19 manifest SHA mismatch")

    print("waiting for XP cont012 clean completion and fail-closed boundary", flush=True)
    deadline = time.monotonic() + 4 * 60 * 60
    while time.monotonic() < deadline:
        summary_path = CONT012 / "replay_summary.json"
        if summary_path.is_file():
            state = load(STATE)
            history = state.get("batch_history") or []
            boundary = state.get("last_batch") or {}
            if (
                int(state.get("accepted_count", -1)) == 1
                and int(state.get("next_batch_index", -1)) == 13
                and state.get("status") == "blocked_no_candidates"
                and boundary.get("batch_id") == "cont013"
                and boundary.get("status") == "no_candidates"
                and history
                and history[-1].get("batch_id") == "cont012"
                and history[-1].get("status") == "complete"
            ):
                break
        time.sleep(5)
    else:
        raise RuntimeError("timed out waiting for XP safe handover boundary")

    summary = load(CONT012 / "replay_summary.json")
    selection = load(CONT012 / "selection.json")
    if not (
        selection.get("batch_mode") == "diagnostic_validation"
        and int(summary.get("physical_workload_invocation_count", -1)) == 5
        and int(summary.get("successful_real_replay_count", -1)) == 5
        and int(summary.get("technical_failure_count", -1)) == 0
        and summary.get("baseline_restore_verified") is True
        and summary.get("diagnostic_only") is True
        and int(summary.get("accepted_count_delta", -1)) == 0
        and summary.get("accepted_update_applied") is False
        and summary.get("gradient_update_applied") is False
        and summary.get("incumbent_updated") is False
        and summary.get("range_updated") is False
    ):
        raise RuntimeError("XP cont012 is not a clean state-neutral diagnostic")

    while process_alive(OLD_PID):
        if time.monotonic() >= deadline:
            raise RuntimeError("old XP controller did not exit at boundary")
        time.sleep(1)

    if already_handed_over:
        quarantine = Path(str(existing_audit["quarantined_sentinel"]))
    else:
        timestamp = time.strftime("%Y%m%dT%H%M%S")
        quarantine = CAMPAIGN / f"quarantine_cont013_v19_handover_{timestamp}"
        if quarantine.exists():
            raise RuntimeError(f"quarantine target already exists: {quarantine}")
        os.replace(SENTINEL_DIR, quarantine)

    argv[bundle_index] = str(NEW_BUNDLE)
    audit = {
        "accepted_count": 1,
        "cont012_replay_sha256": sha256(CONT012 / "replay_summary.json"),
        "new_bundle_manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "new_bundle_root": str(NEW_BUNDLE),
        "old_pid": OLD_PID,
        "quarantined_sentinel": str(quarantine),
        "workload_calls_added_by_handover": 0,
    }
    if not already_handed_over:
        handover_audit_path.write_text(
            json.dumps(audit, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print("XP safe boundary verified; execing v19 controller", flush=True)
    os.execv(argv[0], argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
