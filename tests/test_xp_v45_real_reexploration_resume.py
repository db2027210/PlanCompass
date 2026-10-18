#!/usr/bin/env python3
"""XP v45 zero-call recovery and real EXPLAIN selection integration tests."""

from __future__ import annotations

import copy
from contextlib import nullcontext
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


PROJECT = Path("/home/anonymous/program/resource_adaptive")
TOOLS = PROJECT / "tools"
BUNDLE = Path("/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine")
CAMPAIGN = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
FORMAL = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726/"
    "formal10_after_v3g_entry_gate_20260729T1040"
)
for path in (TOOLS, BUNDLE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import recover_xp_v45_blocked_preworkload_zero_call as recovery
import resume_xp_fresh0_fixed_contract_target10_v36 as v36
import xp_v37_robust_state_patch as robust
import xp_v38_phase_state_patch as phase
import xp_v39_exact_point_pending_trust_patch as pending
import xp_v40_formal_infeasible_reexploration_patch as infeasible
import xp_v41_excluded_ids_sealed_pool_resume_patch as v41
import xp_v42_automatic_transition_family_patch as v42
import xp_v44_pending_post_replay_patch as v44
import xp_v45_recovery_compat_patch as v45


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def installed_runtime() -> tuple[object, type]:
    bundle_module = v36.install_bundle_fallback(BUNDLE)
    phase.install_bundle(bundle_module)
    v41.install_pending_bundle(bundle_module, pending)
    v42.install_bundle(bundle_module)
    explain = importlib.import_module("xp_round10_explain_only_entrypoint")
    v41.install_pool_resume(explain)
    adapter = importlib.import_module("xp_unattended_adapters")
    adapter = importlib.reload(adapter)
    infeasible.install_adapter(adapter)
    robust.install(adapter)
    phase.install_adapter(adapter)
    pending.install_adapter(adapter)
    v44.install_adapter(adapter, pending)
    v45.install_adapter(adapter)
    return adapter, adapter.FormalPaths


def build_paths(formal_paths_type: type, root: Path) -> object:
    return formal_paths_type(
        root=BUNDLE,
        state=root / "state.json",
        batch_root=root / "batches",
        formal_manifest=FORMAL / "formal_manifest.json",
        authority_counter=FORMAL / "state/formal_search_accepted_counter.txt",
        authority_history=FORMAL / "optimizer_history_2026-07-29_104036.jsonl",
        canonical_state=BUNDLE / "fresh0_initial_state.json",
        canonical_manifest=BUNDLE / "fresh0_initialization_manifest.json",
        acceptance_manifest=FORMAL / "formal_manifest.json",
        query_snapshot=BUNDLE / "authority_refresh_accepted4_v5_20260731/query_snapshot_v3.json",
        replay_audit=CAMPAIGN / "batches/seed000/replay_summary.json",
        rank1_evidence=CAMPAIGN / "batches/seed000/selection.json",
        restore_script=Path(
            "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
            "hybench_xp_compute00_59630/experience_sgd/"
            "restore_with_physical_io_reapply_attempt25.sh"
        ),
    )


def test_recovery_is_deterministic_and_formal_state_neutral() -> None:
    before = recovery.validate_before()
    after = recovery.recovered_state(before)
    assert after["status"] == "automatic_reexploration"
    assert after["last_error"] is None
    assert after["active_phase"]["physical_workload_calls"] == 0
    assert after["v45_zero_call_recovery"]["actual_best_top3_gate_bypassed"] is False
    assert recovery.formal_snapshot(after) == recovery.formal_snapshot(before)
    assert recovery.expected_after_sha256(before) == (
        "6682932359b7e5c480d7de14f72cf9b99f1f67d61fbd81cefa729da952de6086"
    )


def test_schema_alias_preserves_strict_failed_gate() -> None:
    adapter, _ = installed_runtime()
    gate = adapter._diagnostic_entry_gate(
        CAMPAIGN / "batches/cont007/selection.json",
        CAMPAIGN / "batches/cont007/replay_summary.json",
    )
    assert gate["passed"] is False
    assert gate["failed_checks"] == ["actual_best_in_predicted_top3"]
    assert gate["checks"]["trusted_spearman_positive"] is True
    assert (
        gate["checks"]["trusted_spearman_positive"]
        is gate["checks"]["trusted_only_spearman_positive"]
    )
    assert gate["actual_best_predicted_rank"] == 4
    assert gate["v45_schema_compatibility"]["threshold_relaxation"] is False


def test_real_campaign_boundary_generates_new_selection_without_workload() -> None:
    state_before = recovery.validate_before()
    recovered = recovery.recovered_state(state_before)
    actual_state_sha = recovery.sha256(CAMPAIGN / "state.json")
    adapter, formal_paths_type = installed_runtime()
    retained_root = os.environ.get("XP_V45_VALIDATION_ROOT")
    context = (
        nullcontext(Path(retained_root))
        if retained_root
        else tempfile.TemporaryDirectory(prefix="xp_v45_real_", dir=PROJECT)
    )
    with context as raw:
        validation_root = Path(raw)
        if retained_root:
            if validation_root.exists():
                raise RuntimeError(f"XP v45 validation root already exists: {validation_root}")
            validation_root.mkdir(parents=True)
        root = validation_root / "campaign"
        source = root / "batches/cont007"
        output = root / "batches/cont008"
        source.mkdir(parents=True)
        output.mkdir(parents=True)
        shutil.copy2(CAMPAIGN / "batches/cont007/selection.json", source / "selection.json")
        shutil.copy2(
            CAMPAIGN / "batches/cont007/replay_summary.json",
            source / "replay_summary.json",
        )
        (root / "state.json").write_text(
            json.dumps(recovered, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        paths = build_paths(formal_paths_type, root)
        selected = adapter.generate_selection(paths, "cont008", recovered, output)
        generated_selection = output / "generated_selection.json"
        generated_selection.write_text(
            json.dumps(selected, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        top5 = list(selected.get("top5") or [])
        identifiers = [
            str((item.get("three_level_fingerprint") or {}).get("id") or "")
            for item in top5
        ]
        assert len(top5) == 5
        assert len(set(identifiers)) == 5 and "" not in identifiers
        assert not (set(identifiers) & set(recovered["replayed_fingerprints"]))
        assert all(item.get("boundary_corner") is False for item in top5)
        assert selected.get("v39_pending_trust") is None
        assert selected["v44_pending_post_replay_reroute"][
            "legacy_probe5_repeat_reused"
        ] is False
        assert selected["v44_pending_post_replay_reroute"]["failed_checks"] == [
            "actual_best_in_predicted_top3"
        ]
        assert (output / "explain_only/explain_only_generation.json").is_file()
        assert not (output / "replay_summary.json").exists()
        assert not (output / "serial_replay").exists()
        assert not list(output.rglob("workload.log"))
        assert recovery.formal_snapshot(recovered) == recovery.formal_snapshot(
            state_before
        )
        assert recovery.sha256(CAMPAIGN / "state.json") == actual_state_sha
        validation = {
            "contract": "xp_v45_real_explain_selection_validation_v1",
            "selection_path": str(generated_selection.resolve()),
            "selection_sha256": hashlib.sha256(
                generated_selection.read_bytes()
            ).hexdigest(),
            "fingerprint_ids": identifiers,
            "candidate_count": 5,
            "all_new_noncorner": True,
            "actual_best_top3_gate_bypassed": False,
            "workload_started": False,
            "formal_state_mutation": False,
        }
        (validation_root / "validation_result.json").write_text(
            json.dumps(validation, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    tests = [
        test_recovery_is_deterministic_and_formal_state_neutral,
        test_schema_alias_preserves_strict_failed_gate,
        test_real_campaign_boundary_generates_new_selection_without_workload,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}", flush=True)
    print(f"PASS {len(tests)}/{len(tests)} XP v45 tests", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
