#!/usr/bin/env python3
"""Static and runtime preflight for the TPC-C v36 single validation launch."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable

try:
    import build_tpcc_unique_exact_validation_v6 as builder
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v36_unique_exact as v36
except ModuleNotFoundError:
    from tools import build_tpcc_unique_exact_validation_v6 as builder
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v36_unique_exact as v36


FORMAL_SHA256 = "62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0"


def _load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _process_cmdlines() -> Iterable[tuple[int, str]]:
    current = os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == current:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        command = raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
        if command:
            yield int(entry.name), command


def _lock_is_free(path: Path) -> bool:
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return True


def activation_runtime_guard(manifest: Dict[str, Any]) -> Dict[str, Any]:
    old_pid_path = Path(manifest["v36_old_controller_pid_path"]).resolve()
    old_pid = int(old_pid_path.read_text(encoding="utf-8").strip())
    old_pid_live = False
    try:
        os.kill(old_pid, 0)
    except ProcessLookupError:
        pass
    except PermissionError:
        old_pid_live = True
    else:
        old_pid_live = True
    if old_pid_live:
        raise RuntimeError(f"old v34 controller/zombie is still present: pid={old_pid}")

    old_lock = Path(manifest["v36_old_controller_lock_path"]).resolve()
    if not _lock_is_free(old_lock):
        raise RuntimeError("old v34 controller lock is still held")
    new_lock = Path(manifest["controller_root"]).resolve() / "controller.lock"
    if new_lock.exists() and not _lock_is_free(new_lock):
        raise RuntimeError("new v36 controller lock is already held")

    old_batch = str(Path(manifest["v36_old_batch_parent"]).resolve())
    markers = (
        "run_tpcc_round11_calibrated_top5_strict_serial.py",
        "strict_top5_real_replay",
        "benchbase",
        "tpcc_sensitive",
    )
    active = [
        {"pid": pid, "command": command}
        for pid, command in _process_cmdlines()
        if old_batch in command and any(marker in command for marker in markers)
    ]
    if active:
        raise RuntimeError(f"old v34 TPC-C runner/workload is still active: {active}")
    return {
        "old_controller_pid": old_pid,
        "old_controller_live": False,
        "old_controller_lock_free": True,
        "new_controller_lock_free": True,
        "old_runner_matches": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--activation-check", action="store_true")
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    if v25.sha256(manifest_path) != args.manifest_sha256:
        raise RuntimeError("TPC-C v36 activation manifest SHA mismatch")
    manifest = _load(manifest_path)
    v25.verify_pins(manifest)
    if (
        manifest.get("controller_version") != 36
        or Path(manifest["controller_entrypoint"]).name
        != "tpcc_autonomous_target10_controller_v36_unique_exact.py"
        or Path(manifest["exact_repeat_builder_path"]).name
        != "build_tpcc_unique_exact_validation_v6.py"
        or Path(manifest["diagnostic_runner_path"]).name
        != "run_tpcc_v36_unique_exact_strict_serial.py"
        or manifest.get("v36_initial_launch_mode")
        != "single_sealed_diagnostic_validation_only"
        or manifest.get("v36_direct_formal_forbidden") is not True
        or manifest.get("v36_activation_authorized") is not False
        or manifest.get("v36_runner_accounting_contract")
        != "reserved_physical_equals_success_plus_technical_v1"
        or manifest.get("v36_preworkload_failure_contract")
        != "setup_failure_does_not_consume_physical_call_v1"
        or manifest.get("v36_invoked_ranks_contract")
        != "recomputed_from_final_events_v1"
        or manifest.get("per_batch_physical_cap") != 5
        or manifest.get("qps_role") != "outer_audit_only_not_inner_score"
    ):
        raise RuntimeError("TPC-C v36 activation manifest contract mismatch")

    for field in (
        "v36_source_manifest",
        "v36_source_controller_state",
        "v36_source_selection",
        "v36_source_ledger",
        "v36_source_summary",
        "v36_failed_prepare06_selection",
    ):
        item = manifest[field]
        path = Path(item["path"]).resolve()
        if not path.is_file() or v25.sha256(path) != item["sha256"]:
            raise RuntimeError(f"TPC-C v36 takeover source SHA mismatch: {field}")
    source_state = _load(Path(manifest["v36_source_controller_state"]["path"]))
    if (
        source_state.get("status") != "preparing_next_batch"
        or source_state.get("current_run") is not None
        or source_state.get("next_mode") != "diagnostic_validation"
        or source_state.get("next_ordinal") != 6
        or source_state.get("formal_state_sha256") != FORMAL_SHA256
    ):
        raise RuntimeError("TPC-C v36 v34 prepare_06 takeover boundary mismatch")

    source_summary = _load(Path(manifest["v36_source_summary"]["path"]))
    if (
        source_summary.get("real_workload_invocation_count") != 5
        or source_summary.get("technical_failure_count") != 0
        or source_summary.get("accepted_count_delta") != 0
        or source_summary.get("gradient_update_applied") is not False
        or source_summary.get("incumbent_updated") is not False
        or source_summary.get("range_updated") is not False
        or source_summary.get("state_commit_eligible") is not False
        or source_summary.get("baseline_params_restored") is not True
        or source_summary.get("baseline_snapshot_restored") is not True
    ):
        raise RuntimeError("TPC-C v36 clean probe ledger/summary contract mismatch")

    failed = _load(Path(manifest["v36_failed_prepare06_selection"]["path"]))
    failed_ids = [item["three_level_fingerprint"]["id"] for item in failed["top5"]]
    if len(set(failed_ids)) == 5:
        raise RuntimeError("TPC-C v36 failed prepare_06 no longer proves collision")

    formal_path = Path(manifest["formal_state_path"]).resolve()
    formal = _load(formal_path)
    if (
        v25.sha256(formal_path) != FORMAL_SHA256
        or formal.get("accepted_count") != 0
        or formal.get("inherited_accepted") != 0
        or formal.get("formal_physical_call_count") != 0
        or formal.get("formal_valid_replay_count") != 0
        or formal.get("formal_technical_failure_count") != 0
        or formal.get("gradient_update_count") != 0
    ):
        raise RuntimeError("TPC-C v36 formal fresh0 boundary mismatch")

    state_path = Path(manifest["controller_state_path"]).resolve()
    state = _load(state_path)
    current = state.get("current_run") or {}
    selection_path = Path(str(current.get("selection_path") or "")).resolve()
    seal_path = Path(str(current.get("seal_path") or "")).resolve()
    launcher_path = Path(str(current.get("launcher_path") or "")).resolve()
    run_root = Path(str(current.get("root") or "")).resolve()
    if (
        v25.sha256(state_path) != manifest["initial_controller_state_sha256"]
        or state.get("status") != "waiting_current_batch"
        or state.get("next_mode") != "diagnostic_validation"
        or state.get("v36_direct_formal_forbidden") is not True
        or current.get("mode") != "diagnostic_validation"
        or current.get("launch_if_absent") is not True
        or current.get("launcher_invoked") is not False
        or run_root.exists()
        or not selection_path.is_file()
        or v25.sha256(selection_path) != current.get("selection_sha256")
        or not seal_path.is_file()
        or v25.sha256(seal_path) != current.get("seal_sha256")
        or not launcher_path.is_file()
        or v25.sha256(launcher_path) != current.get("launcher_sha256")
        or current.get("formal_state_sha256_before") != FORMAL_SHA256
    ):
        raise RuntimeError("TPC-C v36 sealed activation boundary mismatch")
    seal = _load(seal_path)
    if (
        seal.get("mode") != "diagnostic_validation"
        or seal.get("physical_cap") != 5
        or seal.get("qps_role") != "outer_audit_only_not_inner_score"
    ):
        raise RuntimeError("TPC-C v36 seal is not exact-repeat max5")

    selection = _load(selection_path)
    v25.validate_selection(selection, mode="diagnostic_validation")
    admission = v36.unique_exact_prelaunch_gate_v36(
        selection, "diagnostic_validation", state
    )
    roles = [item["selection_role"] for item in selection["top5"]]
    counts = [int(item["exact_evidence_count"]) for item in selection["top5"]]
    ids = [item["three_level_fingerprint"]["id"] for item in selection["top5"]]
    if (
        admission.get("passed") is not True
        or roles != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or min(counts[:3]) < 2
        or len(set(ids)) != 5
    ):
        raise RuntimeError("TPC-C v36 exact-repeat selection admission mismatch")

    runtime = None
    if args.activation_check:
        runtime = activation_runtime_guard(manifest)
    print(
        json.dumps(
            {
                "passed": True,
                "activation_check": args.activation_check,
                "runtime_guard": runtime,
                "formal_accepted_count": 0,
                "formal_state_sha256": FORMAL_SHA256,
                "initial_mode": "diagnostic_validation",
                "direct_formal_forbidden": True,
                "maximum_real_workload_invocations": 5,
                "selection_roles": roles,
                "exact_evidence_counts": counts,
                "unique_postcalibration_fingerprints": len(set(ids)),
                "run_root_absent": True,
                "workload_calls_added_by_preflight": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
