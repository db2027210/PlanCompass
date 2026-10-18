#!/usr/bin/env python3
"""Seal the node02 XP v10 successor offline; never launches a process."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
PARENT_ID = "168f1505fa89a80dee9fb39533300b7e1e1a4ecce6e9d60af2704072e4cb67ac"
PARENT_SHA = "02c85d1bc350fbd8a95a0e5007581fd16721402c6faec59f48ee89dd2151ca26"
PARENT = ROOT / "artifacts/mysql_xp_formal10_launch_v9" / PARENT_ID
OUT = ROOT / "artifacts/mysql_xp_node02_successor_v10"
SCHEMA = "mysql_xp_node02_successor_bundle_v10"
RUNNER = "predecessor/predecessor/tests/run_sealed_bundle_tests.py"
RUNTIME_MANIFEST = "predecessor/predecessor/manifest.json"
LANE = "authority/node02_dedicated_lane_binding_v1.json"
CONTRACT = "xp_node02_executable_launch_contract_v4.json"
TEST = "tests/test_mysql_xp_node02_successor_v10.py"


class BuildError(RuntimeError):
    pass


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def verify_parent() -> dict:
    manifest_path = PARENT / "bundle_manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise BuildError("v9 manifest absent")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (PARENT.name != PARENT_ID or manifest.get("bundle_id") != PARENT_ID
            or digest(manifest_path) != PARENT_SHA
            or sha_bytes(canonical(unsigned)) != PARENT_ID
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True
            or not isinstance(manifest.get("files"), dict)):
        raise BuildError("v9 parent identity/scope differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("v9 parent closure differs: " + relative)
    return manifest


def lane_binding() -> dict:
    return {
        "schema_version": "mysql_xp_node02_dedicated_lane_binding_v1",
        "host_id": "node02", "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "dedicated_docker_daemon_socket": "/run/ra-docker-mysql-xp-v2.sock",
        "storage": {"block_device": "/dev/nvme0n1p1", "major_minor": "259:4",
                    "dedicated_to_mysql_xp": True},
        "database_cpuset": "21-33,57-69", "client_cpuset": "3-11,39-47",
        "cpuset_overlap": False,
        "formal_envelope": {"database_cpu_quota_cores": 5.3,
            "database_io_read_bps": 29360128, "database_io_write_bps": 524288,
            "graceful_quiescence_seconds": 120},
        "capture_only_envelope": {"io_bps_per_direction": 1205862400,
            "capture_is_r0": False, "accepted_round_credit": 0},
        "algorithm_c": {"active_parameter_count": 7,
            "required_nonempty_subsets": 127, "fixed_real_replay_top_k": 5,
            "required_trace_transactions": ["AT1", "AT2", "AT3", "AT4", "AT5", "AT6"],
            "sealed_registry_required_before_fresh_r0": True,
            "qps_resource_input_prohibited": True},
        "formal_launch_authorized": False, "accepted_round_authorized": False,
    }


def launch_contract() -> dict:
    lane = lane_binding()
    return {"schema_version": "mysql_xp_node02_executable_launch_contract_v4",
        "host_id": "node02", "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True, "dedicated_lane": lane,
        "formal_envelope": lane["formal_envelope"],
        "capture_only_envelope": lane["capture_only_envelope"],
        "algorithm_c": lane["algorithm_c"],
        "formal_launch_requires": ["AT1_AT6_real_trace_registry_sealed",
            "fresh_formal_R0", "127_candidates_and_fixed_global_Top5_real_replay",
            "main_agent_live_noninterference_readback"],
        "formal_launch_authorized": False, "accepted_round_authorized": False}


def patched_runner(payload: bytes) -> bytes:
    source = payload.decode("utf-8")
    old = 'manifest.get("bundle_id") != BUNDLE.name'
    new = '(not isinstance(manifest.get("bundle_id"), str) or len(manifest.get("bundle_id")) != 64)'
    if source.count(old) != 1:
        raise BuildError("v9 nested test runner patch cardinality differs")
    return source.replace(old, new).encode("utf-8")


def patched_runtime_manifest(payload: bytes, runner: bytes) -> bytes:
    value = json.loads(payload.decode("utf-8")); files = value.get("payload_files")
    if not isinstance(files, dict) or "tests/run_sealed_bundle_tests.py" not in files:
        raise BuildError("nested runtime manifest lacks test runner")
    files["tests/run_sealed_bundle_tests.py"] = sha_bytes(runner)
    return canonical(value)


def test_source() -> bytes:
    return b'''from __future__ import annotations\nimport hashlib,json\nfrom pathlib import Path\nROOT=Path(__file__).resolve().parents[1]\ndef test_node02_v10_closure_and_lane():\n m=json.loads((ROOT/"bundle_manifest.json").read_text()); assert m["bundle_id"]==ROOT.name\n assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False\n for r,h in m["files"].items(): assert hashlib.sha256((ROOT/r).read_bytes()).hexdigest()==h\n lane=json.loads((ROOT/"authority/node02_dedicated_lane_binding_v1.json").read_text())\n assert lane["dedicated_docker_daemon_socket"]=="/run/ra-docker-mysql-xp-v2.sock"\n assert lane["storage"]["major_minor"]=="259:4"\n assert lane["database_cpuset"]=="21-33,57-69" and lane["client_cpuset"]=="3-11,39-47"\n assert lane["formal_envelope"]=={"database_cpu_quota_cores":5.3,"database_io_read_bps":29360128,"database_io_write_bps":524288,"graceful_quiescence_seconds":120}\n assert lane["capture_only_envelope"]["io_bps_per_direction"]==1205862400\n assert lane["algorithm_c"]["required_nonempty_subsets"]==127\n runner=(ROOT/"predecessor/predecessor/tests/run_sealed_bundle_tests.py").read_text(); assert 'manifest.get("bundle_id") != BUNDLE.name' not in runner\n'''


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink(): raise BuildError("symlink prohibited")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def verify_bundle(path: Path) -> dict:
    manifest_path = path / "bundle_manifest.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    actual = {}
    for item in path.rglob("*"):
        if item.is_symlink(): raise BuildError("symlink in successor")
        if item.is_file():
            relative = item.relative_to(path).as_posix()
            if relative not in {"bundle_manifest.json", "bundle_manifest.json.sha256"}:
                actual[relative] = digest(item)
    if (manifest.get("bundle_id") != path.name or sha_bytes(canonical(unsigned)) != path.name
            or manifest.get("schema_version") != SCHEMA or actual != manifest.get("files")
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False):
        raise BuildError("v10 successor identity differs")
    lane = json.loads((path / LANE).read_text(encoding="utf-8"))
    if (lane.get("dedicated_docker_daemon_socket") != "/run/ra-docker-mysql-xp-v2.sock"
            or lane.get("storage", {}).get("major_minor") != "259:4"
            or lane.get("database_cpuset") != "21-33,57-69"
            or lane.get("client_cpuset") != "3-11,39-47"):
        raise BuildError("node02 lane binding differs")
    return manifest


def main() -> int:
    parent = verify_parent()
    payload = {relative: (PARENT / relative).read_bytes() for relative in parent["files"]}
    runner = patched_runner(payload[RUNNER]); payload[RUNNER] = runner
    payload[RUNTIME_MANIFEST] = patched_runtime_manifest(payload[RUNTIME_MANIFEST], runner)
    payload[LANE] = canonical(lane_binding()); payload[CONTRACT] = canonical(launch_contract()); payload[TEST] = test_source()
    identity = {key: value for key, value in parent.items() if key not in {"bundle_id", "files"}}
    identity.update({"schema_version": SCHEMA, "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA, "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True, "node02_lane": lane_binding(),
        "formal_launch_authorized": False, "accepted_round_authorized": False,
        "offline_build_only": True, "remote_actions": 0, "builder_revision": 2,
        "files": {key: sha_bytes(value) for key, value in sorted(payload.items())}})
    bundle_id = sha_bytes(canonical(identity)); manifest = {**identity, "bundle_id": bundle_id}
    OUT.mkdir(parents=True, exist_ok=True); target = OUT / bundle_id
    if target.exists() or target.is_symlink():
        freeze(target); verify_bundle(target)
    else:
        stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6)); stage.mkdir(mode=0o700)
        try:
            for relative, value in payload.items():
                destination = stage / relative; destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(value); os.chmod(destination, 0o444)
            raw = canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw); (stage / "bundle_manifest.json.sha256").write_text(sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii")
            freeze(stage); os.rename(stage, target)
        except BaseException:
            if stage.exists(): shutil.rmtree(stage)
            raise
        verify_bundle(target)
    print(json.dumps({"status":"SEALED_OFFLINE_NODE02_SUCCESSOR_NOT_LAUNCHED", "bundle":str(target), "bundle_id":target.name, "bundle_manifest_sha256":digest(target / "bundle_manifest.json"), "formal_launch_authorized":False, "accepted_round_authorized":False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\\n"); raise SystemExit(2)
