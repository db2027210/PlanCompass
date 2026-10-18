#!/usr/bin/env python3
"""Seal the rankability-fixed controller around an existing stopped state."""

from __future__ import annotations

import argparse
import fcntl
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.tpcc_autonomous_target10_controller_v23 as controller


PROJECT = Path("/home/anonymous/program/resource_adaptive")
BUILDER = PROJECT / "tools/build_tpcc_autonomous_next_top5_v23.py"
ROBUST_BUILDER = PROJECT / "tools/build_tpcc_round14_robust_regime_top5.py"
REPEAT_BUILDER = PROJECT / "tools/build_tpcc_exact_plan_repeat_validation.py"
CONTROLLER = PROJECT / "tools/tpcc_autonomous_target10_controller_v23.py"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--package", required=True, type=Path)
    args = parser.parse_args()

    source_manifest = args.source_manifest.resolve()
    package = args.package.resolve()
    if package.exists():
        raise FileExistsError(package)
    manifest: Dict[str, Any] = controller.load_json(source_manifest)
    if manifest.get("schema_version") != "tpcc_autonomous_target10_manifest_v1":
        raise RuntimeError("source manifest contract mismatch")
    state_path = Path(manifest["controller_root"]).resolve() / "controller_state.json"
    lock_path = Path(manifest["controller_root"]).resolve() / "controller.lock"
    if not state_path.is_file() or not lock_path.is_file():
        raise RuntimeError("existing controller state/lock missing")

    # Acquiring the existing controller lock proves that no old controller can
    # race the replacement.  The lock is released only after the package is
    # fully sealed.
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = controller.load_json(state_path)
        formal_state = Path(manifest["formal_state_path"]).resolve()
        if state.get("formal_state_sha256") != controller.sha256(formal_state):
            raise RuntimeError("controller/formal-state SHA mismatch")

        # A boundary hand-over may stop the old controller after its child has
        # atomically sealed the summary/ledger but before the parent consumes
        # them.  Preserve that current_run so the replacement controller audits
        # it exactly once on startup.  Anything incomplete, non-diagnostic, or
        # dirty still fails closed.
        pending_completed_run = None
        current = state.get("current_run")
        if current is not None:
            if current.get("mode") != "diagnostic_validation":
                raise RuntimeError(
                    "only a completed diagnostic validation may cross the "
                    "controller hand-over boundary"
                )
            summary_path = Path(current["summary_path"]).resolve()
            ledger_path = Path(current["ledger_path"]).resolve()
            if not summary_path.is_file() or not ledger_path.is_file():
                raise RuntimeError(
                    "old controller still has an incomplete active run"
                )
            if current.get("formal_state_sha256_before") != controller.sha256(
                formal_state
            ):
                raise RuntimeError(
                    "pending diagnostic/formal-state SHA mismatch"
                )
            pending = dict(current)
            pending["summary_sha256"] = controller.sha256(summary_path)
            pending["ledger_sha256"] = controller.sha256(ledger_path)
            pending_completed_run = controller.audit_completed_run(
                pending,
                formal_state,
            )
            if not pending_completed_run.get("clean"):
                raise RuntimeError(
                    "pending diagnostic validation is not clean"
                )

        replacements = {
            str(BUILDER.resolve()): controller.sha256(BUILDER),
            str(ROBUST_BUILDER.resolve()): controller.sha256(ROBUST_BUILDER),
            str(CONTROLLER.resolve()): controller.sha256(CONTROLLER),
        }
        found = set()
        for item in manifest.get("pinned_files") or []:
            path = str(Path(item["path"]).resolve())
            if path in replacements:
                item["sha256"] = replacements[path]
                found.add(path)
        if found != set(replacements):
            raise RuntimeError("source manifest lacks rankability code pins")
        repeat_path = str(REPEAT_BUILDER.resolve())
        repeat_sha = controller.sha256(REPEAT_BUILDER)
        repeat_pins = [
            item for item in manifest.get("pinned_files") or []
            if str(Path(item["path"]).resolve()) == repeat_path
        ]
        if repeat_pins:
            if len(repeat_pins) != 1:
                raise RuntimeError("duplicate exact-repeat builder pins")
            repeat_pins[0]["sha256"] = repeat_sha
        else:
            manifest.setdefault("pinned_files", []).append({
                "path": repeat_path,
                "sha256": repeat_sha,
            })
        manifest["builder_path"] = str(BUILDER.resolve())
        manifest["exact_repeat_builder_path"] = repeat_path
        manifest["rankability_fix_contract"] = (
            "clean_probe5_then_exact_point_repeat_then_two_observation_"
            "median_formal_v3"
        )
        manifest["formal_prediction_contract"] = (
            "two_independent_exact_point_resource_median_rescored_interval_v2"
        )
        manifest["resumed_from_manifest"] = {
            "path": str(source_manifest),
            "sha256": controller.sha256(source_manifest),
            "controller_state_path": str(state_path),
            "controller_state_sha256": controller.sha256(state_path),
        }
        manifest["pending_completed_run_at_handover"] = pending_completed_run

        package.mkdir(mode=0o700)
        manifest_path = package / "manifest.json"
        controller.atomic_json(manifest_path, manifest)
        manifest_sha = controller.sha256(manifest_path)
        packaged_controller = package / CONTROLLER.name
        shutil.copy2(CONTROLLER, packaged_controller)
        launcher = package / "launch_armed_foreground.sh"
        controller.atomic_text(
            launcher,
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            f'test "$(sha256sum "{manifest_path}" | awk \'{{print $1}}\')" '
            f'= "{manifest_sha}"\n'
            f'exec "{manifest["python"]}" -u "{packaged_controller}" '
            f'"{manifest_path}" --armed --manifest-sha256 "{manifest_sha}"\n',
            mode=0o700,
        )
        output = {
            "status": "sealed_resume_no_workload_started",
            "manifest_path": str(manifest_path),
            "manifest_sha256": manifest_sha,
            "controller_path": str(packaged_controller),
            "controller_sha256": controller.sha256(packaged_controller),
            "launcher_path": str(launcher),
            "launcher_sha256": controller.sha256(launcher),
            "controller_state_path": str(state_path),
            "controller_state_sha256": controller.sha256(state_path),
            "formal_state_sha256": controller.sha256(formal_state),
            "accepted_count": int(
                controller.load_json(formal_state)["accepted_count"]
            ),
        }
        controller.atomic_json(package / "package_manifest.json", output)
    print(json.dumps(output, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
