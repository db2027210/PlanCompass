#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/mysql_tpcc_v16_post_tpch_retry9_mutex_one_click_successor_v1"
FILES = {
    "controller/mysql_tpcc_v16_post_tpch_retry9_mutex_one_click_successor_v1.py":
        ROOT / "shell/mysql_tpcc_v16_post_tpch_retry9_mutex_one_click_successor_v1.py",
    "tests/test_mysql_tpcc_v16_post_tpch_retry9_mutex_one_click_successor_v1.py":
        ROOT / "tests/test_mysql_tpcc_v16_post_tpch_retry9_mutex_one_click_successor_v1.py",
    "builder/build_mysql_tpcc_v16_post_tpch_retry9_mutex_one_click_successor_v1.py":
        Path(__file__).resolve(),
}


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    unsigned = {
        "schema_version": "mysql_tpcc_v16_post_tpch_retry9_mutex_one_click_bundle_v1",
        "status": "SEALED_WAIT_FOR_FULL_RETRY9_COMPLETION_THEN_TPCC_LOCAL_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "immutable": True,
        "host": "node04",
        "local_only": True,
        "tpch_retry9_bundle_id": "1ef07fd3c8b547598d3c6d1419c11383abf08ca7d23d8c85e8820707963e0bf3",
        "tpch_retry9_manifest_sha256": "88e06baaec02c84e3cebb78cd74bf2e06b03de674118766aac23581d4c7963bd",
        "tpcc_predecessor_bundle_id": "025b4b9595755761b4c10bdb3324dc8f7dc2f7edea76bb906e166de4dcbdb6d8",
        "tpcc_predecessor_manifest_sha256": "de362d1680ae78611487a5bab9109937d5f31ce941085c81d05a8bbcdfe45a04",
        "mutex": "/run/lock/ra-mysql-node04-tpch-tpcc-fourflow.lock",
        "bootstrap_only_parallel_window_authorized": False,
        "retry9_full_completion_required": True,
        "same_attempt_spec_evidence_defaults_required": True,
        "mutex_held_for_full_tpcc_predecessor_window": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions_during_build": 0,
        "files": {relative: sha(source) for relative, source in FILES.items()},
    }
    identity = hashlib.sha256(canonical(unsigned)).hexdigest()
    final = OUTPUT / identity
    if final.exists():
        manifest = json.loads((final / "bundle_manifest.json").read_text())
        assert manifest == {**unsigned, "bundle_id": identity}
        print(final)
        return
    OUTPUT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=OUTPUT))
    try:
        for relative, source in FILES.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        manifest = {**unsigned, "bundle_id": identity}
        raw = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            hashlib.sha256(raw).hexdigest() + "  bundle_manifest.json\n"
        )
        for path in sorted(stage.rglob("*"), reverse=True):
            path.chmod(0o555 if path.is_dir() else 0o444)
        stage.chmod(0o555)
        stage.rename(final)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print(final)


if __name__ == "__main__":
    main()
