#!/usr/bin/env python3
"""Seal the JOB v13 MySQL-only node04/NVMe relocation successor.

The builder is offline and fail-closed.  It changes no host state, publishes
only a new content-addressed local directory, and leaves every launch flag
false.  The live major:minor is supplied only after a same-host read-only disk
snapshot so an old device number cannot be reused accidentally.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "01e6d81e5e59ca28eaa5497286fbb1cf23d8d434de6077bf6f12119ca81d986b"
PARENT_SHA = "b6f34b74a67a33cfa61543dd365ea7e91cc3022e9b8d525d12c11bfe788936f6"
PARENT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v12" / PARENT_ID
OUT = ROOT / "artifacts/mysql_job_node04_relocation_successor_v13"
SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v13"
OLD_CONTRACT_SHA = "52ba2faa2035dfa9035cb0f6ab75d4461430000097f846c3e94e4a81f99aea58"
CONTRACT = "authority/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json"
RUNTIME = "distributed_runtime_contract.json"
OLD_MOUNT = "/mnt/mysql_nvme1n1"
NEW_MOUNT = "/mnt/mysql_job_nvme1n1"
OLD_PROJECT = OLD_MOUNT + "/anonymous/resource_adaptive_mysql/job"
NEW_PROJECT = NEW_MOUNT + "/anonymous/resource_adaptive_mysql/job"
DB_CPUS = "25-26,61-62"
CLIENT_CPUS = "27-29,63-65"
DAEMON_CPUS = "30-31,66-67"
INVALIDATION = "authority/mysql_job_v12_node02_location_invalidation_20260802.json"
TEST = "tests/test_mysql_job_node04_relocation_v13.py"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    return sha_bytes(Path(path).read_bytes())


def replace_exact(source, old, new, label, count=None):
    actual = source.count(old)
    if actual == 0 or (count is not None and actual != count):
        raise BuildError("%s patch cardinality differs: %d" % (label, actual))
    return source.replace(old, new)


def verify_parent():
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID
            or digest(manifest_path) != PARENT_SHA
            or sha_bytes(canonical(unsigned)) != PARENT_ID
            or manifest.get("schema_version")
            != "mysql_job_raw_all_subset_three_level_successor_bundle_v12"):
        raise BuildError("JOB v12 parent identity differs")
    for relative, expected in manifest.get("files", {}).items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("JOB v12 parent closure differs: " + relative)
    return manifest


def patch_runtime(payload, major_minor):
    value = json.loads(payload.decode("utf-8"))
    value["host"] = {"hostname": "node04", "address": "192.0.2.7"}
    value["bundle_remote_path_rule"] = NEW_PROJECT + "/runtime_algorithm_c_successor_20260729/bundles/<bundle_id>"
    value["storage"].update({
        "device": "/dev/nvme1n1",
        "expected_live_major_minor": major_minor,
        "mount": NEW_MOUNT,
        "runtime_root": NEW_PROJECT + "/runtime_algorithm_c_successor_20260729",
        "template_path": NEW_PROJECT + "/template",
    })
    value["resource_envelope"].update({"cpuset_cpus": DB_CPUS, "cpuset_mems": "1"})
    value["client_resource_envelope"].update({"cpuset_cpus": CLIENT_CPUS, "cpuset_mems": "1"})
    runtime_root = value["storage"]["runtime_root"]
    py_root = runtime_root + "/runtime/python311-" + value["toolchain"]["python_tree_content_sha256"]
    value["toolchain"]["python_runtime_root"] = py_root
    value["toolchain"]["python_runtime"] = py_root + "/bin/python3.11"
    return canonical(value)


def patch_contract(payload, major_minor):
    value = json.loads(payload.decode("utf-8"))
    job = value["scenarios"]["job"]
    job["host"] = {"hostname": "node04", "address": "192.0.2.7"}
    job["storage"].update({
        "device": "/dev/nvme1n1",
        "expected_live_major_minor": major_minor,
        "mount": NEW_MOUNT,
        "runtime_root": NEW_PROJECT + "/runtime_algorithm_c_successor_20260729",
        "template_path": NEW_PROJECT + "/template",
    })
    job["resource_envelope"].update({"cpuset_cpus": DB_CPUS, "cpuset_mems": "1"})
    job["client_resource_envelope"].update({"cpuset_cpus": CLIENT_CPUS, "cpuset_mems": "1"})
    policy = value["same_host_parallel_policy"]
    retained = [row for row in policy["pairs"] if "job" not in row.get("scenarios", [])]
    policy["pairs"] = [
        {"host": "node04", "scenarios": ["job", "tpch"],
         "serial_fallback_lock": NEW_PROJECT + "/host_control/job_tpch_serial_fallback.lock"},
        {"host": "node04", "scenarios": ["job", "tpcc"],
         "serial_fallback_lock": NEW_PROJECT + "/host_control/job_tpcc_serial_fallback.lock"},
    ] + retained
    value["status"] = "OFFLINE_CONTRACT_ONLY_JOB_RELOCATED_TO_NODE04_NOT_LAUNCH_AUTHORITY"
    return canonical(value)


def patch_text(payload, replacements, label):
    source = payload.decode("utf-8")
    for old, new in replacements:
        source = replace_exact(source, old, new, label + ": " + old)
    return source.encode("utf-8")


def disable_legacy_manual_campaign(payload):
    source = payload.decode("utf-8")
    old = '''if __name__ == "__main__":
    raise SystemExit(main())
'''
    new = '''if __name__ == "__main__":
    raise SystemExit(
        "NOT_READY: legacy multi-host manual campaign is provenance only; "
        "use a successor authority bound to distributed_runtime_contract.json"
    )
'''
    return replace_exact(source, old, new, "disable legacy manual campaign", 1).encode("utf-8")


def patch_schema(payload):
    return payload.decode("utf-8").replace(
        "mysql_job_raw_all_subset_three_level_successor_bundle_v12", SCHEMA
    ).replace("JOB v12 successor", "JOB v13 successor").encode("utf-8")


TEST_SOURCE = r'''from __future__ import annotations
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def _cpus(value):
 out=set()
 for part in value.split(","):
  a,*b=part.split("-"); out.update(range(int(a),int(b[0])+1) if b else [int(a)])
 return out
def test_v13_node04_relocation_is_exact_and_fail_closed():
 m=json.loads((ROOT/"bundle_manifest.json").read_text()); assert m["bundle_id"]==ROOT.name
 assert m["schema_version"]=="mysql_job_raw_all_subset_three_level_successor_bundle_v13"
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
 for r,h in m["files"].items(): assert hashlib.sha256((ROOT/r).read_bytes()).hexdigest()==h
 d=json.loads((ROOT/"distributed_runtime_contract.json").read_text())
 assert d["formal_scope"]=="mysql_only" and d["cross_engine_comparison_prohibited"] is True
 assert d["host"]=={"address":"192.0.2.7","hostname":"node04"}
 assert d["storage"]["device"]=="/dev/nvme1n1"
 assert re.fullmatch(r"[0-9]+:[0-9]+",d["storage"]["expected_live_major_minor"])
 assert d["storage"]["mount"]=="/mnt/mysql_job_nvme1n1"
 sets=[_cpus(d["resource_envelope"]["cpuset_cpus"]),_cpus(d["client_resource_envelope"]["cpuset_cpus"]),_cpus("30-31,66-67")]
 assert sets==[_cpus("25-26,61-62"),_cpus("27-29,63-65"),_cpus("30-31,66-67")]
 assert all(a.isdisjoint(b) for i,a in enumerate(sets) for b in sets[i+1:])
 a=json.loads((ROOT/"authority/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json").read_text())
 assert a["algorithm_invariants"]["gradient_formula"]=="g_j = sum_r(dScore/dU_r * T[j,r] * confidence)"
 assert a["algorithm_invariants"]["enumerate_all_nonempty_subspaces"] is True
 assert a["algorithm_invariants"]["fixed_top_k_replay"]==5
 assert a["scenarios"]["job"]["host"]["hostname"]=="node04"
 pairs={tuple(x["scenarios"]) for x in a["same_host_parallel_policy"]["pairs"]}
 assert ("job","tpch") in pairs and ("job","tpcc") in pairs
 active=("distributed_runtime_contract.json","qualification/mysql_job_distributed_v2_adopted_services.py","qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py","qualification/mysql_job_distributed_live_readback_qualification_v2.py","qualification/mysql_job_distributed_v2_rolling_monitor.py","qualification/mysql_job_dedicated_daemon_contract_v1.py","qualification/mysql_job_dedicated_daemon_materializer_v5.py","controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py","controller/render_mysql_job_v32_qualification_solo_tmpfs_v2.py")
 for r in active:
  s=(ROOT/r).read_text(); assert "node02" not in s and "/mnt/mysql_nvme1n1" not in s and "259:3" not in s
'''.encode("utf-8")


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in JOB v13")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-live-major-minor", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[0-9]+:[0-9]+", args.expected_live_major_minor) is None:
        raise BuildError("live major:minor differs")
    parent = verify_parent()
    payload = {relative: (PARENT / relative).read_bytes()
               for relative in parent["files"] if relative != "closure.json"}
    modes = {relative: stat.S_IMODE((PARENT / relative).stat().st_mode)
             for relative in payload}

    payload[RUNTIME] = patch_runtime(payload[RUNTIME], args.expected_live_major_minor)
    payload[CONTRACT] = patch_contract(payload[CONTRACT], args.expected_live_major_minor)
    contract_sha = sha_bytes(payload[CONTRACT])

    common = [
        ("/mnt/mysql_nvme1n1", NEW_MOUNT),
        ("259:3", args.expected_live_major_minor),
        ("node02", "node04"),
    ]
    simple = (
        "qualification/mysql_job_distributed_live_readback_qualification_v2.py",
        "qualification/mysql_job_distributed_v2_rolling_monitor.py",
        "qualification/mysql_job_dedicated_daemon_materializer_v2.py",
        "qualification/mysql_job_dedicated_daemon_materializer_v5.py",
        "controller/render_mysql_job_v32_qualification_solo_tmpfs_v2.py",
        "qualification/mysql_sql_debug_metric_adapter_v1.py",
    )
    for relative in simple:
        applicable = [(old, new) for old, new in common if old in payload[relative].decode("utf-8")]
        payload[relative] = patch_text(payload[relative], applicable, relative)

    adopted = [
        ("anonymous@192.0.2.4", "anonymous@192.0.2.7"),
        ("node02", "node04"), ("/mnt/mysql_nvme1n1", NEW_MOUNT),
        ("259:3", args.expected_live_major_minor),
        ('CPUS = "18-20,54-56"', 'CPUS = "' + DB_CPUS + '"'),
        ('CLIENT_CPUS = "0-2,36-38"', 'CLIENT_CPUS = "' + CLIENT_CPUS + '"'),
    ]
    payload["qualification/mysql_job_distributed_v2_adopted_services.py"] = patch_text(
        payload["qualification/mysql_job_distributed_v2_adopted_services.py"], adopted, "adopted services")

    authority_changes = common + [
        ('"cpuset_cpus": "18-20,54-56"', '"cpuset_cpus": "' + DB_CPUS + '"'),
        ('"cpuset_cpus": "0-2,36-38"', '"cpuset_cpus": "' + CLIENT_CPUS + '"'),
        (OLD_CONTRACT_SHA, contract_sha),
    ]
    payload["qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py"] = patch_text(
        payload["qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py"], authority_changes, "bootstrap authority")

    for relative in ("qualification/mysql_job_dedicated_daemon_contract_v1.py",
                     "authority/mysql_job_dedicated_daemon_contract_v1.py"):
        payload[relative] = patch_text(payload[relative], [
            ("/mnt/mysql_nvme1n1", NEW_MOUNT),
            ('DEVICE="259:3"', 'DEVICE="' + args.expected_live_major_minor + '"'),
            ('CPUS="34-35,70-71"', 'CPUS="' + DAEMON_CPUS + '"'),
        ], relative)

    fixture = json.loads(payload["qualification/fixtures/mysql_job_dedicated_daemon_readback_fixture_v1.json"].decode("utf-8"))
    def fixture_replace(value):
        if isinstance(value, str):
            return value.replace(OLD_MOUNT, NEW_MOUNT).replace("259:3", args.expected_live_major_minor).replace("34-35,70-71", DAEMON_CPUS)
        if isinstance(value, list): return [fixture_replace(x) for x in value]
        if isinstance(value, dict): return {fixture_replace(k): fixture_replace(v) for k,v in value.items()}
        return value
    payload["qualification/fixtures/mysql_job_dedicated_daemon_readback_fixture_v1.json"] = canonical(fixture_replace(fixture))

    bootstrap = [
        ("/mnt/mysql_nvme1n1", NEW_MOUNT), ("node02", "node04"),
        (OLD_CONTRACT_SHA, contract_sha),
    ]
    payload["controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py"] = patch_text(
        payload["controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py"], bootstrap, "bootstrap renderer")
    payload["qualification/shell/manual_mysql_sgd_r0_campaign.py"] = disable_legacy_manual_campaign(
        payload["qualification/shell/manual_mysql_sgd_r0_campaign.py"])

    schema_files = list(parent["files"])
    for relative in schema_files:
        if relative.startswith("tests/") and relative != TEST:
            payload[relative] = patch_schema(payload[relative])
    payload["qualification/mysql_job_distributed_live_readback_qualification_v2.py"] = patch_schema(
        payload["qualification/mysql_job_distributed_live_readback_qualification_v2.py"])
    payload["qualification/mysql_job_dedicated_daemon_materializer_v5.py"] = patch_schema(
        payload["qualification/mysql_job_dedicated_daemon_materializer_v5.py"]
    ).replace(b"mysql_job_dedicated_daemon_materials_v12", b"mysql_job_dedicated_daemon_materials_v13")

    payload[INVALIDATION] = canonical({
        "schema_version": "mysql_job_node02_location_invalidation_v1",
        "status": "INVALID_ZERO_CREDIT_RELOCATION_REQUIRED",
        "predecessor_bundle_id": PARENT_ID,
        "reason": "node02 explicitly excluded by main-agent instruction",
        "replacement_host": "node04",
        "replacement_device": "/dev/nvme1n1",
        "replacement_expected_live_major_minor": args.expected_live_major_minor,
        "capture_credit": 0, "r0_credit": 0, "candidate_credit": 0,
        "top5_replay_credit": 0, "accepted_round_credit": 0,
        "postgresql_mutation": False,
    })
    payload[TEST] = TEST_SOURCE
    for relative in payload:
        modes.setdefault(relative, 0o444)
    modes[TEST] = 0o444
    modes[INVALIDATION] = 0o444

    closure = json.loads((PARENT / "closure.json").read_text(encoding="utf-8"))
    closure.update({
        "schema_version": "mysql_job_module_closure_v13",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_bundle_manifest_sha256": PARENT_SHA,
        "contract_sha256": contract_sha,
        "relocation": {"hostname": "node04", "address": "192.0.2.7",
                       "device": "/dev/nvme1n1", "major_minor": args.expected_live_major_minor,
                       "mount": NEW_MOUNT, "database_cpus": DB_CPUS,
                       "client_cpus": CLIENT_CPUS, "daemon_cpus": DAEMON_CPUS},
    })
    closure["files"] = {relative: sha_bytes(data) for relative, data in sorted(payload.items())}
    payload["closure.json"] = canonical(closure)
    modes["closure.json"] = 0o444

    identity = dict(parent)
    identity.pop("bundle_id", None)
    identity.update({
        "schema_version": SCHEMA,
        "status": "SEALED_NODE04_NVME_RELOCATION_SUCCESSOR_DEBUG_BOOTSTRAP_NOT_R0_OR_FORMAL_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_bundle_manifest_sha256": PARENT_SHA,
        "distributed_v2_contract_sha256": contract_sha,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_launch_authorized": False,
        "formal10_authorized": False,
        "accepted_round_authorized": False,
        "legacy_manual_multi_host_campaign_launch_prohibited": True,
        "relocation": closure["relocation"],
        "files": {relative: sha_bytes(data) for relative, data in sorted(payload.items())},
    })
    identity["dedicated_daemon_contract"].update({
        "daemon_root": NEW_MOUNT + "/anonymous/resource_adaptive_mysql/docker_ra_job_v1",
        "device_major_minor": args.expected_live_major_minor,
        "daemon_cpuset_cpus": DAEMON_CPUS,
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / bundle_id
    if target.exists() or target.is_symlink():
        raise BuildError("immutable JOB v13 already exists")
    stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
    stage.mkdir(mode=0o700)
    try:
        for relative, data in payload.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            os.chmod(path, modes[relative] & ~0o222)
        raw = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii")
        freeze(stage)
        os.rename(stage, target)
    except BaseException:
        if stage.exists(): shutil.rmtree(stage)
        raise
    print(json.dumps({"status": manifest["status"], "bundle": str(target),
                      "bundle_id": bundle_id,
                      "manifest_sha256": digest(target / "bundle_manifest.json"),
                      "formal_launch_authorized": False,
                      "accepted_round_authorized": False}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
