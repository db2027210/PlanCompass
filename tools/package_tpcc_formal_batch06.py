#!/usr/bin/env python3
"""Package an authorized, self-contained TPC-C batch06 without starting it."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Dict


PROJECT = Path("/home/anonymous/program/resource_adaptive")
RUNNER_SOURCE_ROOT = Path(
    "/tmp/tpcc_batch05_retry2_selfcontained_audit"
)
ATTEMPT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726"
)
FORMAL_STATE = ATTEMPT / "formal10_tpcc_state.json"
FORMAL_STATE_SHA = "4a273208d8503f2d18a108293bf5714043418bdf72766bb44e8e10f16e5f9d5f"
BATCH_ROOT = (
    ATTEMPT
    / "formal10_tpcc_batch06_novel_v2_rangebound_20260729T2150"
)
PACKAGE_ROOT = Path("/tmp/tpcc_formal_batch06_package_v2_rangebound")
RUNNER_ROOT = PACKAGE_ROOT / "runner"
PACKAGE_SCHEMA = "tpcc_formal_batch06_package_v2_rangebound"
FORMAL_RUNNER = PROJECT / "tools/run_tpcc_formal_target10_batch.py"
FORMAL_RUNNER_SHA = (
    "b3490570631ff7b6f36110a56472abd5816119c3fb44c68503b47291d487b627"
)
RUNNER_FILES = {
    RUNNER_SOURCE_ROOT / "run_tpcc_round11_calibrated_top5_strict_serial.py":
        "df1f9f9b43656214cdccace0e3614fada7d2efc7c7679942da6478ac0f042d68",
    RUNNER_SOURCE_ROOT / "adaptive_replay_policy.py":
        "c3c7a826ff1ab65c04c93510efd17b26907a79a15b3d13ff91e5ca106de94584",
    RUNNER_SOURCE_ROOT / "tpcc_remaining_budget_selector.py":
        "8c963c9f730e1038b61146e2e928b946a6b213e7ee95b9aa6b9c4ebdf0608376",
    RUNNER_SOURCE_ROOT / "replay_correctness_policy.py":
        "e6bb7aefc67eb01e3d9138c7bf36cf93367d87779d58013cad41b2302f9c05f7",
    RUNNER_SOURCE_ROOT / "gradient_replay_metrics.py":
        "499f71cf7bdb8f882ab7776d1635b7abe021f1eee1e18d90b4aff2b66859592c",
    RUNNER_SOURCE_ROOT / "resource_utility.py":
        "24cb1ed83ef36a577461675c7180ec3fa7343a8bcd3462063c028668433b4e36",
}
DIAGNOSTIC_RUNNER_NAME = "run_tpcc_round11_calibrated_top5_strict_serial.py"
WORKLOAD_RUNNER = (
    ATTEMPT
    / "gradient_debug_round11_tpcc_calibrated_deadband_v1_20260728"
    / "code/run_tpcc_sensitive_stable_point.py"
)
WORKLOAD_RUNNER_SHA = (
    "bf03a7b4e299798739f4d92393b01db2c89c7cd266b58d12dad6737d5b0643ee"
)
RESTORE = PROJECT / "shell/restore_pg5_tpcc_from_template.sh"
RESTORE_SHA = (
    "1af8eca4b8fbd229f525b627293eb58a51aa3510472c97340b5ea317be5cde59"
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
    runner_checks = "\n".join(
        (
            f'test "$(sha256sum "${{RUNNER_ROOT}}/{source.name}" '
            f'| awk \'{{print $1}}\')" = "{digest}"'
        )
        for source, digest in RUNNER_FILES.items()
    )
    return f"""#!/usr/bin/env bash
set -euo pipefail

PACKAGE={PACKAGE_ROOT}
RUNNER_ROOT="${{PACKAGE}}/runner"
TOP5="${{PACKAGE}}/top5.json"
GATE="${{PACKAGE}}/continuation_gate.json"
STATE={FORMAL_STATE}
BATCH_ROOT={BATCH_ROOT}
PYTHON=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
FORMAL_RUNNER={FORMAL_RUNNER}
DIAGNOSTIC_RUNNER="${{RUNNER_ROOT}}/{DIAGNOSTIC_RUNNER_NAME}"
BASELINE={BASELINE}

