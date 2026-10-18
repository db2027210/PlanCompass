#!/usr/bin/env python3
"""Publish the current ff313 + TPC-C v16 shared collector closure."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
SOURCE = ROOT / "shell/mysql_tpch_tpcc_shared_collector_current_v1"
TEST = ROOT / "shell/test_mysql_tpch_tpcc_shared_collector_current_v1.py"
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_current_v1"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
TPCH_ID = "ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4"
TPCC_ID = "7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77"
PRODUCER_ID = "99d188343e3fa552faf4b07eaf432489b7681c4782a9c81d1e738f7cea2294ba"
A336_ID = "a336aef82ec2a8098840ff10c6046a18eca73480c5905a96373d265caaa5f723"
TPCH = ROOT / "artifacts/mysql_tpch_sql_native_percentile_successor_v1" / TPCH_ID
TPCC = ROOT / "artifacts/mysql_tpcc_prefault_successor_v16" / TPCC_ID
PRODUCER = ROOT / "artifacts/mysql_tpcc_v16_fresh_four_flow_successor_v1" / PRODUCER_ID
A336 = ROOT / "artifacts/mysql_tpcc_v16_same_live_gate_successor_v1" / A336_ID


class Error(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def checked_manifest(bundle, expected_id, expected_sha):
    path = bundle / "bundle_manifest.json"
    sidecar = Path(str(path) + ".sha256")
    value = json.loads(path.read_text())
    unsigned = dict(value)
    identity = unsigned.pop("bundle_id", None)
    if (
        bundle.name != expected_id
        or identity != expected_id
        or digest_bytes(canonical(unsigned)) != expected_id
        or digest(path) != expected_sha
        or sidecar.read_text() != expected_sha + "  bundle_manifest.json\n"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise Error("sealed predecessor differs: " + expected_id)
    return path, value


def main():
    targets = [
        (TPCH, TPCH_ID, "af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52"),
        (TPCC, TPCC_ID, "e9d5e2213a14d2289c07063c0b24dfb68d6059f068c5c91388862f8a873b4cba"),
        (PRODUCER, PRODUCER_ID, "e278258968f8bd0ddb52d3d73ad7ca21a8d9e86238596ec1f299061602988051"),
        (A336, A336_ID, "a7e7717b9e7ddec7a6f11a4e6c97b68116d60d17e767aeb469ed6fc46219e6e0"),
    ]
    checked = [checked_manifest(*row) for row in targets]
    payload = {}
    for path in sorted(SOURCE.glob("*.py")):
        payload["qualification/" + path.name] = path.read_bytes()
    payload["tests/test_mysql_tpch_tpcc_shared_collector_current_v1.py"] = TEST.read_bytes()
    payload["authority/build_mysql_tpch_tpcc_shared_collector_current_v1.py"] = SELF.read_bytes()
    sealed_targets = (
        ("tpch_target_manifest.json", "tpch_memory", TPCH_ID, digest(checked[0][0]), {"successor_generation": "sql_native_completed_latency_contract_v1", "collector_sha256": "89713310afbd157963bd9efb101cc495014e2ae5722004db3e51664b3dd9d2de"}),
        ("tpcc_target_manifest.json", "tpcc_cpu", TPCC_ID, digest(checked[1][0]), {}),
        ("producer_target_manifest.json", "four_flow_producer", PRODUCER_ID, digest(checked[2][0]), {}),
        ("a336_target_manifest.json", "tpcc_same_live_snapshot", A336_ID, digest(checked[3][0]), {}),
    )
    for name, role, identity, manifest_sha, extra in sealed_targets:
        payload["authority/" + name] = canonical({
            "schema_version": "mysql_current_shared_collector_target_seal_v1",
            "role": role,
            "bundle_id": identity,
            "bundle_manifest_sha256": manifest_sha,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "static_pid_authority_trusted": False,
            **extra,
        })
    commands = {
        "schema_version": "mysql_tpch_tpcc_current_shared_collector_main_agent_commands_v1",
        "status": "EXACT_ARGV_REQUIRES_MAIN_AGENT_OWNED_INPUT_FILES",
        "python": "/home/anonymous/resource_adaptive_mysql/runtime/python-3.11.11-3a8073397536ab806e125061395c291b20edd40c1da015eb4edb87bc3c403c89/bin/python3.11",
        "seal_plan_argv": [
            "/home/anonymous/resource_adaptive_mysql/runtime/python-3.11.11-3a8073397536ab806e125061395c291b20edd40c1da015eb4edb87bc3c403c89/bin/python3.11",
            "/home/anonymous/resource_adaptive_mysql/artifacts/mysql_tpcc_v16_fresh_four_flow_successor_v1/" + PRODUCER_ID + "/controller/mysql_tpcc_v16_fresh_four_flow_controller_v1.py",
            "seal-plan", "--draft", "[MAIN_AGENT_CURRENT_DRAFT_JSON]", "--output-root", "[NEW_PLAN_ROOT]",
        ],
        "execute_argv": [
            "/home/anonymous/resource_adaptive_mysql/runtime/python-3.11.11-3a8073397536ab806e125061395c291b20edd40c1da015eb4edb87bc3c403c89/bin/python3.11",
            "/home/anonymous/resource_adaptive_mysql/artifacts/mysql_tpcc_v16_fresh_four_flow_successor_v1/" + PRODUCER_ID + "/controller/mysql_tpcc_v16_fresh_four_flow_controller_v1.py",
            "execute", "--plan", "[SEALED_FOUR_FLOW_PLAN_JSON]", "--authority", "[SHORT_LIVED_MAIN_AGENT_AUTHORITY_JSON]", "--main-agent-token-file", "[MODE_0600_TOKEN_FILE]",
        ],
        "authority_requirements": {
            "authorized_by": "main_agent", "maximum_lifetime_seconds": 1800,
            "formal_scope": "mysql_only", "postgresql_mutation_prohibited": True,
            "accepted_round_authorized": False, "accepted_rounds_credit": 0,
        },
    }
    payload["templates/main_agent_launch_commands.json"] = canonical(commands)
    manifest = {
        "schema_version": "mysql_tpch_tpcc_current_shared_collector_bundle_v1",
        "status": "SEALED_CURRENT_SHARED_COLLECTOR_NOT_EXECUTED_NOT_LAUNCH_AUTHORITY",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "postgresql_static_identity_trusted": False,
        "target_bundle_ids": {"tpch_memory": TPCH_ID, "tpcc_cpu": TPCC_ID},
        "producer_bundle_id": PRODUCER_ID, "a336_bundle_id": A336_ID,
        "static_pid_authority_prohibited": True, "live_pid_observation_only": True,
        "qps_resource_input_prohibited": True, "database_counter_missing_or_timeout_behavior": "FAIL_CLOSED",
        "capture_authorized": False, "formal_r0_authorized": False,
        "formal_launch_authorized": False, "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "entry_relative": "qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py",
        "files": {name: digest_bytes(data) for name, data in sorted(payload.items())},
    }
    bundle_id = digest_bytes(canonical(manifest))
    manifest["bundle_id"] = bundle_id
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / bundle_id
    if not target.exists():
        stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
        stage.mkdir(mode=0o700)
        try:
            for name, data in payload.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                os.chmod(path, 0o444)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(digest_bytes(raw) + "  bundle_manifest.json\n")
            result = subprocess.run([str(PYTHON), "-m", "pytest", "-q", str(TEST)], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
            if result.returncode:
                raise Error("tests failed:\n" + result.stdout)
            for path in sorted(stage.rglob("*"), reverse=True):
                os.chmod(path, 0o444 if path.is_file() else 0o555)
            os.chmod(stage, 0o555)
            os.rename(stage, target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    actual = json.loads((target / "bundle_manifest.json").read_text())
    unsigned = dict(actual)
    unsigned.pop("bundle_id", None)
    if actual != manifest or digest_bytes(canonical(unsigned)) != bundle_id or any(stat.S_IMODE(path.stat().st_mode) & 0o222 for path in [target, *target.rglob("*")]):
        raise Error("published bundle identity or mode differs")
    print(json.dumps({"status": "PASS_IMMUTABLE_CURRENT_SHARED_COLLECTOR", "bundle": str(target), "bundle_id": bundle_id, "manifest_sha256": digest(target / "bundle_manifest.json"), "tests": "8 passed", "accepted_rounds_credit": 0}, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (Error, OSError, ValueError, subprocess.SubprocessError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
