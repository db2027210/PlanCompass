#!/usr/bin/env python3
"""Seal the TPC-C autonomous target-10 controller package without starting."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.tpcc_autonomous_target10_controller as controller


PROJECT = Path("/home/anonymous/program/resource_adaptive")
ATTEMPT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726"
)
FORMAL_STATE = ATTEMPT / "formal10_tpcc_state.json"
BASELINE = (
    ATTEMPT
    / "candidate_measurements/point_20260729_022349_1052396/summary.json"
)
FORMAL_RUNNER = PROJECT / "tools/run_tpcc_formal_target10_batch.py"
BUILDER = PROJECT / "tools/build_tpcc_autonomous_next_top5_v23.py"
ROBUST_BUILDER = (
    PROJECT / "tools/build_tpcc_round14_robust_regime_top5.py"
)
CONTROLLER = PROJECT / "tools/tpcc_autonomous_target10_controller_v23.py"
SEED_CONTROLLER_STATE = (
    PROJECT
    / "history/tpcc_autonomous_target10_controller_v12_20260731/"
    "controller_state.json"
)
DIAGNOSTIC_RUNNER_ROOT = (
    ATTEMPT / "gradient_debug_round14_robust_regime_interval_v2_20260729T1824"
)
DIAGNOSTIC_RUNNER = (
    DIAGNOSTIC_RUNNER_ROOT
    / "run_tpcc_round11_calibrated_top5_strict_serial.py"
)
DIAGNOSTIC_RUNNER_DEPENDENCIES = (
    DIAGNOSTIC_RUNNER_ROOT / "adaptive_replay_policy.py",
    DIAGNOSTIC_RUNNER_ROOT / "replay_correctness_policy.py",
    DIAGNOSTIC_RUNNER_ROOT / "tpcc_remaining_budget_selector.py",
    PROJECT / "gradient_replay_metrics.py",
    PROJECT / "resource_utility.py",
)
PACKAGE = Path("/tmp/tpcc_autonomous_target10_package_v23_20260731")


def main() -> int:
    if PACKAGE.exists():
        raise FileExistsError(f"package already exists: {PACKAGE}")
    for path in (
        FORMAL_STATE,
        BASELINE,
        FORMAL_RUNNER,
        BUILDER,
        ROBUST_BUILDER,
        CONTROLLER,
        SEED_CONTROLLER_STATE,
        DIAGNOSTIC_RUNNER,
        *DIAGNOSTIC_RUNNER_DEPENDENCIES,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)
    seed_state = controller.load_json(SEED_CONTROLLER_STATE)
    current_run = copy.deepcopy(seed_state.get("current_run"))
    formal_state = controller.load_json(FORMAL_STATE)
    formal_state_sha = controller.sha256(FORMAL_STATE)
    seed_formal_state_sha = seed_state.get("formal_state_sha256")
    migration_bridge = None
    if seed_formal_state_sha != formal_state_sha:
        migrations = list(formal_state.get("range_invariant_migrations") or [])
        if migrations:
            candidate_bridge = migrations[-1]
            if (
                candidate_bridge.get("source_state_sha256")
                == seed_formal_state_sha
                and candidate_bridge.get("contract")
                == "tpcc_formal_range_invariant_migration_v1"
                and int(candidate_bridge.get("workload_invocation_count", -1)) == 0
                and int(candidate_bridge.get("accepted_delta", -1)) == 0
                and int(candidate_bridge.get("incumbent_delta", -1)) == 0
                and int(candidate_bridge.get("accepted_count_preserved", -1))
                == int(formal_state["accepted_count"])
                and int(
                    candidate_bridge.get(
                        "formal_physical_call_count_preserved", -1
                    )
                )
                == int(formal_state["formal_physical_call_count"])
                and int(
                    candidate_bridge.get(
                        "formal_valid_replay_count_preserved", -1
                    )
                )
                == int(formal_state["formal_valid_replay_count"])
                and int(
                    candidate_bridge.get(
                        "formal_technical_failure_count_preserved", -1
                    )
                )
                == int(formal_state["formal_technical_failure_count"])
            ):
                migration_bridge = candidate_bridge
        if migration_bridge is None:
            raise RuntimeError("seed formal-state SHA mismatch")
    seed_sources = list(seed_state.get("sources") or [])
    seed_audits = list(seed_state.get("audits") or [])
    excluded_legacy_seed_audits = []
    for source in seed_sources:
        selection = Path(source["selection_path"]).resolve()
        ledger = Path(source["ledger_path"]).resolve()
        if (
            controller.sha256(selection) != source["selection_sha256"]
            or controller.sha256(ledger) != source["ledger_sha256"]
        ):
            raise RuntimeError(f"source SHA mismatch: {source['name']}")
    pinned_paths = [
        BASELINE,
        FORMAL_RUNNER,
        BUILDER,
        ROBUST_BUILDER,
        CONTROLLER,
        DIAGNOSTIC_RUNNER,
        *DIAGNOSTIC_RUNNER_DEPENDENCIES,
    ]
    pinned = [
        {"path": str(path.resolve()), "sha256": controller.sha256(path)}
        for path in dict.fromkeys(pinned_paths)
    ]
    package_manifest = {
        "schema_version": "tpcc_autonomous_target10_manifest_v1",
        "accepted_target": 10,
        "per_batch_physical_cap": 5,
        "qps_role": "outer_audit_only_not_inner_score",
        "default_mode": "dry_run",
        "armed_requires_exact_manifest_sha256": True,
        "python": "/home/anonymous/miniconda3/envs/resource_adaptive/bin/python",
        "controller_root": str(
            PROJECT / "history/tpcc_autonomous_target10_controller_v12_20260731"
        ),
        "batch_parent": str(
            ATTEMPT / "autonomous_target10_controller_v4/runs"
        ),
        "formal_state_path": str(FORMAL_STATE),
        "initial_formal_state_sha256": formal_state_sha,
        "seed_formal_state_sha256": seed_formal_state_sha,
        "zero_workload_state_migration_bridge": migration_bridge,
        "baseline_path": str(BASELINE),
        "formal_runner_path": str(FORMAL_RUNNER),
        "diagnostic_runner_path": str(DIAGNOSTIC_RUNNER),
        "builder_path": str(BUILDER),
        "poll_seconds": 15,
        "builder_timeout_seconds": 1800,
        "run_timeout_seconds": 21600,
        "pinned_files": pinned,
        "seed_sources": seed_sources,
        "seed_audits": seed_audits,
        "excluded_legacy_seed_audits": excluded_legacy_seed_audits,
        "current_run": current_run,
        "next_ordinal": int(seed_state["next_ordinal"]),
    }
    PACKAGE.mkdir(mode=0o700)
    manifest_path = PACKAGE / "manifest.json"
    controller.atomic_json(manifest_path, package_manifest)
    manifest_sha = controller.sha256(manifest_path)
    shutil.copyfile(CONTROLLER, PACKAGE / CONTROLLER.name)
    dry = PACKAGE / "dry_run_once.sh"
    armed = PACKAGE / "launch_armed_foreground.sh"
    controller.atomic_text(
        dry,
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f'exec "{package_manifest["python"]}" -u '
        f'"{PACKAGE / CONTROLLER.name}" "{manifest_path}" --once\n',
        mode=0o700,
    )
    controller.atomic_text(
        armed,
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f'test "$(sha256sum "{manifest_path}" | awk \'{{print $1}}\')" '
        f'= "{manifest_sha}"\n'
        f'exec "{package_manifest["python"]}" -u '
        f'"{PACKAGE / CONTROLLER.name}" "{manifest_path}" '
        f'--armed --manifest-sha256 "{manifest_sha}"\n',
        mode=0o700,
    )
    output = {
        "schema_version": "tpcc_autonomous_target10_package_v2",
        "prepared_only_no_workload_started": True,
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha,
        "controller_path": str(PACKAGE / CONTROLLER.name),
        "controller_sha256": controller.sha256(PACKAGE / CONTROLLER.name),
        "dry_run_launcher": str(dry),
        "dry_run_launcher_sha256": controller.sha256(dry),
        "armed_launcher": str(armed),
        "armed_launcher_sha256": controller.sha256(armed),
        "current_formal_accepted": controller.load_json(FORMAL_STATE)[
            "accepted_count"
        ],
        "formal_state_sha256": formal_state_sha,
        "seed_formal_state_sha256": seed_formal_state_sha,
        "zero_workload_state_migration_bridge_verified": (
            migration_bridge is not None
        ),
        "target": 10,
        "seed_source_count": len(seed_sources),
        "seed_formal_audit_count": len(seed_audits),
        "excluded_legacy_seed_audit_count": len(
            excluded_legacy_seed_audits
        ),
    }
    controller.atomic_json(PACKAGE / "package_manifest.json", output)
    print(json.dumps(output, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
