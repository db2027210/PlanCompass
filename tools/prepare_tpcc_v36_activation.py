#!/usr/bin/env python3
"""Prepare, but do not start, the TPC-C v36 single-validation takeover."""

from __future__ import annotations

import copy
import json
from pathlib import Path

try:
    import build_tpcc_unique_exact_validation_v6 as builder
    import tpcc_autonomous_target10_controller_v25 as v25
except ModuleNotFoundError:
    from tools import build_tpcc_unique_exact_validation_v6 as builder
    from tools import tpcc_autonomous_target10_controller_v25 as v25


PROJECT = Path("/home/anonymous/program/resource_adaptive")
V34 = PROJECT / "history/tpcc_final_algorithm_fresh0_v34_20260803"
SOURCE_MANIFEST = V34 / "manifest.json"
SOURCE_STATE = V34 / "autonomous_controller/controller_state.json"
SOURCE_SELECTION = V34 / "autonomous_controller/prepare_05/sealed_run/top5.json"
SOURCE_LEDGER = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh0_v34_20260803/autonomous_runs_v34/"
    "autonomous_diagnostic_05/strict_top5_real_replay_round12_ledger_v1.json"
)
SOURCE_SUMMARY = SOURCE_LEDGER.parent / "top5_real_replay_round12_diagnostic_v1/summary.json"
FAILED_SELECTION = V34 / "autonomous_controller/prepare_06/top5.json"
FAILED_LOG = V34 / "controller.log"
OLD_PID = V34 / "controller.pid"
OLD_LOCK = V34 / "autonomous_controller/controller.lock"
SOURCE_FORMAL = V34 / "formal_state.json"

EXPECTED = {
    SOURCE_MANIFEST: "10741e90a8b5f6e03b4f3dbf9d394be6cf8a19a31816a68414335a26022b9289",
    SOURCE_STATE: "51a2545e715f499de1fcee928afaac64e55387ec77305719ad31efe87042ed19",
    SOURCE_SELECTION: "d8e6dd04fc788ac9d447b344a9b1f15dc7b841a071c6d32e7530f6f81db7a32a",
    SOURCE_LEDGER: "d2928067051584742adbc4ccc158e8acaaff646498dead514a8fc7aa6e4d9d49",
    SOURCE_SUMMARY: "5831c628280bf0e05811dfaa98632eb0f8b2f100a525f149e1ae8ad95ded3f2e",
    FAILED_SELECTION: "59b94f6acdbb8131d141fcb829976ff449e095cf58bcb3d63a5e94ce6bf0d40e",
    FAILED_LOG: "deedb4969f1768f41b2837e051c36629b77014ddb60b5e525d7470a2830be73f",
    SOURCE_FORMAL: "62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0",
}

TARGET = PROJECT / "history/tpcc_final_algorithm_fresh0_v36_20260803"
CONTROL = TARGET / "autonomous_controller"
PREPARE = CONTROL / "prepare_01"
SELECTION = PREPARE / "top5.json"
REBUILD = PREPARE / "top5_rebuild.json"
STATE = CONTROL / "controller_state.json"
FORMAL = TARGET / "formal_state.json"
MANIFEST = TARGET / "manifest.json"
DRY_RUN = TARGET / "dry_run_preflight.sh"
ACTIVATE = TARGET / "launch_single_validation.sh"
BATCH_PARENT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh0_v36_20260803/autonomous_runs_v36"
)


