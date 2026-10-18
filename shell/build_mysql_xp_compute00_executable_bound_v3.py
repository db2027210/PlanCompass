#!/usr/bin/env python3
"""Build the deterministic, executable-bound compute00 XP successor offline."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
PARENT_ID = "b4bd3255634e952e33ea603bbd4ec4b5eb0493450987c041ca3a220414c8a7c4"
PARENT_MANIFEST_SHA256 = "846bfd84dee75bc5180b08bc82d1e3982f9bbf347b661c5571135a6f8b24efe3"
PARENT = ROOT / "artifacts/mysql_xp_compute00_canonical_template_v2" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_xp_compute00_executable_bound_v3"
TEST_SOURCE = ROOT / "shell/test_mysql_xp_compute00_executable_binding_v3.py"
RUNBOOK_SOURCE = ROOT / "shell/mysql_xp_compute00_fresh_r0_runbook_v1.md"

SCHEMA = "mysql_xp_compute00_executable_bound_successor_v3"
TEMPLATE = "/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/template_v2"
RUNTIME = "/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/runtime_algorithm_c_successor_20260803"
DB_CPUS = "184-191,440-447"
CLIENT_CPUS = "192-199,448-455"
RESERVED_CPUS = "184-199,440-455"
PG_CPUS = "32-51,288-307"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                   allow_nan=False) + "\n"
    ).encode("utf-8")


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise BuildError("%s patch point count differs: %r" % (label, old[:100]))
    return text.replace(old, new)


def writable(root):
    root.chmod(0o755)
    for path in root.rglob("*"):
        if path.is_symlink():
            raise BuildError("bundle symlink prohibited")
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            path.chmod(stat.S_IMODE(path.stat().st_mode) | 0o200)


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("bundle symlink prohibited")
        path.chmod(0o555 if path.is_dir() or stat.S_IMODE(path.stat().st_mode) & 0o111 else 0o444)
    root.chmod(0o555)


def files(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative not in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
            result[relative] = sha(path)
    return result


def patch_schema_consumer(path):
    text = path.read_text(encoding="utf-8")
    if SCHEMA not in text:
        needle = '"mysql_xp_compute00_successor_bundle_v28",'
        if text.count(needle) != 1:
            raise BuildError("schema tuple anchor absent: " + str(path))
        line_start = text.index(needle)
        line_start = text.rfind("\n", 0, line_start) + 1
        indent = text[line_start:text.index(needle)]
        anchor = indent + needle + "\n"
        text = text.replace(anchor, anchor + indent + '"%s",\n' % SCHEMA, 1)
    path.write_text(text, encoding="utf-8")


def patch_active_files(stage):
    service = stage / "launch/mysql_xp_compute00_adopted_service_v1.py"
    text = service.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'TEMPLATE = MOUNT + "/anonymous/resource_adaptive_mysql/hybench_xp/template_v2"',
        'TEMPLATE = "%s"' % TEMPLATE,
        "service template",
    )
    text = replace_once(
        text,
        'RUNTIME_ROOT = (\n    MOUNT\n    + "/anonymous/resource_adaptive_mysql/hybench_xp/"\n    "runtime_algorithm_c_successor_20260729"\n)',
        'RUNTIME_ROOT = "%s"' % RUNTIME,
        "service runtime",
    )
    text = replace_once(text, "CLIENT_CPU_QUOTA = 1800000", "CLIENT_CPU_QUOTA = 800000", "service client quota")
    text = replace_once(text, 'CLIENT_MEMS = "0"', 'CLIENT_MEMS = "1"', "service client mems")
    text = replace_once(
        text,
        '        != "mysql_xp_compute00_successor_bundle_v28"',
        '        != "%s"' % SCHEMA,
        "service schema",
    )
    text = replace_once(
        text,
        "\ndef adopt(plan, copy_authority, output):\n",
        '''
def _same_live_identity(plan, database, client):
    return {
        "database_container_id": database["container_id"],
        "mysql_pid": database["pid"],
        "mysql_starttime_ticks": database["pid_starttime_ticks"],
        "physical_datadir": plan["database"]["datadir"],
        "client_container_id": client["container_id"],
        "client_init_pid": client["pid"],
        "client_init_starttime_ticks": client["pid_starttime_ticks"],
        "attempt_id": plan["attempt_id"],
        "handoff_token_sha256": plan["handoff_token_sha256"],
    }


def adopt(plan, copy_authority, output):
''',
        "service same-live helper",
    )
    text = replace_once(
        text,
        '        "database": db,\n        "client": cl,\n',
        '        "database": db,\n        "client": cl,\n        "same_live_identity": _same_live_identity(plan, db, cl),\n',
        "service adoption same-live binding",
    )
    service.write_text(text, encoding="utf-8")

    executor = stage / "launch/mysql_xp_fresh_r0_executor_v2.py"
    text = executor.read_text(encoding="utf-8")
    text = replace_once(text, 'CLIENT = {"cpu_quota_cores": 18,', 'CLIENT = {"cpu_quota_cores": 8,', "fresh client quota")
    text = replace_once(text, '          "cpuset_mems": "0",', '          "cpuset_mems": "1",', "fresh client mems")
    executor.write_text(text, encoding="utf-8")

    producer = stage / "launch/mysql_xp_formal_evidence_producer_v1.py"
    text = producer.read_text(encoding="utf-8")
    text = replace_once(text, 'client.get("cpuset_mems") == "0"', 'client.get("cpuset_mems") == "1"', "producer client mems")
    text = replace_once(text, 'cgroup.get("cpu_quota_cores") == 18', 'cgroup.get("cpu_quota_cores") == 8', "producer client quota")
    text = replace_once(text, 'cgroup.get("mems_allowed_list") == "0"', 'cgroup.get("mems_allowed_list") == "1"', "producer cgroup mems")
    producer.write_text(text, encoding="utf-8")

    authority = stage / "qualification/mysql_xp_qualification_authority_v1.py"
    text = authority.read_text(encoding="utf-8")
    text = replace_once(text, 'CLIENT_MEMSET = "0"', 'CLIENT_MEMSET = "1"', "authority client mems")
    authority.write_text(text, encoding="utf-8")

    for relative in (
        "launch/mysql_xp_compute00_formal10_materializer_v1.py",
        "qualification/mysql_distributed_live_readback_qualification_v1.py",
        "qualification/mysql_xp_qualification_authority_v1.py",
    ):
        patch_schema_consumer(stage / relative)

    materials = stage / "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
    text = materials.read_text(encoding="utf-8")
    mapping_anchor = (
        '    "mysql_xp_compute00_successor_bundle_v28": '
        '"a0187979839c8fdb9530da525425a031bca9d955234d754cc495dbe0d5b342e9",\n'
    )
    if SCHEMA not in text:
        text = replace_once(
            text, mapping_anchor,
            mapping_anchor + '    "%s": "%s",\n' % (SCHEMA, PARENT_ID),
            "materials schema mapping",
        )
    text = replace_once(
        text,
        'REMOTE_RUNTIME_ROOT = (\n    "/disk1/anonymous/resource_adaptive_mysql/hybench_xp/"\n    "runtime_algorithm_c_successor_20260729"\n)',
        'REMOTE_RUNTIME_ROOT = "%s"' % RUNTIME,
        "materials runtime",
    )
    text = replace_once(text, 'or client.get("cpuset_mems") != "0"', 'or client.get("cpuset_mems") != "1"', "materials client mems")
    text = replace_once(
        text,
        '        or client.get("cpuset_cpus") != "192-199,448-455"\n',
        '        or client.get("cpu_quota_cores") != 8\n        or client.get("cpuset_cpus") != "192-199,448-455"\n',
        "materials client quota gate",
    )
    text = replace_once(
        text,
        '                "data_partition_and_throttle_parent_must_not_be_conflated": True,\n                "unique_partition_required": True,\n',
        '                "data_partition_and_throttle_parent_must_not_be_conflated": storage["data_partition_and_throttle_parent_must_not_be_conflated"],\n                "unique_partition_required": storage["unique_partition_required"],\n',
        "materials storage topology",
    )
    materials.write_text(text, encoding="utf-8")


def update_path_hashes(value, stage):
    if isinstance(value, dict):
        relative = value.get("path")
        if isinstance(relative, str) and isinstance(value.get("sha256"), str):
            candidate = stage / relative
            if candidate.is_file():
                value["sha256"] = sha(candidate)
        for item in value.values():
            update_path_hashes(item, stage)
    elif isinstance(value, list):
        for item in value:
            update_path_hashes(item, stage)


def bind_contract(path, stage):
    value = json.loads(path.read_text(encoding="utf-8"))
    db = value["database_resource_envelope"]
    client = value["client_resource_envelope"]
    storage = value["storage"]
    host = value["distributed_host_contract"]
    db.update({
        "cpu_quota_cores": 5.3, "cpuset_cpus": DB_CPUS,
        "cpuset_mems": "1", "io_read_bps": 29360128,
        "io_write_bps": 524288, "memory_bytes": 8589934592,
        "memory_swap_bytes": 8589934592, "pids_max": 1024,
        "innodb_buffer_pool_bytes": 4294967296, "mysql_port": 65104,
    })
    client.update({
        "cpu_quota_cores": 8, "cpuset_cpus": CLIENT_CPUS,
        "cpuset_mems": "1",
    })
    storage.update({
        "mount": "/disk1", "device": "/dev/nvme1n1",
        "data_partition": "/dev/nvme1n1", "data_partition_major_minor": "259:6",
        "expected_live_major_minor": "259:6",
        "io_throttle_parent_device": "/dev/nvme1n1",
        "io_throttle_parent_major_minor": "259:6",
        "docker_bps_declaration_device": "/dev/nvme1n1",
        "effective_cgroup_v1_throttle_major_minor": "259:6",
        "runtime_root": RUNTIME, "template_path": TEMPLATE,
        "data_partition_and_throttle_parent_must_not_be_conflated": False,
        "unique_partition_required": False,
        "whole_disk_filesystem_and_throttle_identity_same": True,
    })
    host.update({
        "host_id": "compute00", "database_cpuset_cpus": DB_CPUS,
        "database_cpuset_mems": "1", "client_cpuset_cpus": CLIENT_CPUS,
        "client_cpuset_mems": "1", "mysql_xp_reserved_cpuset": RESERVED_CPUS,
        "postgresql_reserved_cpuset": PG_CPUS,
        "postgresql_mysql_cpuset_overlap": False,
    })
    paths = {
        "adopted_service": "launch/mysql_xp_compute00_adopted_service_v1.py",
        "fresh_executor": "launch/mysql_xp_fresh_r0_executor_v2.py",
        "formal_evidence_producer": "launch/mysql_xp_formal_evidence_producer_v1.py",
        "promotion_materializer": "launch/mysql_xp_compute00_formal10_materializer_v1.py",
        "qualification_authority": "qualification/mysql_xp_qualification_authority_v1.py",
        "live_readback": "qualification/mysql_distributed_live_readback_qualification_v1.py",
        "preflight": "preflight/mysql_xp_compute00_disk1_preflight_v2.py",
    }
    hashes = {label: sha(stage / relative) for label, relative in paths.items()}
    value["executable_binding"] = {
        "schema_version": "mysql_xp_compute00_executable_binding_v1",
        "status": "PASS_STATIC_EXACT_BINDING_NOT_LAUNCH_AUTHORITY",
        "bundle_schema": SCHEMA,
        "host": "compute00",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "template_path": TEMPLATE,
        "runtime_root": RUNTIME,
        "device": "/dev/nvme1n1",
        "device_major_minor": "259:6",
        "database": dict(db),
        "client": {key: client[key] for key in (
            "cpu_quota_cores", "cpuset_cpus", "cpuset_mems", "memory_bytes",
            "memory_swap_bytes", "pids_max",
        )},
        "reserved_cpuset": RESERVED_CPUS,
        "postgresql_reserved_cpuset": PG_CPUS,
        "postgresql_mysql_cpuset_overlap": False,
        "entrypoint_sha256": hashes,
        "preflight_pass_is_launch_authority": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }
    if "adopted_service" in value:
        value["adopted_service"]["sha256"] = hashes["adopted_service"]
    if "fresh_r0_execution" in value:
        value["fresh_r0_execution"]["executor_sha256"] = hashes["fresh_executor"]
    if "formal_evidence_producer" in value:
        value["formal_evidence_producer"]["sha256"] = hashes["formal_evidence_producer"]
    if "promotion_materializer" in value:
        value["promotion_materializer"]["sha256"] = hashes["promotion_materializer"]
    if "qualification_authority" in value:
        value["qualification_authority"]["sha256"] = hashes["qualification_authority"]
    if "collector_sha256" in value:
        value["collector_sha256"] = hashes["live_readback"]
    if "bidirectional_qualification_gate" in value:
        value["bidirectional_qualification_gate"]["collector_sha256"] = hashes["live_readback"]
    if "private_runtime_workload_properties" in value:
        value["private_runtime_workload_properties"]["fresh_executor_sha256"] = hashes["fresh_executor"]
    if "runtime_entrypoints" in value:
        entrypoints = value.get("runtime_entrypoints") or []
        for relative in paths.values():
            if relative not in entrypoints:
                entrypoints.append(relative)
        value["runtime_entrypoints"] = entrypoints
    update_path_hashes(value, stage)
    path.write_bytes(canonical(value))


def validate_target(target, bundle_id):
    manifest_path = target / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest)
    observed_id = unsigned.pop("bundle_id", None)
    if (
        target.name != bundle_id
        or observed_id != bundle_id
        or sha_bytes(canonical(unsigned)) != bundle_id
        or files(target) != manifest.get("files")
        or (target / "bundle_manifest.json.sha256").read_text(encoding="ascii")
        != "%s  bundle_manifest.json\n" % sha(manifest_path)
    ):
        raise BuildError("published content-addressed closure differs")
    environment = dict(os.environ)
    environment.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "XP_BUNDLE_UNDER_TEST": str(target),
        "XP_EXPECTED_BUNDLE_ID": bundle_id,
    })
    completed = subprocess.run(
        [str(PYTHON), "-B", str(target / "tests_compute00/test_mysql_xp_compute00_executable_binding_v3.py")],
        cwd=str(ROOT), env=environment, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=120,
    )
    if completed.returncode != 0:
        raise BuildError("sealed executable binding tests failed:\n" + completed.stdout)
    return completed.stdout


def build():
    if sha(PARENT / "bundle_manifest.json") != PARENT_MANIFEST_SHA256:
        raise BuildError("parent manifest identity differs")
    if not TEST_SOURCE.is_file() or not RUNBOOK_SOURCE.is_file():
        raise BuildError("builder source closure is incomplete")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    raw = Path(tempfile.mkdtemp(prefix=".stage-xp-executable-v3-", dir=str(OUTPUT)))
    stage = raw / "bundle"
    try:
        shutil.copytree(str(PARENT), str(stage))
        writable(stage)
        for name in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
            (stage / name).unlink()
        patch_active_files(stage)
        shutil.copy2(str(TEST_SOURCE), str(stage / "tests_compute00/test_mysql_xp_compute00_executable_binding_v3.py"))
        shutil.copy2(str(SELF), str(stage / "builder/build_mysql_xp_compute00_executable_bound_v3.py"))
        (stage / "operator").mkdir(exist_ok=True)
        shutil.copy2(str(RUNBOOK_SOURCE), str(stage / "operator/mysql_xp_compute00_fresh_r0_runbook_v1.md"))
        for relative in ("xp_executable_launch_contract_v3.json", "distributed_runtime_contract.json"):
            bind_contract(stage / relative, stage)
        parent = json.loads((PARENT / "bundle_manifest.json").read_text(encoding="utf-8"))
        manifest = {key: value for key, value in parent.items() if key not in ("bundle_id", "files")}
        manifest.update({
            "schema_version": SCHEMA,
            "status": "SEALED_COMPUTE00_EXECUTABLE_BOUND_NOT_LAUNCHED",
            "predecessor_bundle_id": PARENT_ID,
            "supersedes_bundle_id": PARENT_ID,
            "host_binding": "compute00",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "mysql_xp_reserved_cpuset": RESERVED_CPUS,
            "database_cpuset": DB_CPUS,
            "client_cpuset": CLIENT_CPUS,
            "postgresql_reserved_cpuset": PG_CPUS,
            "cpuset_overlap": False,
            "numa_mems": "1",
            "storage": {
                "mount": "/disk1", "device": "/dev/nvme1n1",
                "major_minor": "259:6", "device_numa_node": 0,
                "whole_disk_filesystem_and_throttle_identity_same": True,
                "runtime_root": RUNTIME, "template_path": TEMPLATE,
            },
            "formal_envelope": {
                "cpu_cores": 5.3, "read_bps": 29360128,
                "write_bps": 524288, "graceful_quiescence_seconds": 120,
            },
            "client_envelope": {
                "cpu_cores": 8, "cpuset_cpus": CLIENT_CPUS,
                "cpuset_mems": "1",
            },
            "service_sha256": sha(stage / "launch/mysql_xp_compute00_adopted_service_v1.py"),
            "collector_sha256": sha(stage / "qualification/mysql_distributed_live_readback_qualification_v1.py"),
            "active_preflight": "preflight/mysql_xp_compute00_disk1_preflight_v2.py",
            "executable_binding_test": "tests_compute00/test_mysql_xp_compute00_executable_binding_v3.py",
            "operator_runbook": "operator/mysql_xp_compute00_fresh_r0_runbook_v1.md",
            "preflight_is_launch_authority": False,
            "formal_launch_authorized": False,
            "formal_r0_authorized": False,
            "accepted_round_authorized": False,
            "accepted_rounds_credit": 0,
            "remote_actions": 0,
            "files": files(stage),
        })
        bundle_id = sha_bytes(canonical(manifest))
        manifest["bundle_id"] = bundle_id
        target = OUTPUT / bundle_id
        if target.exists():
            output = validate_target(target, bundle_id)
            return target, output, "ADOPTED_IDENTICAL"
        (stage / "bundle_manifest.json").write_bytes(canonical(manifest))
        (stage / "bundle_manifest.json.sha256").write_text(
            "%s  bundle_manifest.json\n" % sha(stage / "bundle_manifest.json"),
            encoding="ascii",
        )
        freeze(stage)
        # Keep only the staging root owner-writable for the atomic directory
        # rename; seal the visible content-addressed root immediately after.
        stage.chmod(0o755)
        os.rename(str(stage), str(target))
        target.chmod(0o555)
        output = validate_target(target, bundle_id)
        return target, output, "PUBLISHED_NEW"
    finally:
        if raw.exists():
            writable(raw)
            shutil.rmtree(str(raw))


def main():
    target, test_output, mode = build()
    result = {
        "schema_version": "mysql_xp_compute00_executable_bound_build_result_v1",
        "status": "PASS_IMMUTABLE_EXECUTABLE_BOUND_NOT_LAUNCHED",
        "publish_mode": mode,
        "bundle": str(target),
        "bundle_id": target.name,
        "manifest_sha256": sha(target / "bundle_manifest.json"),
        "executable_binding_tests": "PASS",
        "test_output": test_output.strip().splitlines()[-1],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit("NOT_READY: " + str(error))
