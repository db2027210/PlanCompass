#!/usr/bin/env python3
"""Build v27 from v25 with a restart-specific TPC-H cgroup authority."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location(
    "v25", ROOT / "shell/build_mysql_tpch_tpcc_shared_collector_v25.py"
)
v25 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v25)
u = v25.u

PARENT_ID = "97e625ef164ab1b27f4b2edc647ec7f1a8b54ac196be97366d53fb38726a88ec"
PARENT_SHA = "59457555ecb3b24b18fb2b458b64ecb77588493a7a442de3f7c98ae92c6e4e26"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v25" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v27"
ENTRY = "qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v27.py"
REMEDIATOR = "authority/remediate_mysql_tpch_restart_authority_v1.py"
REMEDIATOR_SOURCE = ROOT / "shell/remediate_mysql_tpch_restart_authority_v1.py"
INVALIDATION = "authority/tpch_v25c_restart_authority_invalidation.json"
TEST = "tests/test_shared_collector_tpch_restart_authority_v27.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")

AUTHORITY_PATH = (
    "/home/anonymous/resource_adaptive_mysql/authorities/"
    "mysql_tpch_restart_blkio_v1/"
    "16f1b6fa6d904ab443050995595473259f47db20c80ad0c6213ece7afc754340/"
    "blkio_throttle_authority.json"
)
AUTHORITY_FILE_SHA256 = (
    "968d957a66df984c98a0024abe663feb0a9703cf7adc3a2799f132b428b4ca11"
)
AUTHORITY_INTERNAL_SHA256 = (
    "16f1b6fa6d904ab443050995595473259f47db20c80ad0c6213ece7afc754340"
)
REMEDIATOR_SHA256 = (
    "19c90fbe54e3b5f7b10e1c46900fb9830a59405a19606e94ae41efdab2a93d47"
)
DATABASE_CONTAINER_ID = (
    "7c395d290f115dbac9c1146ee8b7599de5967888b19965fa5c114692c1912250"
)
DATABASE_PID = 81330


def load_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text())
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != PARENT_ID
        or u.digest(path) != PARENT_SHA
        or u.sha_bytes(u.canonical(unsigned)) != PARENT_ID
        or manifest.get("schema_version")
        != "mysql_tpch_tpcc_shared_collector_bundle_v25"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise u.BuildError("v25 parent differs")
    for relative, expected in manifest["files"].items():
        item = PARENT / relative
        if item.is_symlink() or not item.is_file() or u.digest(item) != expected:
            raise u.BuildError("v25 closure differs: " + relative)
    if u.digest(REMEDIATOR_SOURCE) != REMEDIATOR_SHA256:
        raise u.BuildError("restart remediator source differs")
    return manifest


def invalidation():
    return {
        "schema_version": "mysql_tpch_v25c_restart_authority_invalidation_v1",
        "status": "INVALIDATED_ZERO_CREDIT_REPLACED_BY_V27",
        "failed_attempt": "tpch-a-solo-v25c-20260802t004017p08-6c3809b8",
        "failure": (
            "the TPC-H database restart retained the container ID but changed the "
            "live PID from 57773 to 81330 and reset cgroup-v1 blkio/swappiness; "
            "the immutable pre-restart authority correctly failed closed"
        ),
        "repair": (
            "restore only the exact MySQL cgroup controls, publish an append-only "
            "content-addressed authority bound to PID 81330, and pin its path plus "
            "file/internal hashes in a v25-core successor"
        ),
        "collector_core_changed": False,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "postgresql_mutation": False,
    }


def test_source():
    return b'''from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_v27_restart_authority_is_exact_and_v25_core_is_preserved():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v27"
 assert m["restart_specific_tpch_authority"] is True
 assert m["collector_core_changed_from_v25"] is False
 entry=(ROOT/"qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py").read_text()
 assert "tpch_restart_blkio_authority" in entry
 assert "TPC-H restart blkio authority file differs" in entry
 assert "database_pid_starttime_ticks" in entry
 assert "remediation_script_sha256" in entry
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
def test_v27_embeds_exact_remediator_identity():
 m=json.loads((ROOT/"bundle_manifest.json").read_text())
 p=ROOT/"authority/remediate_mysql_tpch_restart_authority_v1.py"
 assert hashlib.sha256(p.read_bytes()).hexdigest()==m["tpch_restart_remediator_sha256"]
 assert m["files"]["authority/remediate_mysql_tpch_restart_authority_v1.py"]==m["tpch_restart_remediator_sha256"]
'''


def main():
    parent = load_parent()
    payload = {r: (PARENT / r).read_bytes() for r in parent["files"]}
    modes = {r: stat.S_IMODE((PARENT / r).stat().st_mode) for r in parent["files"]}
    entry = payload[ENTRY]
    entry = u.replace_exact(
        entry,
        b'''        "credential_authority_internal_sha256": "0a515a8e1162f8fb217b61458c483fa9ed654b2c711f252255632a46220d47b8",\n''',
        b'''        "credential_authority_internal_sha256": "0a515a8e1162f8fb217b61458c483fa9ed654b2c711f252255632a46220d47b8",\n        "tpch_restart_blkio_authority": "'''
        + AUTHORITY_PATH.encode("ascii")
        + b'''",\n        "tpch_restart_blkio_authority_file_sha256": "'''
        + AUTHORITY_FILE_SHA256.encode("ascii")
        + b'''",\n        "tpch_restart_blkio_authority_internal_sha256": "'''
        + AUTHORITY_INTERNAL_SHA256.encode("ascii")
        + b'''",\n        "tpch_restart_remediator_sha256": "'''
        + REMEDIATOR_SHA256.encode("ascii")
        + b'''",\n        "tpch_restart_database_container_id": "'''
        + DATABASE_CONTAINER_ID.encode("ascii")
        + b'''",\n        "tpch_restart_database_pid": 81330,\n''',
        "TPC-H restart authority profile",
    )
    entry = u.replace_exact(
        entry,
        b'''    attempt_root = defaults_file.parent.parent\n    authority_path = attempt_root / "blkio_throttle_authority.json"\n    value = core.load_object(authority_path, "effective blkio authority")\n    sidecar = authority_path.with_name(authority_path.name + ".sha256")\n    if sidecar.is_file() and not core.valid_sidecar(authority_path):\n        raise core.QualificationError("effective blkio authority sidecar differs")\n    scenario = runtime["scenario"]\n''',
        b'''    attempt_root = defaults_file.parent.parent\n    scenario = runtime["scenario"]\n    profile = _profile_for_scenario(scenario)\n    authority_path = (\n        Path(profile["tpch_restart_blkio_authority"])\n        if scenario == "tpch_memory"\n        else attempt_root / "blkio_throttle_authority.json"\n    )\n    value = core.load_object(authority_path, "effective blkio authority")\n    sidecar = authority_path.with_name(authority_path.name + ".sha256")\n    if scenario == "tpch_memory":\n        if (\n            not sidecar.is_file()\n            or not core.valid_sidecar(authority_path)\n            or core.sha256(authority_path)\n            != profile["tpch_restart_blkio_authority_file_sha256"]\n        ):\n            raise core.QualificationError(\n                "TPC-H restart blkio authority file differs"\n            )\n    elif sidecar.is_file() and not core.valid_sidecar(authority_path):\n        raise core.QualificationError("effective blkio authority sidecar differs")\n''',
        "restart authority selection",
    )
    entry = u.replace_exact(
        entry,
        b'''    if scenario == "tpch_memory":\n        unsigned = dict(value)\n        recorded = unsigned.pop("authority_sha256", None)\n        if recorded != core.sha_bytes(core.canonical(unsigned)):\n            raise core.QualificationError("TPC-H blkio authority digest differs")\n''',
        b'''    if scenario == "tpch_memory":\n        unsigned = dict(value)\n        recorded = unsigned.pop("authority_sha256", None)\n        if (\n            recorded != core.sha_bytes(core.canonical(unsigned))\n            or recorded\n            != profile["tpch_restart_blkio_authority_internal_sha256"]\n            or value.get("remediation_script_sha256")\n            != profile["tpch_restart_remediator_sha256"]\n            or value.get("database_container_id")\n            != profile["tpch_restart_database_container_id"]\n            or int(value.get("database_pid") or 0)\n            != int(profile["tpch_restart_database_pid"])\n            or int(value.get("database_pid_starttime_ticks") or 0)\n            != core.process_start_ticks(pid)\n            or value.get("memory_swappiness") != 0\n            or value.get("total_swap_bytes") != 0\n            or value.get("database_restarted_by_remediation") is not False\n            or value.get("postgresql_mutation_performed") is not False\n            or value.get("storage_mutation_performed") is not False\n        ):\n            raise core.QualificationError(\n                "TPC-H restart blkio authority digest/identity differs"\n            )\n''',
        "restart authority identity",
    )
    payload[ENTRY] = entry
    for relative, content in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = content.replace(
                b"mysql_tpch_tpcc_shared_collector_bundle_v25",
                b"mysql_tpch_tpcc_shared_collector_bundle_v27",
            )
    payload[BUILDER] = SOURCE.read_bytes()
    modes[BUILDER] = 0o444
    payload[REMEDIATOR] = REMEDIATOR_SOURCE.read_bytes()
    modes[REMEDIATOR] = 0o444
    payload[INVALIDATION] = u.canonical(invalidation())
    modes[INVALIDATION] = 0o444
    payload[TEST] = test_source()
    modes[TEST] = 0o444
    unsigned = {k: v for k, v in parent.items() if k not in {"bundle_id", "files"}}
    unsigned.update(
        {
            "schema_version": "mysql_tpch_tpcc_shared_collector_bundle_v27",
            "status": "SEALED_TPCH_RESTART_AUTHORITY_NOT_LAUNCH_AUTHORITY",
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_manifest_sha256": PARENT_SHA,
            "restart_specific_tpch_authority": True,
            "tpch_restart_authority_path": AUTHORITY_PATH,
            "tpch_restart_authority_file_sha256": AUTHORITY_FILE_SHA256,
            "tpch_restart_authority_internal_sha256": AUTHORITY_INTERNAL_SHA256,
            "tpch_restart_database_container_id": DATABASE_CONTAINER_ID,
            "tpch_restart_database_pid": DATABASE_PID,
            "tpch_restart_remediator_sha256": REMEDIATOR_SHA256,
            "collector_core_changed_from_v25": False,
            "exact_dedicated_daemon_attribution_from_v25_entry_preserved": True,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "files": {r: u.sha_bytes(d) for r, d in sorted(payload.items())},
        }
    )
    if unsigned["files"][CORE] != parent["files"][CORE]:
        raise u.BuildError("v25 collector core changed")
    bundle_id = u.sha_bytes(u.canonical(unsigned))
    manifest = dict(unsigned, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / bundle_id
    result_text = "existing immutable bundle reverified"
    if not destination.exists():
        stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
        stage.mkdir(mode=0o700)
        try:
            for relative, content in payload.items():
                item = stage / relative
                item.parent.mkdir(parents=True, exist_ok=True)
                item.write_bytes(content)
                os.chmod(item, modes.get(relative, 0o444) & ~0o222)
            raw = u.canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                u.sha_bytes(raw) + "  bundle_manifest.json\n"
            )
            temp = Path(tempfile.mkdtemp(prefix="mysql-shared-v27-test-"))
            scratch = temp / bundle_id
            try:
                shutil.copytree(stage, scratch)
                tests = sorted(str(p) for p in (scratch / "tests").glob("test_*.py"))
                result = subprocess.run(
                    [str(PYTHON), "-m", "pytest", "-q", *tests],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=180,
                )
                if result.returncode:
                    raise u.BuildError("v27 tests failed:\n" + result.stdout[-16000:])
                result_text = result.stdout.strip()
            finally:
                shutil.rmtree(temp)
            u.clean(stage)
            u.freeze(stage)
            os.rename(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    published = json.loads((destination / "bundle_manifest.json").read_text())
    check = dict(published)
    check.pop("bundle_id", None)
    if (
        published != manifest
        or u.sha_bytes(u.canonical(check)) != bundle_id
        or u.file_map(destination) != manifest["files"]
        or any(
            stat.S_IMODE(path.stat().st_mode) & 0o222
            for path in [destination, *destination.rglob("*")]
        )
    ):
        raise u.BuildError("published v27 differs")
    print(
        json.dumps(
            {
                "status": "PASS_IMMUTABLE_SHARED_COLLECTOR_V27",
                "bundle": str(destination),
                "bundle_id": bundle_id,
                "bundle_manifest_sha256": u.digest(
                    destination / "bundle_manifest.json"
                ),
                "verified_files": len(manifest["files"]),
                "tests": result_text,
                "formal_launch_authorized": False,
                "accepted_round_authorized": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (u.BuildError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("NOT_READY: " + str(exc), file=__import__("sys").stderr)
        raise SystemExit(2)