test "$(sha256sum "$TOP5" | awk '{{print $1}}')" = "{top5_sha}"
test "$(sha256sum "$GATE" | awk '{{print $1}}')" = "{gate_sha}"
test "$(sha256sum "$STATE" | awk '{{print $1}}')" = "{FORMAL_STATE_SHA}"
test "$(sha256sum "$FORMAL_RUNNER" | awk '{{print $1}}')" = "{FORMAL_RUNNER_SHA}"
{runner_checks}
test "$(sha256sum "{WORKLOAD_RUNNER}" | awk '{{print $1}}')" = "{WORKLOAD_RUNNER_SHA}"
test "$(sha256sum "{RESTORE}" | awk '{{print $1}}')" = "{RESTORE_SHA}"
test "$(sha256sum "$BASELINE" | awk '{{print $1}}')" = "{BASELINE_SHA}"
test ! -e "$BATCH_ROOT"
if pgrep -af '[r]un_tpcc_round11_calibrated_top5_strict_serial.py' >/dev/null
then
    echo "TPC-C diagnostic runner already active" >&2
    exit 73
fi

exec "$PYTHON" -u "$FORMAL_RUNNER" \\
  "$TOP5" "{top5_sha}" \\
  "$GATE" "{gate_sha}" \\
  "$DIAGNOSTIC_RUNNER" "$BASELINE" "$STATE" "$BATCH_ROOT"
"""


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: package_tpcc_formal_batch06.py TOP5 CONTINUATION_GATE"
        )
    top5_source = Path(sys.argv[1]).resolve()
    gate_source = Path(sys.argv[2]).resolve()
    if PACKAGE_ROOT.exists() or BATCH_ROOT.exists():
        raise FileExistsError("batch06 package or batch root already exists")
    pinned = (
        (FORMAL_STATE, FORMAL_STATE_SHA, "formal state"),
        (FORMAL_RUNNER, FORMAL_RUNNER_SHA, "formal runner"),
        (WORKLOAD_RUNNER, WORKLOAD_RUNNER_SHA, "workload runner"),
        (RESTORE, RESTORE_SHA, "restore script"),
        (BASELINE, BASELINE_SHA, "baseline"),
    )
    for path, expected, label in pinned:
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"{label} SHA mismatch: {path}")
    for path, expected in RUNNER_FILES.items():
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"runner dependency SHA mismatch: {path}")

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
        or top5.get("qps_role") != "outer_audit_only_not_inner_score"
        or [item.get("selection_role") for item in top5.get("top5", [])]
        != [
            "trusted",
            "trusted",
            "trusted",
            "uncertain_probe",
            "uncertain_probe",
        ]
    ):
        raise RuntimeError("batch06 authorization/top5 contract mismatch")

    PACKAGE_ROOT.mkdir(mode=0o700)
    RUNNER_ROOT.mkdir(mode=0o700)
    shutil.copyfile(top5_source, PACKAGE_ROOT / "top5.json")
    shutil.copyfile(gate_source, PACKAGE_ROOT / "continuation_gate.json")
    for source in RUNNER_FILES:
        shutil.copyfile(source, RUNNER_ROOT / source.name)

    launcher_path = PACKAGE_ROOT / "launch_batch06_foreground.sh"
    atomic_text(launcher_path, launcher(top5_sha, gate_sha), mode=0o700)
    manifest = {
        "schema_version": PACKAGE_SCHEMA,
        "prepared_only_no_workload_started": True,
        "formal_state_path": str(FORMAL_STATE),
        "formal_state_sha256": FORMAL_STATE_SHA,
        "accepted_before": 1,
        "accepted_target": 10,
        "physical_calls_before": 25,
        "valid_replays_before": 25,
        "technical_failures_before": 0,
        "maximum_physical_calls": 5,
        "top5_path": str(PACKAGE_ROOT / "top5.json"),
        "top5_sha256": top5_sha,
        "gate_path": str(PACKAGE_ROOT / "continuation_gate.json"),
        "gate_sha256": gate_sha,
        "formal_runner_path": str(FORMAL_RUNNER),
        "formal_runner_sha256": FORMAL_RUNNER_SHA,
        "diagnostic_runner_path": str(
            RUNNER_ROOT / DIAGNOSTIC_RUNNER_NAME
        ),
        "diagnostic_runner_sha256": RUNNER_FILES[
            RUNNER_SOURCE_ROOT / DIAGNOSTIC_RUNNER_NAME
        ],
        "runner_file_sha256": {
            str(RUNNER_ROOT / source.name): digest
            for source, digest in RUNNER_FILES.items()
        },
        "workload_runner_path": str(WORKLOAD_RUNNER),
        "workload_runner_sha256": WORKLOAD_RUNNER_SHA,
        "restore_script_path": str(RESTORE),
        "restore_script_sha256": RESTORE_SHA,
        "baseline_path": str(BASELINE),
        "baseline_sha256": BASELINE_SHA,
        "batch_root": str(BATCH_ROOT),
        "launcher_path": str(launcher_path),
        "launcher_sha256": sha256(launcher_path),
    }
    atomic_text(
        PACKAGE_ROOT / "manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
