#!/usr/bin/env python3
"""Build a fail-closed TPC-C inventory successor for the 30664 zero-credit run."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "6953a554fc57508d6685e95581935ce7eb7f0969c144265f2d1da034b9c7bf41"
PARENT_SHA = "f2dba3ad086bf60ba5a930c46410558adde46e472a0c3143424e1a70eb8c025f"
PARENT = ROOT / "artifacts/mysql_tpcc_phase_prime_support_successor_v1" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpcc_inventory_dynamic_successor_v1"
HERE = Path(__file__).resolve()
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
ENTRY = "qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode()


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def sha(path):
    return digest(Path(path).read_bytes())


def once(text, old, new, label):
    if text.count(old) != 1:
        raise BuildError(f"{label} anchor count={text.count(old)}")
    return text.replace(old, new, 1)


CORE_TREE = r'''
    # A PG query-artifact HTTP service is not a database or benchmark.  It is
    # nevertheless included (parent and descendants) so the mysql_only entry
    # can prove a versioned rolling CPU/IO non-interference window.  Inclusion
    # is not authorization: the entry re-reads exact argv/exe/cwd identity and
    # fails closed on every sample.
    pg_http_candidate_tree = {
        pid for pid, row in all_processes.items()
        if "python3 -m http.server" in row["_command"]
        and "pg5_common_outer_bgallowed_20260722_v1" in row["_command"]
    }
    changed = True
    while changed:
        changed = False
        for pid, row in all_processes.items():
            if row["ppid"] in pg_http_candidate_tree and pid not in pg_http_candidate_tree:
                pg_http_candidate_tree.add(pid)
                changed = True
'''


ENTRY_SUCCESSOR = r'''

# mysql_only rolling authority for the exact PG query-artifact HTTP helper.
# These limits are deliberately strict and versioned.  They do not authorize
# PostgreSQL mutation, database overlap, benchmark overlap, or target-device IO.
PG_HTTP_POLICY_VERSION = "mysql_only_pg_artifact_http_dynamic_noninterference_v1"
PG_HTTP_ROOT = Path("/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpch_node04/tmp")
PG_HTTP_BIND = "192.0.2.7"
PG_HTTP_PORT = "49218"
PG_HTTP_MAX_ADJACENT_CPU_TICKS = 2
PG_HTTP_MAX_ADJACENT_READ_BYTES = 0
PG_HTTP_MAX_ADJACENT_WRITE_BYTES = 0
_pg_http_previous = {}
_root_inventory_cache = {}


def _proc_cpu_ticks(pid, expected_starttime):
    raw = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
    boundary = raw.rfind(") ")
    if boundary < 0:
        raise core.QualificationError("PG HTTP process stat differs")
    fields = raw[boundary + 2:].split()
    if int(fields[19]) != int(expected_starttime):
        raise core.QualificationError("PG HTTP process start identity differs")
    return int(fields[11]) + int(fields[12])


def _exact_pg_http_rows(result):
    rows = result.get("processes") or []
    candidates = []
    for row in rows:
        argv = _proc_argv_and_hash(int(row.get("pid") or 0), row.get("cmdline_sha256"))
        if "http.server" in argv or any("pg5_common_outer_bgallowed_20260722_v1" in value for value in argv):
            candidates.append((row, argv))
    roots = []
    servers = []
    expected_fragment = "cd %s && nohup python3 -m http.server %s --bind %s" % (
        PG_HTTP_ROOT, PG_HTTP_PORT, PG_HTTP_BIND)
    for row, argv in candidates:
        if (len(argv) == 3 and Path(argv[0]).name == "bash" and argv[1] == "-c"
                and expected_fragment in argv[2]
                and "http_tpch_queries_20260726.log" in argv[2]
                and int(row.get("ppid") or 0) == 1):
            roots.append((row, argv))
        elif (len(argv) == 6 and Path(argv[0]).name.startswith("python3")
              and argv[1:] == ["-m", "http.server", PG_HTTP_PORT,
                               "--bind", PG_HTTP_BIND]):
            servers.append((row, argv))
    if len(roots) != 1 or len(servers) != 1:
        raise core.QualificationError("PG HTTP service exact parent/child cardinality differs")
    root, root_argv = roots[0]
    server, server_argv = servers[0]
    if int(server.get("ppid") or 0) != int(root.get("pid") or 0):
        raise core.QualificationError("PG HTTP service parent/child lineage differs")
    return ((root, root_argv), (server, server_argv))


def _classify_pg_http_dynamic_noninterference(result):
    pairs = _exact_pg_http_rows(result)
    samples = []
    dynamic_conflict = False
    for row, argv in pairs:
        pid = int(row["pid"])
        proc = Path("/proc") / str(pid)
        try:
            cwd = Path(os.readlink(proc / "cwd")).resolve(strict=True)
            exe = Path(os.readlink(proc / "exe")).resolve(strict=True)
            exe_stat = exe.stat()
        except (OSError, PermissionError) as exc:
            raise core.QualificationError("PG HTTP service exe/cwd identity unreadable: %s" % exc)
        if cwd != PG_HTTP_ROOT.resolve(strict=True):
            raise core.QualificationError("PG HTTP service cwd identity differs")
        source_stat = PG_HTTP_ROOT.stat()
        source_mm = "%d:%d" % (os.major(source_stat.st_dev), os.minor(source_stat.st_dev))
        if source_mm == PROFILES["tpcc_cpu"]["device_major_minor"]:
            raise core.QualificationError("PG HTTP service shares TPC-C target device")
        if int(row.get("declared_device_writable_fds") or 0) != 0:
            raise core.QualificationError("PG HTTP service has target-device writer")
        current = {
            "pid": pid,
            "starttime_ticks": int(row["starttime_ticks"]),
            "cmdline_sha256": row["cmdline_sha256"],
            "exe_path": str(exe),
            "exe_stat_identity": [int(exe_stat.st_dev), int(exe_stat.st_ino),
                                  int(exe_stat.st_size), int(exe_stat.st_mtime_ns)],
            "cwd": str(cwd),
            "source_major_minor": source_mm,
            "cpus_allowed_list": row.get("cpus_allowed_list"),
            "mems_allowed_list": row.get("mems_allowed_list"),
            "cpu_ticks": _proc_cpu_ticks(pid, row["starttime_ticks"]),
            "read_bytes": int((row.get("process_io") or {}).get("read_bytes") or 0),
            "write_bytes": int((row.get("process_io") or {}).get("write_bytes") or 0),
            "target_device_writable_fds": int(row.get("declared_device_writable_fds") or 0),
        }
        key = "root" if row is pairs[0][0] else "server"
        previous = _pg_http_previous.get(key)
        delta = {"cpu_ticks": 0, "read_bytes": 0, "write_bytes": 0}
        if previous is not None:
            identity_keys = ("pid", "starttime_ticks", "cmdline_sha256", "exe_path",
                             "exe_stat_identity", "cwd", "source_major_minor",
                             "cpus_allowed_list", "mems_allowed_list")
            if any(previous.get(name) != current.get(name) for name in identity_keys):
                raise core.QualificationError("PG HTTP service rolling identity changed")
            for name in delta:
                delta[name] = current[name] - previous[name]
            if (delta["cpu_ticks"] < 0 or delta["read_bytes"] < 0 or delta["write_bytes"] < 0
                    or delta["cpu_ticks"] > PG_HTTP_MAX_ADJACENT_CPU_TICKS
                    or delta["read_bytes"] > PG_HTTP_MAX_ADJACENT_READ_BYTES
                    or delta["write_bytes"] > PG_HTTP_MAX_ADJACENT_WRITE_BYTES):
                dynamic_conflict = True
        _pg_http_previous[key] = dict(current)
        row["role"] = "co_resident_pg_artifact_http_dynamic_probe"
        row["pg_http_dynamic_policy_version"] = PG_HTTP_POLICY_VERSION
        samples.append({"subrole": key, **current, "adjacent_delta": delta})
    conflicts = list(result.get("conflicts") or [])
    if dynamic_conflict:
        conflicts.append("pg_http_dynamic_interference")
    result["conflicts"] = sorted(set(conflicts))
    result["mysql_only_pg_http_dynamic_noninterference"] = {
        "schema_version": PG_HTTP_POLICY_VERSION,
        "status": "PASS_SAMPLE" if not dynamic_conflict else "FAIL_SAMPLE",
        "not_database_or_benchmark_process": True,
        "static_process_identity_trusted": False,
        "postgresql_mutation_performed": False,
        "target_device_major_minor": PROFILES["tpcc_cpu"]["device_major_minor"],
        "thresholds": {
            "maximum_adjacent_cpu_ticks": PG_HTTP_MAX_ADJACENT_CPU_TICKS,
            "maximum_adjacent_read_bytes": PG_HTTP_MAX_ADJACENT_READ_BYTES,
            "maximum_adjacent_write_bytes": PG_HTTP_MAX_ADJACENT_WRITE_BYTES,
        },
        "processes": samples,
    }
    return result


def _classify_complete_tpcc_controller_lineage(result):
    result = _phase_prime_classify_tpcc_controller(result)
    rows = result.get("processes") or []
    by_pid = {int(row.get("pid") or 0): row for row in rows}
    execute_ids = result.get("verified_local_controller_ancestor_pids") or []
    if len(execute_ids) != 1:
        raise core.QualificationError("TPC-C execute controller lineage differs")
    execute = by_pid[int(execute_ids[0])]
    execute_argv = _proc_argv_and_hash(int(execute["pid"]), execute.get("cmdline_sha256"))
    scripts = [Path(value).resolve(strict=True) for value in execute_argv
               if Path(value).name == "mysql_tpcc_v16_independent_parallel_one_click_v1.py"]
    if len(scripts) != 1:
        raise core.QualificationError("TPC-C execute script identity differs")
    script = scripts[0]
    run_rows = []
    for row in rows:
        try:
            argv = _proc_argv_and_hash(int(row.get("pid") or 0), row.get("cmdline_sha256"))
        except core.QualificationError:
            continue
        if (sum(Path(value).name == script.name for value in argv) == 1
                and argv.count("run") == 1 and "execute" not in argv
                and Path(next(value for value in argv if Path(value).name == script.name)).resolve(strict=True) == script):
            try:
                _python_script_index(argv, script)
            except core.QualificationError:
                continue
            run_rows.append((row, argv))
    if len(run_rows) != 1:
        raise core.QualificationError("TPC-C run controller lineage cardinality differs")
    run_row, run_argv = run_rows[0]
    sudo_row = by_pid.get(int(run_row.get("ppid") or 0))
    if sudo_row is None:
        raise core.QualificationError("TPC-C sudo launch ancestor is absent")
    sudo_argv = _proc_argv_and_hash(int(sudo_row["pid"]), sudo_row.get("cmdline_sha256"))
    expected_sudo = ["/usr/bin/sudo", "-n", str(Path(sys.executable).resolve()),
                     "-B", str(script), "run"]
    actual_sudo = list(sudo_argv)
    try:
        actual_sudo[0] = str(Path(actual_sudo[0]).resolve(strict=True))
        actual_sudo[2] = str(Path(actual_sudo[2]).resolve(strict=True))
    except (OSError, IndexError) as exc:
        raise core.QualificationError("TPC-C sudo launch ancestor executable unreadable: %s" % exc)
    if actual_sudo != expected_sudo:
        raise core.QualificationError("TPC-C sudo launch ancestor identity differs")
    for role, row in (("qualification_controller_run", run_row),
                      ("qualification_controller_sudo_gate", sudo_row)):
        if (row.get("container_identity") is not None
                or int(row.get("declared_device_writable_fds") or 0) != 0):
            raise core.QualificationError("TPC-C controller ancestor isolation differs")
        row["role"] = "qualification_control_plane"
        row["controller_lineage_subrole"] = role
        row["controller_lineage_authority"] = "sealed_bundle_plan_authority_exact_execute_run_sudo_chain_v1"
    execute["controller_lineage_subrole"] = "qualification_controller_execute"
    result["verified_local_controller_ancestor_pids"] = [
        int(sudo_row["pid"]), int(run_row["pid"]), int(execute["pid"])]
    result["local_controller_lineage_exact_authority_verified"] = True
    result["local_controller_lineage_schema_version"] = "mysql_tpcc_complete_controller_lineage_v1"
    return result


# Cache only immutable root mount-device authority.  Every sample still runs
# Docker ps/inspect, checks the exact live container ID/PID/mount projection,
# and reads current cgroup IO counters in the original collector.
_uncached_root_docker_mount_inventory = _root_docker_mount_inventory
def _root_docker_mount_inventory():
    if _active_profile is None:
        raise core.QualificationError("shared Docker profile is not bound")
    key = _active_profile["socket_uri"]
    if key not in _root_inventory_cache:
        value, evidence = _uncached_root_docker_mount_inventory()
        _root_inventory_cache[key] = json.loads(json.dumps([value, evidence], sort_keys=True))
    value, evidence = json.loads(json.dumps(_root_inventory_cache[key], sort_keys=True))
    evidence["same_live_immutable_mount_cache_policy"] = "mysql_shared_same_live_root_mount_cache_v1"
    evidence["current_docker_ps_inspect_and_cgroup_io_revalidation_required"] = True
    evidence["dynamic_cgroup_io_cached"] = False
    return value, evidence


_phase_prime_classify_tpcc_controller = _classify_verified_tpcc_controller
_classify_verified_tpcc_controller = _classify_complete_tpcc_controller_lineage
'''


TEST = r'''import hashlib,importlib.util,json,time
from pathlib import Path
import pytest
B=Path(__file__).resolve().parents[1]
E=B/"qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"
C=B/"qualification/mysql_distributed_live_readback_qualification_v1.py"
def load(path,name):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def test_content_address_scope_and_zero_credit():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\n").encode()).hexdigest()==m["bundle_id"]==B.name
 assert m["formal_scope"]=="mysql_only" and m["postgresql_mutation_prohibited"] is True and m["accepted_round_authorized"] is False and m["accepted_rounds_credit"]==0
def test_complete_controller_and_pg_http_are_exact_dynamic_authorities():
 s=E.read_text();assert "mysql_tpcc_complete_controller_lineage_v1" in s and "qualification_controller_sudo_gate" in s and "qualification_controller_run" in s
 assert "mysql_only_pg_artifact_http_dynamic_noninterference_v1" in s and "static_process_identity_trusted\": False" in s and "postgresql_mutation_performed\": False" in s
 assert "PG_HTTP_MAX_ADJACENT_CPU_TICKS = 2" in s and "PG_HTTP_MAX_ADJACENT_READ_BYTES = 0" in s and "PG_HTTP_MAX_ADJACENT_WRITE_BYTES = 0" in s
 assert "current_docker_ps_inspect_and_cgroup_io_revalidation_required" in s and "dynamic_cgroup_io_cached\"] = False" in s
def test_core_includes_http_parent_child_for_proof_not_whitelist():
 s=C.read_text();assert "pg_http_candidate_tree" in s and "not authorization" in s
 assert "MAXIMUM_SAMPLE_GAP_SECONDS = 5.0" in s and "WORKLOAD_SAMPLE_TIMEOUT_SECONDS = 1.0" in s
def test_typed_transport_and_phase_sequences_unchanged():
 m=json.loads((B/"bundle_manifest.json").read_text());assert m["typed_binding_count"]==34 and m["transaction_types"]==["Delivery","NewOrder","OrderStatus","Payment","StockLevel"] and m["transaction_aliases"]=={"SensitiveStockLevel":"StockLevel"}
 assert m["native_metric_phase_prime_sequence"]==1 and m["native_metric_producer_initial_sequence"]==2 and m["exact_consumer_first_sequence"]==0 and m["exact_consumer_last_sequence"]==180
 assert hashlib.sha256((B/"qualification/mysql_shared_tpcc_counter_transport_v1.py").read_bytes()).hexdigest()=="f0462fefbf79630d8617756b0363d43de1341c7498ebd871b3352e4a0e5359ea"
'''


def verify_parent():
    manifest = json.loads((PARENT / "bundle_manifest.json").read_text())
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (sha(PARENT / "bundle_manifest.json") != PARENT_SHA
            or digest(canonical(unsigned)) != PARENT_ID
            or manifest.get("bundle_id") != PARENT_ID):
        raise BuildError("parent identity differs")
    for relative, wanted in manifest["files"].items():
        if sha(PARENT / relative) != wanted:
            raise BuildError("parent closure differs: " + relative)
    return manifest


def patch_core(raw):
    text = raw.decode()
    anchor = '    control_plane = {os.getpid(), int(control_pid)}\n\n    mysql_cpus = expand_cpuset(mysql_cpuset)'
    return once(text, anchor,
                '    control_plane = {os.getpid(), int(control_pid)}\n' + CORE_TREE +
                '\n    mysql_cpus = expand_cpuset(mysql_cpuset)', "core HTTP closure").replace(
        '            or container_row is not None\n            or CORESIDENT_PROCESS_PATTERN.search(',
        '            or container_row is not None\n            or pid in pg_http_candidate_tree\n            or CORESIDENT_PROCESS_PATTERN.search(', 1).encode()


def patch_entry(raw):
    text = raw.decode()
    text = once(text, '\ncore._root_docker_mount_inventory = _root_docker_mount_inventory\n',
                ENTRY_SUCCESSOR +
                '\n\ncore._root_docker_mount_inventory = _root_docker_mount_inventory\n',
                "entry successor helpers")
    anchor = '''        result = _mark_pid_set_consistency(result, before, after)
        result = _classify_verified_tpcc_controller(result)
        result = _classify_two_verified_daemons(
'''
    replacement = '''        result = _mark_pid_set_consistency(result, before, after)
        result = _classify_verified_tpcc_controller(result)
        result = _classify_pg_http_dynamic_noninterference(result)
        result = _classify_two_verified_daemons(
'''
    return once(text, anchor, replacement, "dynamic classifier invocation").encode()


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def main():
    parent_manifest = verify_parent()
    content = {}
    for relative in sorted(parent_manifest["files"]):
        if not relative.startswith("tests/") and not relative.startswith("builder/"):
            content[relative] = (PARENT / relative).read_bytes()
    content[CORE] = patch_core(content[CORE])
    content[ENTRY] = patch_entry(content[ENTRY])
    content["tests/test_mysql_tpcc_inventory_dynamic_successor_v1.py"] = TEST.encode()
    content["builder/build_mysql_tpcc_inventory_dynamic_successor_v1.py"] = HERE.read_bytes()
    audit = {
        "schema_version": "mysql_tpcc_30664_zero_credit_root_cause_v1",
        "failed_bundle_id": "30664c823bc8335e68abd80dfc67cfd89e8374cd1477d5f7532f8efd71bb3a21",
        "failed_error": "workload metric sample age exceeded after seq0..85; earlier NOT_READY_PROCESS_INVENTORY co_resident_cpu_or_device",
        "failed_credit": 0,
        "rerun_prohibited": True,
        "root_causes": [
            "controller run and sudo launch ancestors were not classified by the sealed controller lineage",
            "PG query-artifact HTTP parent/child had no versioned rolling CPU/IO non-interference proof",
            "two sequential root mount helper calls made the strict exact metric consumer drift beyond five seconds",
        ],
    }
    content["authority/mysql_tpcc_30664_zero_credit_root_cause_v1.json"] = canonical(audit)
    files = {relative: digest(payload) for relative, payload in sorted(content.items())}
    unsigned = {
        "schema_version": "mysql_tpcc_inventory_dynamic_successor_bundle_v1",
        "status": "SEALED_NOT_EXECUTED",
        "immutable": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "parent_bundle_id": PARENT_ID,
        "parent_manifest_sha256": PARENT_SHA,
        "failed_30664_credit": 0,
        "failed_attempt_rerun_prohibited": True,
        "controller_lineage_schema_version": "mysql_tpcc_complete_controller_lineage_v1",
        "pg_http_dynamic_policy_version": "mysql_only_pg_artifact_http_dynamic_noninterference_v1",
        "root_mount_cache_policy_version": "mysql_shared_same_live_root_mount_cache_v1",
        "typed_binding_count": 34,
        "transaction_types": ["Delivery", "NewOrder", "OrderStatus", "Payment", "StockLevel"],
        "transaction_aliases": {"SensitiveStockLevel": "StockLevel"},
        "qps_resource_input_prohibited": True,
        "native_metric_phase_prime_sequence": 1,
        "native_metric_producer_initial_sequence": 2,
        "exact_consumer_first_sequence": 0,
        "exact_consumer_last_sequence": 180,
        "freshness_threshold_relaxed": False,
        "timeout_threshold_relaxed": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "files": files,
    }
    bundle_id = digest(canonical(unsigned))
    manifest = dict(unsigned, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / bundle_id
    if destination.exists():
        print(bundle_id); print(sha(destination / "bundle_manifest.json")); print(destination); return
    stage = Path(tempfile.mkdtemp(prefix=".inventory-dynamic-", dir=OUT))
    try:
        for relative, payload in content.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        raw = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            digest(raw) + "  bundle_manifest.json\n", encoding="ascii")
        freeze(stage)
        os.rename(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print(bundle_id); print(sha(destination / "bundle_manifest.json")); print(destination)


if __name__ == "__main__":
    main()
