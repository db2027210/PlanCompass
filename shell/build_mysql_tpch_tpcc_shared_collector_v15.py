#!/usr/bin/env python3
"""Build immutable v15 with signed paired-collector control-tree attribution."""
from __future__ import annotations

import hashlib
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
PARENT_ID = "a0674d4e62f2c9991ca959ea52a69763b1154547631e0996bbbfb9712d0dc954"
PARENT_SHA = "7561202259805c02af0322283ab26af7b0e2695d281834a4e4285e2222d997e1"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v14" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v15"
SCHEMA = "mysql_tpch_tpcc_shared_collector_bundle_v15"
ENTRY = "qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v15.py"
INVALIDATION = "authority/v14a_peer_control_tree_invalidation.json"
TEST = "tests/test_shared_collector_peer_control_tree_v15.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def replace_exact(data, old, new, label):
    if data.count(old) != 1:
        raise BuildError(label + " patch cardinality differs")
    return data.replace(old, new)


def verified_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID or digest(path) != PARENT_SHA
            or sha(canonical(unsigned)) != PARENT_ID
            or manifest.get("schema_version") != "mysql_tpch_tpcc_shared_collector_bundle_v14"
            or manifest.get("proc_fd_exit_enoent_classification") != "race_retry_requires_final_zero_races"
            or manifest.get("permission_errors_remain_fail_closed") is not True
            or manifest.get("minimum_samples") != 181
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True
            or manifest.get("postgresql_mutation_prohibited") is not True
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False):
        raise BuildError("v14 parent identity/scope differs")
    for relative, expected in manifest["files"].items():
        item = PARENT / relative
        if item.is_symlink() or not item.is_file() or digest(item) != expected:
            raise BuildError("v14 parent closure differs: " + relative)
    return manifest


def peer_code():
    return r'''
def _exact_option(argv, name):
    indexes = [index for index, value in enumerate(argv) if value == name]
    if len(indexes) != 1 or indexes[0] + 1 >= len(argv):
        raise core.QualificationError("collector control option differs: " + name)
    return argv[indexes[0] + 1]


def _proc_argv_and_hash(pid, expected_hash):
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except (OSError, PermissionError) as exc:
        raise core.QualificationError("collector control cmdline unreadable: %s" % exc)
    if core.sha_bytes(raw) != expected_hash:
        raise core.QualificationError("collector control cmdline identity changed")
    argv = [value.decode("utf-8", errors="strict") for value in raw.split(b"\0") if value]
    return argv


def _sealed_collector_command(argv):
    entry = str(Path(__file__).resolve())
    if (len(argv) < 3 or Path(argv[0]).resolve() != Path(sys.executable).resolve()
            or Path(argv[1]).resolve() != Path(entry)
            or argv.count("--qualification-paired") != 1):
        raise core.QualificationError("paired collector executable identity differs")
    command_path = Path(_exact_option(argv, "--command-spec"))
    authority_path = Path(_exact_option(argv, "--main-agent-authority"))
    token_path = Path(_exact_option(argv, "--main-agent-token-file"))
    command = core.load_object(command_path, "paired collector command")
    authority = core.load_object(authority_path, "paired collector authority")
    token_state = token_path.stat(follow_symlinks=False)
    token = token_path.read_text(encoding="ascii").strip()
    manifest_path = HERE.parent / "bundle_manifest.json"
    manifest = core.load_object(manifest_path, "collector manifest")
    now = time.time()
    flags = ("capture_authorized", "formal_r0_authorized", "formal_authorized", "accepted_round_authorized")
    if (not core.valid_sidecar(command_path) or not core.valid_sidecar(authority_path)
            or token_path.is_symlink() or not token_path.is_file()
            or stat.S_IMODE(token_state.st_mode) != 0o600 or token_state.st_uid != 0
            or len(token) < 48 or any(character.isspace() for character in token)
            or command.get("mode") != "qualification_paired" or command.get("debug_only") is not True
            or any(command.get(key) is not False for key in flags)
            or authority.get("authorized_by") != "main_agent" or authority.get("debug_only") is not True
            or any(authority.get(key) is not False for key in flags)
            or authority.get("qualification_attempt_id") != command.get("qualification_attempt_id")
            or authority.get("command_spec_sha256") != core.sha256(command_path)
            or authority.get("main_agent_token_sha256") != core.sha_bytes(token.encode("ascii"))
            or authority.get("collector_bundle_id") != HERE.parent.name
            or authority.get("collector_manifest_sha256") != core.sha256(manifest_path)
            or manifest.get("bundle_id") != HERE.parent.name
            or not float(authority.get("not_before_epoch") or 0) <= now <= float(authority.get("expires_epoch") or 0)):
        raise core.QualificationError("paired collector signed authority differs")
    return command


def _classify_verified_collector_trees(result, mysql_cpuset, workload_cpuset):
    own_pid = os.getpid()
    rows = result.get("processes") or []
    by_pid = {int(row.get("pid") or 0): row for row in rows}
    own_row = by_pid.get(own_pid)
    if own_row is None:
        raise core.QualificationError("own collector control row is absent")
    own_argv = _proc_argv_and_hash(own_pid, own_row.get("cmdline_sha256"))
    own_command = _sealed_collector_command(own_argv)
    own_target = _exact_option(own_argv, "--container")
    own_peer = _exact_option(own_argv, "--peer-container")
    peer_attempt = (own_command.get("paired_peer_workload") or {}).get("qualification_attempt_id")
    candidates = []
    for row in rows:
        pid = int(row.get("pid") or 0)
        if (pid == own_pid or int(row.get("ppid") or 0) != 1
                or row.get("container_identity") is not None
                or row.get("comm") != "python3.11"):
            continue
        try:
            argv = _proc_argv_and_hash(pid, row.get("cmdline_sha256"))
        except (core.QualificationError, UnicodeError):
            continue
        if len(argv) < 2 or Path(argv[1]).resolve() != Path(__file__).resolve() or "--qualification-paired" not in argv:
            continue
        command = _sealed_collector_command(argv)
        if (_exact_option(argv, "--container") != own_peer
                or _exact_option(argv, "--peer-container") != own_target
                or command.get("qualification_attempt_id") != peer_attempt
                or (command.get("paired_peer_workload") or {}).get("qualification_attempt_id") != own_command.get("qualification_attempt_id")):
            raise core.QualificationError("paired collector reciprocal binding differs")
        candidates.append((row, command))
    if len(candidates) != 1:
        raise core.QualificationError("paired collector root cardinality differs")
    peer_row, peer_command = candidates[0]
    own_cpus = core.expand_cpuset((own_command.get("workload_process_isolation") or {}).get("cpuset_cpus", ""))
    peer_cpus = core.expand_cpuset((peer_command.get("workload_process_isolation") or {}).get("cpuset_cpus", ""))
    if (own_cpus != core.expand_cpuset(workload_cpuset) or not peer_cpus
            or not peer_cpus.isdisjoint(core.expand_cpuset(mysql_cpuset) | own_cpus)):
        raise core.QualificationError("paired collector CPU isolation binding differs")
    def descendants(root_pid):
        closure = {root_pid}; changed = True
        while changed:
            changed = False
            for row in rows:
                pid = int(row.get("pid") or 0)
                if int(row.get("ppid") or 0) in closure and pid not in closure:
                    closure.add(pid); changed = True
        return closure
    for root_pid, expected_cpus, root_role in ((own_pid, own_cpus, "qualification_control_plane"), (int(peer_row["pid"]), peer_cpus, "qualification_peer_control_plane")):
        for pid in descendants(root_pid):
            row = by_pid.get(pid)
            if row is None:
                continue
            if (row.get("container_identity") is not None
                    or core.expand_cpuset(row.get("cpus_allowed_list", "")) != expected_cpus
                    or int(row.get("declared_device_writable_fds") or 0) != 0):
                raise core.QualificationError("collector control descendant isolation differs")
            row["role"] = root_role if pid == root_pid else "qualification_workload"
    result["paired_peer_control_plane_root_pid"] = int(peer_row["pid"])
    result["paired_peer_control_plane_attempt_id"] = peer_attempt
    result["paired_peer_control_tree_exact_authority_verified"] = True
'''.encode("utf-8")