def _pin(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": v25.sha256(path.resolve())}


def main() -> int:
    mismatches = {
        str(path): {"expected": expected, "observed": v25.sha256(path)}
        for path, expected in EXPECTED.items()
        if not path.is_file() or v25.sha256(path) != expected
    }
    if mismatches:
        raise RuntimeError(f"TPC-C v36 takeover source SHA mismatch: {mismatches}")
    if TARGET.exists() or BATCH_PARENT.exists():
        raise RuntimeError("TPC-C v36 activation target already exists")
    source_state = v25.load_json(SOURCE_STATE)
    if (
        source_state.get("status") != "preparing_next_batch"
        or source_state.get("current_run") is not None
        or source_state.get("next_mode") != "diagnostic_validation"
        or source_state.get("next_ordinal") != 6
        or source_state.get("formal_state_sha256") != EXPECTED[SOURCE_FORMAL]
    ):
        raise RuntimeError("TPC-C v36 source is not failed prepare_06 boundary")

    TARGET.mkdir(parents=True, mode=0o700)
    PREPARE.mkdir(parents=True, mode=0o700)
    v25.atomic_text(FORMAL, SOURCE_FORMAL.read_text(encoding="utf-8"))
    source_selection = v25.load_json(SOURCE_SELECTION)
    first = builder.build(
        source_selection,
        source_selection_path=SOURCE_SELECTION,
        source_ledger_path=SOURCE_LEDGER,
    )
    second = builder.build(
        source_selection,
        source_selection_path=SOURCE_SELECTION,
        source_ledger_path=SOURCE_LEDGER,
    )
    v25.atomic_json(SELECTION, first)
    v25.atomic_json(REBUILD, second)
    if SELECTION.read_bytes() != REBUILD.read_bytes():
        raise RuntimeError("TPC-C v36 takeover double build differs")

    manifest = v25.load_json(SOURCE_MANIFEST)
    controller = PROJECT / "tools/tpcc_autonomous_target10_controller_v36_unique_exact.py"
    repeat_builder = PROJECT / "tools/build_tpcc_unique_exact_validation_v6.py"
    diagnostic_runner = PROJECT / "tools/run_tpcc_v36_unique_exact_strict_serial.py"
    preflight = PROJECT / "tools/preflight_tpcc_v36_activation.py"
    prepare_tool = PROJECT / "tools/prepare_tpcc_v36_activation.py"
    test_path = PROJECT / "tests/test_tpcc_v36_unique_exact.py"
    runner_test_path = PROJECT / "tests/test_tpcc_v36_runner_accounting.py"
    manifest.update(
        {
            "controller_version": 36,
            "controller_entrypoint": str(controller),
            "controller_root": str(CONTROL),
            "controller_state_path": str(STATE),
            "batch_parent": str(BATCH_PARENT),
            "formal_state_path": str(FORMAL),
            "initial_formal_state_sha256": v25.sha256(FORMAL),
            "diagnostic_runner_path": str(diagnostic_runner),
            "exact_repeat_builder_path": str(repeat_builder),
            "current_run": None,
            "next_ordinal": 2,
            "default_mode": "activation_requires_exact_manifest_sha",
            "v36_activation_authorized": False,
            "v36_initial_launch_mode": "single_sealed_diagnostic_validation_only",
            "v36_direct_formal_forbidden": True,
            "v36_postrun_action": "stop_at_audit_boundary_before_any_next_mode",
            "v36_runner_accounting_contract": (
                "reserved_physical_equals_success_plus_technical_v1"
            ),
            "v36_preworkload_failure_contract": (
                "setup_failure_does_not_consume_physical_call_v1"
            ),
            "v36_invoked_ranks_contract": "recomputed_from_final_events_v1",
            "v36_activation_preflight_path": str(preflight),
            "v36_source_manifest": _pin(SOURCE_MANIFEST),
            "v36_source_controller_state": _pin(SOURCE_STATE),
            "v36_source_selection": _pin(SOURCE_SELECTION),
            "v36_source_ledger": _pin(SOURCE_LEDGER),
            "v36_source_summary": _pin(SOURCE_SUMMARY),
            "v36_failed_prepare06_selection": _pin(FAILED_SELECTION),
            "v36_failed_controller_log": _pin(FAILED_LOG),
            "v36_old_controller_pid_path": str(OLD_PID),
            "v36_old_controller_lock_path": str(OLD_LOCK),
            "v36_old_batch_parent": str(Path(v25.load_json(SOURCE_MANIFEST)["batch_parent"]).resolve()),
            "v36_takeover_boundary": "v34_prepare_06_postcalibration_duplicate_fail_closed",
            "v36_double_build_selection_sha256": v25.sha256(SELECTION),
            "v36_selection_roles": [item["selection_role"] for item in first["top5"]],
            "v36_selection_exact_evidence_counts": [
                item["exact_evidence_count"] for item in first["top5"]
            ],
            "v36_state_neutral_preparation": True,
            "v36_preparation_physical_workload_calls_added": 0,
        }
    )
    pins = {
        str(Path(item["path"]).resolve()): copy.deepcopy(item)
        for item in manifest.get("pinned_files") or []
    }
    for path in (
        controller,
        repeat_builder,
        diagnostic_runner,
        preflight,
        prepare_tool,
        test_path,
        runner_test_path,
        SOURCE_MANIFEST,
        SOURCE_STATE,
        SOURCE_SELECTION,
        SOURCE_LEDGER,
        SOURCE_SUMMARY,
        FAILED_SELECTION,
        FAILED_LOG,
        FORMAL,
        SELECTION,
        REBUILD,
    ):
        pins[str(path.resolve())] = _pin(path)
    manifest["pinned_files"] = [pins[key] for key in sorted(pins)]

    seal = v25.seal_next_run(
        manifest,
        PREPARE,
        SELECTION,
        "diagnostic_validation",
        1,
        {
            "passed": False,
            "formal_start_authorized": False,
            "source": "v36_unique_exact_single_validation_only",
        },
    )
    current = {
        "mode": "diagnostic_validation",
        "root": seal["run_root"],
        "selection_path": seal["selection_path"],
        "selection_sha256": seal["selection_sha256"],
        "ledger_path": seal["ledger_path"],
        "summary_path": seal["summary_path"],
        "launcher_path": seal["launcher_path"],
        "launcher_sha256": seal["launcher_sha256"],
        "seal_path": seal["seal_path"],
        "seal_sha256": seal["seal_sha256"],
        "formal_state_sha256_before": seal["formal_state_sha256_before"],
        "launch_if_absent": True,
        "launcher_invoked": False,
    }
    state = {
        "schema_version": "tpcc_autonomous_target10_state_v1",
        "status": "waiting_current_batch",
        "dry_run": False,
        "fresh0_contract": "tpcc_v36_unique_exact_fresh0_v1",
        "historical_accepted_excluded": 2,
        "audits": copy.deepcopy(source_state.get("audits") or []),
        "sources": copy.deepcopy(source_state.get("sources") or []),
        "quarantined_fingerprints": copy.deepcopy(
            source_state.get("quarantined_fingerprints") or []
        ),
        "acquisition_transitions": copy.deepcopy(
            source_state.get("acquisition_transitions") or []
        ),
        "current_run": current,
        "next_ordinal": 2,
        "next_mode": "diagnostic_validation",
        "formal_state_sha256": v25.sha256(FORMAL),
        "last_quality_gate": copy.deepcopy(source_state.get("last_quality_gate")),
        "v36_takeover_source_state_sha256": v25.sha256(SOURCE_STATE),
        "v36_takeover_failed_prepare06_selection_sha256": v25.sha256(FAILED_SELECTION),
        "v36_direct_formal_forbidden": True,
        "v36_single_batch_only": True,
    }
    v25.atomic_json(STATE, state)
    manifest["current_run"] = copy.deepcopy(current)
    manifest["seed_audits"] = copy.deepcopy(state["audits"])
    manifest["seed_sources"] = copy.deepcopy(state["sources"])
    manifest["initial_controller_state_sha256"] = v25.sha256(STATE)
    for path in (
        Path(seal["selection_path"]),
        Path(seal["launcher_path"]),
        Path(seal["seal_path"]),
    ):
        pins[str(path.resolve())] = _pin(path)
    manifest["pinned_files"] = [pins[key] for key in sorted(pins)]
    v25.atomic_json(MANIFEST, manifest)
    manifest_sha = v25.sha256(MANIFEST)
    python = str(manifest["python"])
    preflight_sha = v25.sha256(preflight)
    controller_sha = v25.sha256(controller)
    dry_script = f"""#!/usr/bin/env bash
set -euo pipefail
manifest={MANIFEST}
preflight={preflight}
python={python}
manifest_sha={manifest_sha}
test "$(sha256sum "${{manifest}}" | awk '{{print $1}}')" = "${{manifest_sha}}"
exec "${{python}}" -u "${{preflight}}" "${{manifest}}" \\
  --manifest-sha256 "${{manifest_sha}}" --activation-check
"""
    v25.atomic_text(DRY_RUN, dry_script, mode=0o700)
    activation_script = f"""#!/usr/bin/env bash
set -euo pipefail
manifest={MANIFEST}
controller={controller}
preflight={preflight}
python={python}
manifest_sha={manifest_sha}
controller_sha={controller_sha}
preflight_sha={preflight_sha}
session=tpcc_fresh0_v36_single_validation
test "$(sha256sum "${{manifest}}" | awk '{{print $1}}')" = "${{manifest_sha}}"
test "$(sha256sum "${{controller}}" | awk '{{print $1}}')" = "${{controller_sha}}"
test "$(sha256sum "${{preflight}}" | awk '{{print $1}}')" = "${{preflight_sha}}"
"${{python}}" -u "${{preflight}}" "${{manifest}}" \\
  --manifest-sha256 "${{manifest_sha}}" --activation-check
if tmux has-session -t "${{session}}" 2>/dev/null; then
  echo "TPC-C v36 session already exists" >&2
  exit 73
fi
tmux new-session -d -s "${{session}}" \\
  "exec '${{python}}' -u '${{controller}}' '${{manifest}}' --armed --manifest-sha256 '${{manifest_sha}}' --single-sealed-run >> '{TARGET}/controller.log' 2>&1"
sleep 2
tmux has-session -t "${{session}}"
pane_pid="$(tmux list-panes -t "${{session}}" -F '#{{pane_pid}}')"
test -n "${{pane_pid}}"
kill -0 "${{pane_pid}}"
printf '%s\n' "${{pane_pid}}" > "{TARGET}/controller.pid"
printf 'TPCC_V36_SINGLE_VALIDATION_STARTED session=%s pid=%s\n' "${{session}}" "${{pane_pid}}"
"""
    v25.atomic_text(ACTIVATE, activation_script, mode=0o700)
    report = {
        "contract": "tpcc_v36_activation_ready_single_validation_v1",
        "passed": True,
        "manifest_path": str(MANIFEST),
        "manifest_sha256": manifest_sha,
        "controller_path": str(controller),
        "controller_sha256": controller_sha,
        "builder_path": str(repeat_builder),
        "builder_sha256": v25.sha256(repeat_builder),
        "preflight_path": str(preflight),
        "preflight_sha256": preflight_sha,
        "dry_run_launcher_path": str(DRY_RUN),
        "dry_run_launcher_sha256": v25.sha256(DRY_RUN),
        "activation_launcher_path": str(ACTIVATE),
        "activation_launcher_sha256": v25.sha256(ACTIVATE),
        "sealed_selection_sha256": current["selection_sha256"],
        "formal_state_sha256": v25.sha256(FORMAL),
        "initial_mode": "diagnostic_validation",
        "direct_formal_forbidden": True,
        "maximum_real_workload_invocations": 5,
        "physical_workload_calls_added": 0,
        "activation_started": False,
    }
    v25.atomic_json(TARGET / "activation_ready_report.json", report)
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
