#!/usr/bin/env python3
"""Auditably upgrade retry3 runner to emit the derived-resource contract."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Dict


LANE = Path("/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02")
ROOT = LANE / "history/job/outer/attempt_4_formal_dbqps_v1_retry3"
WORKSPACE = LANE / "workspace_outer_attempt4_formal_dbqps_v1_retry3"
LAUNCH = LANE / "launch_outer_attempt4_formal_dbqps_v1_retry3"
MANIFEST = ROOT / "attempt4_contract_manifest.json"
RUNNER = WORKSPACE / "formal_pg_point_runner.py"
STAGED_RUNNER = LAUNCH / "formal_pg_point_runner.authoritative.py"
TEST = LAUNCH / "test_formal_db_qps_runner.py"
NEXT_RUNNER = LAUNCH / "formal_pg_point_runner.derived_contract.py"
NEXT_TEST = LAUNCH / "test_formal_db_qps_runner.derived_contract.py"
EVIDENCE = ROOT / "future_candidate_derived_contract_runner_upgrade.json"
SNAPSHOT_ROOT = ROOT / "code_snapshots"
OLD_RUNNER_SHA = "9b655402d82ede45a7d509cd81fdf9eb7c79c828c066b1b117bdc8fef2fd19ae"
OLD_TEST_SHA = "0030bfb39028ce180cf2ac26568b91accaddd657c474a1aacc3fb3b614498220"
NEW_RUNNER_SHA = "1743855faee3f0ab1bff2335b3ef975ab7b28bdba0b21c5463ea61339a11bb66"
NEW_TEST_SHA = "f371dcc660744a99ef599b9ab770cfa25eacb1ee7c8c3e98656ce740c5d8f3c6"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeError(f"stale upgrade temporary exists: {temporary}")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def main() -> int:
    if EVIDENCE.exists() or EVIDENCE.is_symlink():
        raise RuntimeError("retry3 runner upgrade evidence already exists")
    expected = {
        RUNNER: OLD_RUNNER_SHA,
        STAGED_RUNNER: OLD_RUNNER_SHA,
        TEST: OLD_TEST_SHA,
        NEXT_RUNNER: NEW_RUNNER_SHA,
        NEXT_TEST: NEW_TEST_SHA,
    }
    for path, expected_sha in expected.items():
        if not path.is_file() or sha256(path) != expected_sha:
            raise RuntimeError(f"runner upgrade SHA mismatch: {path}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if (
        manifest.get("formal_runner_sha256") != OLD_RUNNER_SHA
        or manifest.get("formal_runner_test_sha256") != OLD_TEST_SHA
    ):
        raise RuntimeError("retry3 manifest is not bound to the pre-upgrade runner")

    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=False)
    old_runner_snapshot = SNAPSHOT_ROOT / "formal_pg_point_runner.before_derived_contract.py"
    old_test_snapshot = SNAPSHOT_ROOT / "test_formal_db_qps_runner.before_derived_contract.py"
    shutil.copy2(RUNNER, old_runner_snapshot)
    shutil.copy2(TEST, old_test_snapshot)
    shutil.copy2(NEXT_RUNNER, RUNNER)
    shutil.copy2(NEXT_RUNNER, STAGED_RUNNER)
    shutil.copy2(NEXT_TEST, TEST)
    if sha256(RUNNER) != NEW_RUNNER_SHA or sha256(TEST) != NEW_TEST_SHA:
        raise RuntimeError("retry3 runner/test upgrade copy did not preserve SHA")

    evidence = {
        "schema_version": 1,
        "audit_id": "pg5_job_attempt4_retry3_derived_contract_runner_upgrade_v1",
        "scope": "future_points_and_point31_unsealed_postprocessing",
        "formula_changed": False,
        "new_behavior": (
            "atomically persist the already-computed formal derived-resource "
            "rows and publish path+SHA in measurement"
        ),
        "old_runner_snapshot_path": str(old_runner_snapshot),
        "old_runner_sha256": sha256(old_runner_snapshot),
        "new_runner_path": str(RUNNER),
        "new_runner_sha256": sha256(RUNNER),
        "old_test_snapshot_path": str(old_test_snapshot),
        "old_test_sha256": sha256(old_test_snapshot),
        "new_test_path": str(TEST),
        "new_test_sha256": sha256(TEST),
        "test_count": 8,
        "test_status": "passed",
        "point31_rerun": False,
        "candidate_budget_consumed": False,
        "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    atomic_json(EVIDENCE, evidence)
    manifest.update(
        {
            "formal_runner_sha256": NEW_RUNNER_SHA,
            "formal_runner_test_sha256": NEW_TEST_SHA,
            "formal_runner_test_count": 8,
            "formal_runner_test_status": "passed",
            "derived_contract_runner_upgrade_path": str(EVIDENCE),
            "derived_contract_runner_upgrade_sha256": sha256(EVIDENCE),
        }
    )
    atomic_json(MANIFEST, manifest)
    print(
        json.dumps(
            {
                **evidence,
                "evidence_path": str(EVIDENCE),
                "evidence_sha256": sha256(EVIDENCE),
                "manifest_sha256": sha256(MANIFEST),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
