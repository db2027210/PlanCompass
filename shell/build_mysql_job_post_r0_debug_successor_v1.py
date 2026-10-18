#!/usr/bin/env python3
"""Build the immutable JOB post-R0 debug successor closure."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/mysql_job_post_r0_debug_successor_v1"
SOURCES = {
    "campaign/mysql_job_post_r0_exact_services_v1.py": ROOT / "shell/mysql_job_post_r0_exact_services_v1.py",
    "campaign/mysql_job_post_r0_debug_campaign_v1.py": ROOT / "shell/mysql_job_post_r0_debug_campaign_v1.py",
    "campaign/mysql_job_post_r0_debug_evidence_v1.py": ROOT / "shell/mysql_job_post_r0_debug_evidence_v1.py",
    "materialize/mysql_job_post_r0_debug_materializer_v1.py": ROOT / "shell/mysql_job_post_r0_debug_materializer_v1.py",
    "tests/test_mysql_job_post_r0_debug_successor_v1.py": ROOT / "tests/test_mysql_job_post_r0_debug_successor_v1.py",
    "authority/build_mysql_job_post_r0_debug_successor_v1.py": ROOT / "shell/build_mysql_job_post_r0_debug_successor_v1.py",
}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="job-post-r0-", dir=str(OUTPUT)) as temporary:
        stage = Path(temporary)
        for relative, source in SOURCES.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            os.chmod(target, 0o555 if target.suffix == ".py" else 0o444)
        files = {
            relative: {"sha256": sha(stage / relative), "size": (stage / relative).stat().st_size}
            for relative in sorted(SOURCES)
        }
        identity = {
            "schema_version": "mysql_job_post_r0_debug_successor_manifest_v1",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "target_bundle_id": "659b121c32f1866d4fe2a07ec2aa5857c81578f8b75cfafedf04654cb3b4bcef",
            "target_manifest_sha256": "f6018261584f06ea9a8968357db26b2c3da6ec019a31bb295530a2979f14b406",
            "qualification_support_predecessor_bundle_id": "6b55e29ef3c47cca437c94501511317c6c79dd1579be3dc6e3a4983587245f74",
            "qualification_support_predecessor_manifest_sha256": "5c5e59098304942800ef44dc17c777ffa6df0a81261a275b526a9737474c77ba",
            "support_bundle_id": "ec061f7b186af215e14daaaac64333fa3e88c8f934f78fe055d1f3ef091ace0b",
            "support_manifest_sha256": "1117b36cc27c5dbb4d69b26420240d3fbaf8b0d20b8e97ef6ba5490a87bc0438",
            "shared_dispatch_sha256": "a0b0200c0dbca23350840a8c11af376f9cab30f4ca09fd7944ea4a5ef0cb3a01",
            "algorithm_contract": {
                "active_parameters": ["io_block_read_cost", "memory_block_read_cost", "row_evaluate_cost", "memory_temptable_row_cost"],
                "parameter_count": 4,
                "all_nonempty_subsets": 15,
                "gradient_formula": "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)",
                "candidate_order": "global_descending_final_candidate_score",
                "fixed_real_replay_top_k": 5,
                "fingerprint_levels_required": ["l1", "l2", "l3"],
                "qps_role": "outer_acceptance_after_real_replay_only",
                "transport": "exact_client_docker_exec_stdout_v1",
            },
            "authority_contract": {
                "debug_only": True,
                "accepted_round_authorized": False,
                "accepted_rounds_credit": 0,
                "formal_launch_authorized": False,
                "fail_closed": True,
                "main_agent_token_file_only": True,
            },
            "entrypoints": {
                "materializer": "materialize/mysql_job_post_r0_debug_materializer_v1.py",
                "campaign_cli": "campaign/mysql_job_post_r0_debug_campaign_v1.py",
                "exact_services": "campaign/mysql_job_post_r0_exact_services_v1.py",
                "pass_debug_round_producer": "campaign/mysql_job_post_r0_debug_evidence_v1.py",
            },
            "files": files,
        }
        bundle_id = hashlib.sha256(canonical(identity)).hexdigest()
        manifest = {**identity, "bundle_id": bundle_id}
        manifest_path = stage / "bundle_manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (stage / "bundle_manifest.json.sha256").write_text(sha(manifest_path) + "\n", encoding="utf-8")
        closure = {
            "schema_version": "exact_file_closure_v1",
            "bundle_id": bundle_id,
            "files": files,
            "excluded_control_files": ["bundle_manifest.json", "bundle_manifest.json.sha256", "closure.json", "closure.json.sha256"],
        }
        closure_path = stage / "closure.json"
        closure_path.write_text(json.dumps(closure, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (stage / "closure.json.sha256").write_text(sha(closure_path) + "\n", encoding="utf-8")
        for item in sorted(stage.rglob("*"), reverse=True):
            if item.is_symlink():
                raise RuntimeError("successor closure contains a symlink")
            if item.is_file():
                os.chmod(item, 0o444 if item.suffix != ".py" else 0o555)
            elif item.is_dir():
                os.chmod(item, 0o555)
        os.chmod(stage, 0o555)
        destination = OUTPUT / bundle_id
        if destination.exists():
            existing = json.loads((destination / "bundle_manifest.json").read_text())
            if existing != manifest:
                raise RuntimeError("existing bundle identity differs")
        else:
            os.rename(stage, destination)
        print(json.dumps({"bundle_id": bundle_id, "path": str(destination), "manifest_sha256": sha(destination / "bundle_manifest.json")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