def test_source():
    return b'''from __future__ import annotations
import hashlib,json,py_compile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_v15_binds_only_reciprocal_signed_peer_control_tree():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v15"
 assert m["paired_peer_control_tree_policy"]=="reciprocal_attempt_authority_hash_cpu_and_parentage_exact"
 assert m["unknown_process_change_remains_fail_closed"] is True
 assert m["proc_snapshot_acceptance_requires_zero_races"] is True
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
 for relative,expected in m["files"].items():assert hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()==expected
 entry=(ROOT/"qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py").read_text()
 for text in ("paired collector reciprocal binding differs","paired collector signed authority differs","collector control descendant isolation differs","qualification_peer_control_plane","_classify_verified_collector_trees(result, args[4], args[5])"):assert text in entry
 core=(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()
 assert 'conflicts.append("process_fd_or_io_permission_failure")' in core
 assert '"accepted_snapshot_requires_zero_races": True' in entry
 py_compile.compile(str(ROOT/"qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py"),doraise=True,cfile=str(Path("/tmp")/(ROOT.name+"-v15-entry.pyc")))
'''


def invalidation():
    return {"schema_version":"mysql_shared_peer_control_tree_invalidation_v1","status":"INVALIDATED_ZERO_CREDIT_REPLACED_BY_V15","invalidated_collector_bundle_id":PARENT_ID,"failed_attempts":["tpch-a-paired-v14a-20260801t194000p08-6c3809b8","tpcc-b-paired-v14a-20260801t194000p08-7e2e13aa"],"observed_complete_samples":{"tpch":181,"tpcc":181},"failure":"verified_peer_collector_root_helper_children_misclassified_as_unknown_co_resident","foreign_writer_count":0,"repair":"reciprocal_signed_peer_control_tree_attribution","unknown_process_change_remains_fail_closed":True,"qualification_credit":0,"r0_credit":0,"accepted_rounds_credit":0,"postgresql_mutation":False}


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink(): raise BuildError("symlink in v15")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def remove_caches(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir() and path.name in {"__pycache__", ".pytest_cache"}: shutil.rmtree(path)
        elif path.is_file() and path.suffix in {".pyc", ".pyo"}: path.unlink()


def file_map(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink(): raise BuildError("symlink in v15 closure")
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in {"bundle_manifest.json","bundle_manifest.json.sha256"}: result[relative] = digest(path)
    return result


def main():
    parent = verified_parent()
    payload = {r:(PARENT/r).read_bytes() for r in parent["files"]}
    modes = {r:stat.S_IMODE((PARENT/r).stat().st_mode) for r in parent["files"]}
    entry = payload[ENTRY]
    entry = replace_exact(entry, b"\ndef shared_rolling_process_inventory(*args, **kwargs):\n", b"\n" + peer_code() + b"\ndef shared_rolling_process_inventory(*args, **kwargs):\n", "peer helper insertion")
    marker = b'''    result["inventory_snapshot_retry_policy"] = {\n        "schema_version": "mysql_shared_proc_snapshot_retry_policy_v1",\n        "maximum_complete_snapshot_attempts": 16,\n        "retry_backoff_milliseconds": [10, 20, 30, 40],\n        "race_rows_ignored": False,\n        "accepted_snapshot_requires_zero_races": True,\n    }\n'''
    entry = replace_exact(entry, marker, marker + b"    _classify_verified_collector_trees(result, args[4], args[5])\n", "peer classification call")
    payload[ENTRY] = entry
    for relative, data in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = data.replace(b"mysql_tpch_tpcc_shared_collector_bundle_v14", SCHEMA.encode("ascii"))
    payload[BUILDER]=SOURCE.read_bytes(); modes[BUILDER]=0o444
    payload[INVALIDATION]=canonical(invalidation()); modes[INVALIDATION]=0o444
    payload[TEST]=test_source(); modes[TEST]=0o444
    unsigned={k:v for k,v in parent.items() if k not in {"bundle_id","files"}}
    unsigned.update({"schema_version":SCHEMA,"status":"SEALED_SIGNED_PEER_CONTROL_TREE_NOT_LAUNCH_AUTHORITY","predecessor_bundle_id":PARENT_ID,"predecessor_manifest_sha256":PARENT_SHA,"paired_peer_control_tree_policy":"reciprocal_attempt_authority_hash_cpu_and_parentage_exact","unknown_process_change_remains_fail_closed":True,"resource_workload_sample_gates_algorithm_c_unchanged":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"files":{r:sha(d) for r,d in sorted(payload.items())}})
    bundle_id=sha(canonical(unsigned)); manifest=dict(unsigned,bundle_id=bundle_id)
    OUT.mkdir(parents=True,exist_ok=True); destination=OUT/bundle_id; test_result="existing immutable bundle reverified"
    if not destination.exists():
        stage=OUT/(".stage-"+bundle_id+"-"+secrets.token_hex(6)); stage.mkdir(mode=0o700)
        try:
            for relative,data in payload.items():
                path=stage/relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data); os.chmod(path,modes.get(relative,0o444)&~0o222)
            raw=canonical(manifest); (stage/"bundle_manifest.json").write_bytes(raw); (stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n",encoding="ascii")
            scratch_parent=Path(tempfile.mkdtemp(prefix="mysql-shared-v15-test-")); scratch=scratch_parent/bundle_id
            try:
                shutil.copytree(stage,scratch); tests=sorted(str(p) for p in (scratch/"tests").glob("test_*.py")); result=subprocess.run([str(PYTHON),"-m","pytest","-q",*tests],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180,check=False)
                if result.returncode: raise BuildError("v15 tests failed:\n"+result.stdout[-16000:])
                test_result=result.stdout.strip()
            finally: shutil.rmtree(scratch_parent)
            remove_caches(stage); freeze(stage); os.rename(stage,destination)
        finally:
            if stage.exists(): shutil.rmtree(stage)
    published=json.loads((destination/"bundle_manifest.json").read_text()); check=dict(published); check.pop("bundle_id",None)
    if published!=manifest or sha(canonical(check))!=bundle_id or file_map(destination)!=manifest["files"] or any(stat.S_IMODE(p.stat().st_mode)&0o222 for p in [destination,*destination.rglob("*")]): raise BuildError("published v15 differs")
    print(json.dumps({"status":"PASS_IMMUTABLE_SHARED_COLLECTOR_V15","bundle":str(destination),"bundle_id":bundle_id,"bundle_manifest_sha256":digest(destination/"bundle_manifest.json"),"verified_files":len(manifest["files"]),"tests":test_result,"formal_launch_authorized":False,"accepted_round_authorized":False},indent=2,sort_keys=True))


if __name__=="__main__":
    try: main()
    except (BuildError,OSError,ValueError,subprocess.SubprocessError) as exc:
        print("NOT_READY: "+str(exc),file=__import__("sys").stderr); raise SystemExit(2)
