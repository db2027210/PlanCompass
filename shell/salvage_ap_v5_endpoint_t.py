#!/usr/bin/env python3
"""Create a deterministic T release from the sealed AP 0820 endpoint run."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shell"))
from calibrate_mysql_experience_table import _canonical_bytes, _load_json  # noqa: E402
from freeze_mysql_estimator_experience_table import build_estimator_artifact  # noqa: E402
from run_host01_ap_v5_endpoint_collection import seal_run, sha, write_json  # noqa: E402

SOURCE = ROOT / "history/mysql_sgd_env_agents_20260723/ap/ap_v5_endpoint_real_20260723T082000Z"
TARGET = ROOT / "history/mysql_sgd_env_agents_20260723/ap/ap_v5_endpoint_real_20260723T082000Z_salvaged_t"


def verify_source() -> dict:
    summary = _load_json(SOURCE / "summary.json")
    if summary.get("failure_stage") != "independent_verify" or summary.get("cost_restore_equal") is not True or summary.get("cleanup_ok") is not True:
        raise ValueError("source did not fail only at independent verify with clean restore")
    manifest = _load_json(SOURCE / "MANIFEST.json")
    if (SOURCE / "SEALED").read_text(encoding="ascii").split()[0] != sha(SOURCE / "MANIFEST.json"):
        raise ValueError("source SEALED pointer mismatch")
    for row in manifest.get("files", []):
        path = SOURCE / row["path"]
        if path.stat().st_size != row["bytes"] or sha(path) != row["sha256"]:
            raise ValueError(f"source manifest mismatch: {row['path']}")
    evidence = _load_json(SOURCE / "endpoint_evidence/endpoint_evidence.json")
    if evidence.get("query_coverage", {}).get("coverage_ratio") != 1.0 or 1 + 2 * len(evidence.get("active_params", [])) != 17:
        raise ValueError("source endpoint coverage mismatch")
    remote_events = (SOURCE / "remote/events.jsonl").read_text(encoding="utf-8")
    if '"rc":0' not in remote_events or '"template_recursive_manifest_unchanged":true' not in remote_events:
        raise ValueError("source remote cleanup/template gate mismatch")
    return dict(summary)


def main() -> int:
    if TARGET.exists(): raise ValueError("salvage target already exists")
    summary = verify_source()
    staging = TARGET.with_name(f".{TARGET.name}.staging.{os.getpid()}")
    if staging.exists(): raise ValueError("salvage staging exists")
    staging.mkdir(mode=0o700)
    try:
        shutil.copytree(SOURCE / "endpoint_evidence", staging / "endpoint_evidence")
        request_path = staging / "endpoint_evidence/freeze_request.json"
        artifact = build_estimator_artifact(_load_json(request_path), request_path)
        payload = _canonical_bytes(artifact)
        t_path = staging / "T.mysql_ap_estimator_v1.json"
        t_path.write_bytes(payload)
        (staging / "T.mysql_ap_estimator_v1.json.sha256").write_text(
            f"{hashlib.sha256(payload).hexdigest()}  T.mysql_ap_estimator_v1.json\n", encoding="ascii"
        )
        rebuilt = _canonical_bytes(build_estimator_artifact(_load_json(request_path), request_path))
        if rebuilt != payload: raise ValueError("deterministic independent rebuild mismatch")
        verification = {
            "status": "PASS", "source_run": SOURCE.name,
            "source_summary_sha256": sha(SOURCE / "summary.json"),
            "source_manifest_sha256": sha(SOURCE / "MANIFEST.json"),
            "source_cost_restore_equal": summary["cost_restore_equal"],
            "source_cleanup_ok": summary["cleanup_ok"],
            "endpoint_count": 17, "query_coverage_ratio": 1.0,
            "canonical_deterministic_rebuild_equal": True,
            "t_sha256": hashlib.sha256(payload).hexdigest(),
            "t_provenance_paths_relative": True,
            "real_measurement": False, "source": "trained_estimator_experience",
            "r0_started": False, "sgd_started": False, "ready_promoted": False,
        }
        write_json(staging / "independent_verification.json", verification)
        write_json(staging / "source_run_pointer.json", {
            "path": str(SOURCE), "sha256": sha(SOURCE / "summary.json"),
            "sealed_manifest_sha256": sha(SOURCE / "MANIFEST.json"),
        })
        manifest_sha = seal_run(staging, TARGET)
        print(json.dumps({"status": "PASS", "target": str(TARGET), "t_sha256": verification["t_sha256"], "manifest_sha256": manifest_sha}, sort_keys=True))
        return 0
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


if __name__ == "__main__": raise SystemExit(main())
