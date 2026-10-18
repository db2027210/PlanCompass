#!/usr/bin/env python3
"""Build the SHA-bound XP autonomous controller package; never launch it."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT = Path("/home/anonymous/program/resource_adaptive")
CONTROLLER = PROJECT / "tools" / "xp_autonomous_controller.py"
TEST = PROJECT / "tools" / "test_xp_autonomous_controller.py"
CAMPAIGN = (
    PROJECT / "config" / "formal" / "xp_autonomous_controller_accepted10_v1.json"
)
OUTPUT = Path("/tmp/xp_autonomous_controller_accepted10_v1_20260729T2300")
DEPLOY_ROOT = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726/"
    "xp_autonomous_controller_v1/control"
)
STATE = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726/"
    "xp_autonomous_controller_v1/state.json"
)
PYTHON = Path("/home/anonymous/miniconda3/envs/resource_adaptive/bin/python")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_text(path: Path, text: str, mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def main() -> None:
    if OUTPUT.exists():
        raise RuntimeError(f"output already exists: {OUTPUT}")
    test_result = subprocess.run(
        [str(PYTHON), str(TEST)],
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if test_result.returncode != 0:
        raise RuntimeError(
            f"controller mock tests failed:\n{test_result.stdout}\n{test_result.stderr}"
        )
    test_audit = json.loads(test_result.stdout)
    if (
        test_audit.get("status") != "passed"
        or test_audit.get("physical_calls") != 0
        or test_audit.get("workload_started") is not False
    ):
        raise RuntimeError("mock test did not prove zero workload calls")

    OUTPUT.mkdir(parents=True)
    shutil.copy2(CONTROLLER, OUTPUT / CONTROLLER.name)
    shutil.copy2(TEST, OUTPUT / TEST.name)
    shutil.copy2(CAMPAIGN, OUTPUT / "campaign.json")
    controller_path = OUTPUT / CONTROLLER.name
    test_path = OUTPUT / TEST.name
    campaign_path = OUTPUT / "campaign.json"
    os.chmod(controller_path, 0o700)
    os.chmod(test_path, 0o700)

    armed = {
        "schema_version": "xp_autonomous_armed_manifest_v1",
        "contract": "xp_formal_accepted10_autonomous_v1",
        "mode": "armed",
        "allow_local_process_execution": True,
        "controller_sha256": sha256(controller_path),
        "campaign_sha256": sha256(campaign_path),
        "cpu_affinity": "32-51,288-307",
        "accepted_target": 10,
        "physical_cap_per_batch": 5,
        "qps_used_for_inner_scoring": False,
        "manual_promotion_allowed": False,
        "ssh_allowed": False,
        "mysql_access_allowed": False,
        "created_at": "2026-07-29T23:00:00+08:00",
    }
    armed_path = OUTPUT / "armed_manifest.json"
    atomic_text(armed_path, json.dumps(armed, indent=2, sort_keys=True) + "\n")
    test_audit_path = OUTPUT / "mock_zero_call_test.json"
    atomic_text(
        test_audit_path,
        json.dumps(test_audit, indent=2, sort_keys=True) + "\n",
    )
    dry_launcher = f"""#!/usr/bin/env bash
set -euo pipefail
ROOT={DEPLOY_ROOT}
PYTHON={PYTHON}
test "$(sha256sum "$ROOT/xp_autonomous_controller.py" | awk '{{print $1}}')" = "{sha256(controller_path)}"
test "$(sha256sum "$ROOT/campaign.json" | awk '{{print $1}}')" = "{sha256(campaign_path)}"
exec "$PYTHON" "$ROOT/xp_autonomous_controller.py" --campaign "$ROOT/campaign.json" --state {STATE}
"""
    armed_launcher = f"""#!/usr/bin/env bash
set -euo pipefail
ROOT={DEPLOY_ROOT}
PYTHON={PYTHON}
test "$(sha256sum "$ROOT/xp_autonomous_controller.py" | awk '{{print $1}}')" = "{sha256(controller_path)}"
test "$(sha256sum "$ROOT/campaign.json" | awk '{{print $1}}')" = "{sha256(campaign_path)}"
test "$(sha256sum "$ROOT/armed_manifest.json" | awk '{{print $1}}')" = "{sha256(armed_path)}"
exec "$PYTHON" "$ROOT/xp_autonomous_controller.py" --campaign "$ROOT/campaign.json" --state {STATE} --execute --armed-manifest "$ROOT/armed_manifest.json"
"""
    dry_launcher_path = OUTPUT / "run_dry.sh"
    armed_launcher_path = OUTPUT / "run_armed.sh"
    atomic_text(dry_launcher_path, dry_launcher, mode=0o700)
    atomic_text(armed_launcher_path, armed_launcher, mode=0o700)
    for launcher in (dry_launcher_path, armed_launcher_path):
        syntax = subprocess.run(
            ["bash", "-n", str(launcher)],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if syntax.returncode != 0:
            raise RuntimeError(f"launcher syntax failed: {launcher}: {syntax.stderr}")
    package_manifest = {
        "schema_version": "xp_autonomous_controller_package_v1",
        "contract": "xp_formal_accepted10_autonomous_v1",
        "staging_root": str(OUTPUT),
        "deploy_root": str(DEPLOY_ROOT),
        "state_path": str(STATE),
        "default_mode": "dry_run",
        "prepared_only_no_workload_started": True,
        "cpu_affinity": "32-51,288-307",
        "forbidden_mysql_cpu_affinity": ["184-199", "440-455"],
        "physical_cap_per_batch": 5,
        "technical_failure_consumes_cap": True,
        "rejected_updates_forbidden": True,
        "qps_used_for_inner_scoring": False,
        "manual_promotion_allowed": False,
        "rolling_stability_fields": [
            "trusted_spearman",
            "actual_best_in_predicted_top3",
            "exact_regime_resource_error_factor_max.cpu",
            "exact_regime_resource_error_factor_max.io",
            "exact_regime_resource_error_factor_max.memory",
            "gradient_sign_reversal_rate"
        ],
        "files": {
            CONTROLLER.name: sha256(controller_path),
            TEST.name: sha256(test_path),
            "campaign.json": sha256(campaign_path),
            "armed_manifest.json": sha256(armed_path),
            "mock_zero_call_test.json": sha256(test_audit_path),
            "run_dry.sh": sha256(dry_launcher_path),
            "run_armed.sh": sha256(armed_launcher_path),
        },
        "mock_test": test_audit,
    }
    package_manifest_path = OUTPUT / "package_manifest.json"
    atomic_text(
        package_manifest_path,
        json.dumps(package_manifest, indent=2, sort_keys=True) + "\n",
    )
    print(json.dumps({
        "output": str(OUTPUT),
        "deploy_root": str(DEPLOY_ROOT),
        "controller_sha256": sha256(controller_path),
        "campaign_sha256": sha256(campaign_path),
        "armed_manifest_sha256": sha256(armed_path),
        "package_manifest_sha256": sha256(package_manifest_path),
        "dry_launcher_sha256": sha256(dry_launcher_path),
        "armed_launcher_sha256": sha256(armed_launcher_path),
        "mock_test": test_audit,
        "physical_calls": 0,
        "workload_started": False,
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
