#!/usr/bin/env python3
"""Package an authorized TPC-C batch05 without starting it."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Dict


ATTEMPT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726"
)
FORMAL_STATE = ATTEMPT / "formal10_tpcc_state.json"
FORMAL_STATE_SHA = "c802eb584841a098de2ccee7bc2cea8bdf23d3fb7440081c85aeb5f5e958b13c"
BATCH_ROOT = ATTEMPT / "formal10_tpcc_batch05_novel_v1_20260729T2100"
PACKAGE_ROOT = Path("/tmp/tpcc_formal_batch05_package_v1")
FORMAL_RUNNER = Path(
    "/home/anonymous/program/resource_adaptive/tools/"
    "run_tpcc_formal_target10_batch.py"
)
FORMAL_RUNNER_SHA = (
    "b3490570631ff7b6f36110a56472abd5816119c3fb44c68503b47291d487b627"
)
DIAGNOSTIC_RUNNER = Path(
    "/tmp/run_tpcc_round11_calibrated_top5_strict_serial.py"
)
DIAGNOSTIC_RUNNER_SHA = (
    "0d44b3bd0d4b113ad1f08c4d3d2a461235d86b71da9dce11d3b75d5636f5ce31"
)
WORKLOAD_RUNNER = (
    ATTEMPT
    / "gradient_debug_round11_tpcc_calibrated_deadband_v1_20260728"
    / "code"
    / "run_tpcc_sensitive_stable_point.py"
)
WORKLOAD_RUNNER_SHA = (
    "bf03a7b4e299798739f4d92393b01db2c89c7cd266b58d12dad6737d5b0643ee"
)
BASELINE = (
    ATTEMPT
    / "candidate_measurements"
    / "point_20260729_022349_1052396"
    / "summary.json"
)
BASELINE_SHA = (
    "9c0f0d083796bef1ec8d49c4aa71b1d8e904fca43dbfb49a1708c0c40ccd7356"
)
SUPPORT_FILES = {
    Path("/tmp/extract_tpcc_round11_calibrated_top5.py"):
        "42714617c1f965e19b5cfb13caa45cafb0274479806eb5a5963344ab361e0adb",
    Path("/tmp/tpcc_round11_current_correctness_policy.py"):
        "e6bb7aefc67eb01e3d9138c7bf36cf93367d87779d58013cad41b2302f9c05f7",
    Path("/tmp/adaptive_replay_policy.py"):
        "c3c7a826ff1ab65c04c93510efd17b26907a79a15b3d13ff91e5ca106de94584",
    Path("/tmp/tpcc_remaining_budget_selector.py"):
        "8c963c9f730e1038b61146e2e928b946a6b213e7ee95b9aa6b9c4ebdf0608376",
    Path("/home/anonymous/program/resource_adaptive/gradient_replay_metrics.py"):
        "499f71cf7bdb8f882ab7776d1635b7abe021f1eee1e18d90b4aff2b66859592c",
    Path("/home/anonymous/program/resource_adaptive/resource_utility.py"):
        "24cb1ed83ef36a577461675c7180ec3fa7343a8bcd3462063c028668433b4e36",
    Path(
        "/home/anonymous/program/resource_adaptive/shell/"
        "restore_pg5_tpcc_from_template.sh"
    ):
        "1af8eca4b8fbd229f525b627293eb58a51aa3510472c97340b5ea317be5cde59",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_text(path: Path, text: str, mode: int = 0o600) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def launcher(top5_sha: str, gate_sha: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

PACKAGE={PACKAGE_ROOT}
TOP5="${{PACKAGE}}/top5.json"
GATE="${{PACKAGE}}/continuation_gate.json"
STATE={FORMAL_STATE}
BATCH_ROOT={BATCH_ROOT}
PYTHON=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
FORMAL_RUNNER={FORMAL_RUNNER}
DIAGNOSTIC_RUNNER={DIAGNOSTIC_RUNNER}
BASELINE={BASELINE}

test "$(sha256sum "$TOP5" | awk '{{print $1}}')" = "{top5_sha}"
test "$(sha256sum "$GATE" | awk '{{print $1}}')" = "{gate_sha}"
test "$(sha256sum "$STATE" | awk '{{print $1}}')" = "{FORMAL_STATE_SHA}"
test "$(sha256sum "$FORMAL_RUNNER" | awk '{{print $1}}')" = "{FORMAL_RUNNER_SHA}"
test "$(sha256sum "$DIAGNOSTIC_RUNNER" | awk '{{print $1}}')" = "{DIAGNOSTIC_RUNNER_SHA}"
test "$(sha256sum "{WORKLOAD_RUNNER}" | awk '{{print $1}}')" = "{WORKLOAD_RUNNER_SHA}"
test "$(sha256sum "$BASELINE" | awk '{{print $1}}')" = "{BASELINE_SHA}"
{os.linesep.join(
    f'''test "$(sha256sum "{path}" | awk '{{print $1}}')" = "{digest}"'''
    for path, digest in SUPPORT_FILES.items()
)}
test ! -e "$BATCH_ROOT"
! pgrep -af "$DIAGNOSTIC_RUNNER"

exec "$PYTHON" -u "$FORMAL_RUNNER" \\
  "$TOP5" "{top5_sha}" \\
  "$GATE" "{gate_sha}" \\
  "$DIAGNOSTIC_RUNNER" "$BASELINE" "$STATE" "$BATCH_ROOT"
"""


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: package_tpcc_formal_batch05.py TOP5 CONTINUATION_GATE"
        )
    top5_source = Path(sys.argv[1]).resolve()
    gate_source = Path(sys.argv[2]).resolve()
    if PACKAGE_ROOT.exists() or BATCH_ROOT.exists():
        raise FileExistsError("batch05 package or batch root already exists")
    for path, expected, label in (
        (FORMAL_STATE, FORMAL_STATE_SHA, "formal state"),
        (FORMAL_RUNNER, FORMAL_RUNNER_SHA, "formal runner"),
        (DIAGNOSTIC_RUNNER, DIAGNOSTIC_RUNNER_SHA, "diagnostic runner"),
        (WORKLOAD_RUNNER, WORKLOAD_RUNNER_SHA, "workload runner"),
        (BASELINE, BASELINE_SHA, "baseline"),
    ):
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"{label} SHA mismatch: {path}")
    for path, expected in SUPPORT_FILES.items():
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"support file SHA mismatch: {path}")
    top5 = load(top5_source)
    gate = load(gate_source)
    top5_sha = sha256(top5_source)
    gate_sha = sha256(gate_source)
    if (
        gate.get("passed") is not True
        or gate.get("formal_start_authorized") is not True
        or gate.get("formal_state_sha256") != FORMAL_STATE_SHA
        or gate.get("selection_sha256") != top5_sha
        or top5.get("maximum_real_workload_invocations") != 5
        or [item.get("selection_role") for item in top5.get("top5", [])]
        != [
            "trusted",
            "trusted",
            "trusted",
            "uncertain_probe",
            "uncertain_probe",
        ]
    ):
        raise RuntimeError("batch05 authorization/top5 contract mismatch")

    PACKAGE_ROOT.mkdir(mode=0o700)
    shutil.copyfile(top5_source, PACKAGE_ROOT / "top5.json")
    shutil.copyfile(gate_source, PACKAGE_ROOT / "continuation_gate.json")
    launcher_path = PACKAGE_ROOT / "launch_batch05_foreground.sh"
    atomic_text(launcher_path, launcher(top5_sha, gate_sha), mode=0o700)
    manifest = {
        "schema_version": "tpcc_formal_batch05_package_v1",
        "prepared_only_no_workload_started": True,
        "formal_state_path": str(FORMAL_STATE),
        "formal_state_sha256": FORMAL_STATE_SHA,
        "accepted_before": 1,
        "accepted_target": 10,
        "physical_calls_before": 20,
        "valid_replays_before": 20,
        "technical_failures_before": 0,
        "maximum_physical_calls": 5,
        "top5_path": str(PACKAGE_ROOT / "top5.json"),
        "top5_sha256": top5_sha,
        "gate_path": str(PACKAGE_ROOT / "continuation_gate.json"),
        "gate_sha256": gate_sha,
        "formal_runner_path": str(FORMAL_RUNNER),
        "formal_runner_sha256": FORMAL_RUNNER_SHA,
        "diagnostic_runner_path": str(DIAGNOSTIC_RUNNER),
        "diagnostic_runner_sha256": DIAGNOSTIC_RUNNER_SHA,
        "workload_runner_path": str(WORKLOAD_RUNNER),
        "workload_runner_sha256": WORKLOAD_RUNNER_SHA,
        "baseline_path": str(BASELINE),
        "baseline_sha256": BASELINE_SHA,
        "support_file_sha256": {
            str(path): digest for path, digest in SUPPORT_FILES.items()
        },
        "batch_root": str(BATCH_ROOT),
        "launcher_path": str(launcher_path),
        "launcher_sha256": sha256(launcher_path),
    }
    atomic_text(
        PACKAGE_ROOT / "manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
