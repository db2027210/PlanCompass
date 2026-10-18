#!/usr/bin/env python3
"""TPC-C v36 controller with post-calibration unique exact-evidence admission."""

from __future__ import annotations

import copy
import fcntl
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Mapping

try:
    import build_tpcc_unique_exact_validation_v6 as repair_v6
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v28_role_aware as v28
    import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29
    import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31
    import tpcc_autonomous_target10_controller_v34_heterogeneous_repeat as v34
except ModuleNotFoundError:
    from tools import build_tpcc_unique_exact_validation_v6 as repair_v6
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v28_role_aware as v28
    from tools import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29
    from tools import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31
    from tools import tpcc_autonomous_target10_controller_v34_heterogeneous_repeat as v34


def unique_exact_prelaunch_gate_v36(
    selection: Mapping[str, Any],
    mode: str,
    controller_state: Mapping[str, Any],
) -> Dict[str, Any]:
    """Rebuild the selection from sealed evidence before allowing a seal."""
    global_gate = v29.global_prelaunch_fingerprint_gate(
        selection, mode, controller_state
    )
    if mode != "diagnostic_validation":
        return global_gate
    contract = selection.get("v36_repair_contract")
    if not isinstance(contract, Mapping) or contract.get("contract") != repair_v6.REPAIR_CONTRACT:
        raise RuntimeError("diagnostic validation lacks v36 repair contract")
    source_selection_path = Path(
        str(contract.get("source_selection_path") or "")
    ).resolve()
    source_ledger_path = Path(str(contract.get("source_ledger_path") or "")).resolve()
    if (
        not source_selection_path.is_file()
        or not source_ledger_path.is_file()
        or repair_v6._sha256(source_selection_path)
        != contract.get("source_selection_sha256")
        or repair_v6._sha256(source_ledger_path)
        != contract.get("source_ledger_sha256")
    ):
        raise RuntimeError("v36 repair source SHA mismatch")

    rebuilt = repair_v6.build(
        repair_v6._load(source_selection_path),
        source_selection_path=source_selection_path,
        source_ledger_path=source_ledger_path,
    )
    if repair_v6._canonical_sha256(rebuilt) != repair_v6._canonical_sha256(selection):
        raise RuntimeError("v36 selection is not the deterministic evidence rebuild")

    candidates = list(selection.get("top5") or [])
    ids = [str(item["three_level_fingerprint"]["id"]) for item in candidates]
    roles = [str(item.get("selection_role") or "") for item in candidates]
    counts = [int(item.get("exact_evidence_count", -1)) for item in candidates]
    if (
        len(candidates) != v25.CAP
        or len(set(ids)) != v25.CAP
        or roles
        != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or min(counts[:3]) < 2
        or any(count < 2 and role != "uncertain_probe" for count, role in zip(counts, roles))
        or contract.get("postcalibration_fingerprint_unique") is not True
        or contract.get("unknown_exact_points_must_be_probes") is not True
        or contract.get("state_neutral") is not True
        or int(contract.get("physical_workload_calls_added", -1)) != 0
        or selection.get("maximum_real_workload_invocations") != v25.CAP
        or selection.get("qps_role") != "outer_audit_only_not_inner_score"
        or selection.get("state_mutation_contract")
        != {
            "accepted_update": False,
            "gradient_update": False,
            "incumbent_update": False,
            "range_update": False,
        }
    ):
        raise RuntimeError("v36 exact evidence/role/state-neutral contract mismatch")

    result = copy.deepcopy(global_gate)
    result.update(
        {
            "contract": "tpcc_postcalibration_unique_exact_admission_v36",
            "passed": True,
            "collision_ids": [],
            "allowed_historical_exact_evidence_collision_ids": copy.deepcopy(
                global_gate.get("collision_ids") or []
            ),
            "candidate_fingerprint_ids": ids,
            "exact_evidence_counts": counts,
            "minimum_trusted_exact_clean_observations": min(counts[:3]),
            "postcalibration_unique": True,
            "deterministic_rebuild_verified": True,
            "workload_calls_added": 0,
            "manual_candidate_injection": False,
        }
    )
    return result


