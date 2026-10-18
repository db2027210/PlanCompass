#!/usr/bin/env python3
"""Build a dormant zero-workload TPC-C v35 handoff package."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import tpcc_autonomous_target10_controller_v25 as v25


PROJECT = Path("/home/anonymous/program/resource_adaptive")
SOURCE_MANIFEST = (
    PROJECT / "history/tpcc_final_algorithm_fresh0_v34_20260803/manifest.json"
)
SOURCE_MANIFEST_SHA256 = (
    "10741e90a8b5f6e03b4f3dbf9d394be6cf8a19a31816a68414335a26022b9289"
)
PACKAGE = PROJECT / "history/tpcc_v35_preworkload_block_package_20260803"
MANIFEST = PACKAGE / "manifest.json"
LAUNCHER = PACKAGE / "launch_v35_handoff.sh"


def main() -> int:
    if PACKAGE.exists():
        raise RuntimeError("TPC-C v35 package already exists")
    if v25.sha256(SOURCE_MANIFEST) != SOURCE_MANIFEST_SHA256:
        raise RuntimeError("TPC-C v35 source manifest SHA mismatch")
    controller = (
        PROJECT
        / "tools/tpcc_autonomous_target10_controller_v35_preworkload_block.py"
    )
    preflight = PROJECT / "tools/preflight_tpcc_v35_preworkload_block.py"
    package_builder = PROJECT / "tools/package_tpcc_v35_preworkload_block.py"
    test_path = PROJECT / "tests/test_tpcc_v35_preworkload_block.py"
    manifest = v25.load_json(SOURCE_MANIFEST)
    manifest.update(
        {
            "controller_version": 35,
            "current_run": None,
            "v35_zero_call_handoff": True,
            "v35_activation_state": "awaiting_failed_group5_no_active_replay",
            "v35_activation_requires_failed_group5": True,
            "v35_activation_requires_controller_lock": True,
            "v35_builder_success_action": "launch_new_novel_probe5_diagnostic",
            "v35_builder_failure_status": "blocked_preworkload_no_novel_top5",
            "v35_no_manual_candidate_injection": True,
            "v35_no_gate_bypass": True,
            "v35_no_sixth_same_group_repeat": True,
            "v35_source_manifest_path": str(SOURCE_MANIFEST),
            "v35_source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        }
    )
    pins = {
        str(Path(item["path"]).resolve()): copy.deepcopy(item)
        for item in manifest.get("pinned_files") or []
    }
    for path in (controller, preflight, package_builder, test_path):
        pins[str(path.resolve())] = {
            "path": str(path.resolve()),
            "sha256": v25.sha256(path.resolve()),
        }
    manifest["pinned_files"] = [pins[key] for key in sorted(pins)]
    PACKAGE.mkdir(parents=True, mode=0o700)
    v25.atomic_json(MANIFEST, manifest)
    manifest_sha = v25.sha256(MANIFEST)
    controller_sha = v25.sha256(controller)
    preflight_sha = v25.sha256(preflight)
    python = str(manifest["python"])
    script = f"""#!/usr/bin/env bash
set -euo pipefail

manifest={MANIFEST}
controller={controller}
preflight={preflight}
python={python}
session=tpcc_fresh0_v35
manifest_sha={manifest_sha}
controller_sha={controller_sha}
preflight_sha={preflight_sha}

test "$(sha256sum "${{manifest}}" | awk '{{print $1}}')" = "${{manifest_sha}}"
test "$(sha256sum "${{controller}}" | awk '{{print $1}}')" = "${{controller_sha}}"
test "$(sha256sum "${{preflight}}" | awk '{{print $1}}')" = "${{preflight_sha}}"

if [[ "${{1:-}}" == "--package-only" ]]; then
  exec "${{python}}" -u "${{preflight}}" "${{manifest}}" \\
    --manifest-sha256 "${{manifest_sha}}" --package-only
fi
[[ "$#" -eq 0 ]]

"${{python}}" -u "${{preflight}}" "${{manifest}}" \\
  --manifest-sha256 "${{manifest_sha}}"

if tmux has-session -t "${{session}}" 2>/dev/null; then
  echo "TPC-C v35 tmux session already exists" >&2
  exit 73
fi
tmux new-session -d -s "${{session}}" \\
  "exec '${{python}}' -u '${{controller}}' '${{manifest}}' --armed --manifest-sha256 '${{manifest_sha}}' >> '{PACKAGE}/controller.log' 2>&1"
sleep 2
tmux has-session -t "${{session}}"
pane_pid="$(tmux list-panes -t "${{session}}" -F '#{{pane_pid}}')"
test -n "${{pane_pid}}"
kill -0 "${{pane_pid}}"
printf '%s\n' "${{pane_pid}}" > "{PACKAGE}/controller.pid"
printf 'TPCC_V35_STARTED session=%s pid=%s\n' "${{session}}" "${{pane_pid}}"
"""
    v25.atomic_text(LAUNCHER, script, mode=0o700)
    report = {
        "contract": "tpcc_v35_dormant_zero_call_handoff_package_v1",
        "passed": True,
        "manifest_path": str(MANIFEST),
        "manifest_sha256": manifest_sha,
        "controller_path": str(controller),
        "controller_sha256": controller_sha,
        "preflight_path": str(preflight),
        "preflight_sha256": preflight_sha,
        "launcher_path": str(LAUNCHER),
        "launcher_sha256": v25.sha256(LAUNCHER),
        "activation_state": "awaiting_failed_group5_no_active_replay",
        "physical_workload_calls_added": 0,
        "current_v34_replaced_or_stopped": False,
    }
    v25.atomic_json(PACKAGE / "package_report.json", report)
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