def launch_single_sealed_validation(manifest_path: Path, manifest_sha256: str) -> int:
    """Launch exactly the sealed validation batch and stop before any audit/next mode."""
    if v25.sha256(manifest_path) != manifest_sha256:
        raise RuntimeError("v36 single-run activation manifest SHA mismatch")
    manifest = v25.load_json(manifest_path)
    v25.verify_pins(manifest)
    if (
        manifest.get("controller_version") != 36
        or manifest.get("v36_initial_launch_mode") != "single_sealed_diagnostic_validation_only"
        or manifest.get("v36_direct_formal_forbidden") is not True
    ):
        raise RuntimeError("v36 single-run activation contract mismatch")
    runtime_preflight = Path(manifest["v36_activation_preflight_path"]).resolve()
    completed_preflight = subprocess.run(
        [
            str(manifest["python"]),
            "-u",
            str(runtime_preflight),
            str(manifest_path),
            "--manifest-sha256",
            manifest_sha256,
            "--activation-check",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if completed_preflight.returncode != 0:
        raise RuntimeError(
            "v36 activation runtime preflight failed:\n"
            + completed_preflight.stdout
        )

    root = Path(manifest["controller_root"]).resolve()
    state_path = Path(manifest["controller_state_path"]).resolve()
    formal_path = Path(manifest["formal_state_path"]).resolve()
    lock_path = root / "controller.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = v25.load_json(state_path)
        current = state.get("current_run") or {}
        selection_path = Path(str(current.get("selection_path") or "")).resolve()
        launcher_path = Path(str(current.get("launcher_path") or "")).resolve()
        run_root = Path(str(current.get("root") or "")).resolve()
        if (
            state.get("status") != "waiting_current_batch"
            or current.get("mode") != "diagnostic_validation"
            or current.get("launch_if_absent") is not True
            or current.get("launcher_invoked") is not False
            or run_root.exists()
            or not selection_path.is_file()
            or v25.sha256(selection_path) != current.get("selection_sha256")
            or not launcher_path.is_file()
            or v25.sha256(launcher_path) != current.get("launcher_sha256")
            or v25.sha256(formal_path) != current.get("formal_state_sha256_before")
        ):
            raise RuntimeError("v36 sealed single-validation boundary mismatch")
        selection = v25.load_json(selection_path)
        v25.validate_selection(selection, mode="diagnostic_validation")
        admission = unique_exact_prelaunch_gate_v36(
            selection, "diagnostic_validation", state
        )
        if admission.get("passed") is not True:
            raise RuntimeError("v36 sealed validation admission failed")

        current["launcher_invoked"] = True
        state["status"] = "launching_single_sealed_diagnostic_validation"
        state["v36_direct_formal_forbidden"] = True
        state["v36_single_batch_admission"] = admission
        v25.atomic_json(state_path, state)
        completed = subprocess.run(
            ["bash", str(launcher_path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=int(manifest.get("run_timeout_seconds", 21600)),
        )
        v25.atomic_text(root / "single_sealed_launcher.log", completed.stdout)
        state["last_launcher_returncode"] = completed.returncode
        state["formal_state_sha256"] = v25.sha256(formal_path)
        state["status"] = "awaiting_postrun_audit_single_batch_boundary"
        state["next_mode"] = "postrun_audit_required_before_any_next_batch"
        v25.atomic_json(state_path, state)
        return int(completed.returncode)


def main() -> int:
    manifest_path = Path(sys.argv[1]).resolve()
    if "--single-sealed-run" in sys.argv:
        try:
            manifest_sha = sys.argv[sys.argv.index("--manifest-sha256") + 1]
        except (ValueError, IndexError) as exc:
            raise RuntimeError(
                "v36 single sealed run requires --manifest-sha256"
            ) from exc
        return launch_single_sealed_validation(manifest_path, manifest_sha)
    manifest = v25.load_json(manifest_path)
    builder = Path(str(manifest.get("exact_repeat_builder_path") or "")).resolve()
    if builder.name != "build_tpcc_unique_exact_validation_v6.py":
        raise RuntimeError("v36 requires the unique exact-evidence v6 builder")
    v31._configure(manifest_path)
    v29._configure_from_argv(sys.argv)
    v25.prelaunch_fingerprint_gate = unique_exact_prelaunch_gate_v36
    v25.accumulated_observation_formal_selection = (
        v28.accumulated_observation_formal_selection_v28
    )
    v25.audit_completed_run = v34.audit_completed_run_v34
    v25.quality_gate = v34.quality_gate_v34
    v25.build_exact_calibration = v31.build_exact_calibration_v31
    v25.choose_next_mode = v34.choose_next_mode_v34
    return v25.main()


if __name__ == "__main__":
    raise SystemExit(main())
