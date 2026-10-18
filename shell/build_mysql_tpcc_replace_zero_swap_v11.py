#!/usr/bin/env python3
"""Build the immutable recoverable TPC-C container-replacement transaction."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
CONTROL_SOURCE = ROOT / "shell/mysql_tpcc_replace_zero_swap_v11.py"
CONTROL = "control/mysql_tpcc_replace_zero_swap_v11.py"
OUT = ROOT / "artifacts/mysql_tpcc_replace_zero_swap_v11"
COLLECTOR_ID = "1878a2ca8b68e1b3917021499cda396e23f60a23baacd299496c78346e29c86c"
COLLECTOR = ROOT / "artifacts/mysql_tpcc_typed_collector_portable_v2" / COLLECTOR_ID
IMAGE = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
SOCKET = "unix:///run/ra-docker-mysql-tpcc-v1.sock"
DOCKER = ["/usr/bin/docker", "--host", SOCKET]
ATTEMPT = "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08"
DB = "ra_mysql_tpcc_v2_db_tpcc-v16-7e2e13aa-20260801t121500p08"
CLIENT = "ra_mysql_tpcc_v2_client_tpcc-v16-7e2e13aa-20260801t121500p08"
DB_ARCHIVE = DB + "-archive-v11-5cdcc870"
CLIENT_ARCHIVE = CLIENT + "-archive-v11-445c4e17"


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode()


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def file_sha(path):
    return sha(Path(path).read_bytes())


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


ENV = [
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "GOSU_VERSION=1.19", "MYSQL_MAJOR=8.0", "MYSQL_VERSION=8.0.45-1.el9",
    "MYSQL_SHELL_VERSION=8.0.45-1.el9",
]
LABELS = {
    "ra.attempt_id": "tpcc-v16-7e2e13aa-20260801t121500p08",
    "ra.bundle_id": "7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77",
    "ra.cross_engine_comparison_prohibited": "true", "ra.formal_scope": "mysql_only",
    "ra.handoff_token_sha256": "3f82130508994eff565ba3d47638bb1f3bb1beb25f25fe4ad5618cff8f56602e",
    "ra.role": None, "ra.scenario": "tpcc",
}


def mount(kind, source, destination, rw, propagation="rprivate", name=None):
    return {"Type": kind, "Name": name, "Source": source, "Destination": destination, "RW": rw, "Propagation": propagation}


def base(role):
    labels = dict(LABELS)
    labels["ra.role"] = role
    return {
        "image": IMAGE, "hostname": "node04", "user": "", "env": ENV,
        "cmd": None, "entrypoint": None, "labels": labels, "working_dir": "/",
        "network_mode": "host", "cgroupns_mode": "host", "ipc_mode": "private",
        "runtime": "runc", "log_config": {"Type": "none", "Config": {}},
        "restart_policy": {"Name": "no", "MaximumRetryCount": 0},
        "readonly_rootfs": False, "cap_drop": None, "security_opt": None,
        "privileged": False, "memory": 0, "memory_swap": 0, "memory_swappiness": None,
        "pids_limit": 0, "cpu_period": 100000, "cpu_quota": 0,
        "cpuset_cpus": "", "cpuset_mems": "", "shm_size": 67108864, "mounts": [],
    }


EXPECTED_DB = base("database")
EXPECTED_DB.update({
    "cmd": ["--datadir=/var/lib/mysql", "--innodb-buffer-pool-size=4294967296", "--bind-address=127.0.0.1", "--port=65106"],
    "entrypoint": ["docker-entrypoint.sh"], "memory": 34359738368,
    "memory_swap": 34359738368, "pids_limit": 512, "cpu_quota": 400000,
    "cpuset_cpus": "21-24,57-60", "cpuset_mems": "1",
    "mounts": [mount("bind", ATTEMPT + "/datadir/datadir", "/var/lib/mysql", True)],
})
EXPECTED_CLIENT = base("client")
EXPECTED_CLIENT.update({
    "user": "1008:1008", "cmd": ["-c", "import time; time.sleep(315360000)"],
    "entrypoint": ["/opt/mysql-tpcc-python/bin/python3.11"], "readonly_rootfs": True,
    "cap_drop": ["ALL"], "security_opt": ["no-new-privileges"],
    "memory": 8589934592, "memory_swap": 8589934592, "pids_limit": 1024,
    "cpu_quota": 1600000, "cpuset_cpus": "8-15,44-51", "cpuset_mems": "0",
    "mounts": sorted([
        mount("bind", "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/bundles/7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77", "/opt/mysql-tpcc-bundle", False),
        mount("bind", "/home/anonymous/recost_deploy_20260721_v1/runtime/jdk-23.0.2-oracle-x64", "/opt/mysql-tpcc-jdk", False),
        mount("bind", "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89", "/opt/mysql-tpcc-python", False),
        mount("bind", ATTEMPT + "/config", "/opt/mysql-tpcc-config", False),
        mount("bind", ATTEMPT + "/measurement", "/measurement", True),
        mount("bind", "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/bundles/7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77/benchbase_runtime/config", "/measurement/config", False),
        mount("volume", "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/docker_ra_tpcc_v1/volumes/169d9b39dfb3f97a55bb17e8455b0816dc262163c7c79ee3ad6c414d87b29ae4/_data", "/var/lib/mysql", True, "", "169d9b39dfb3f97a55bb17e8455b0816dc262163c7c79ee3ad6c414d87b29ae4"),
    ], key=lambda value: (value["Destination"], value["Source"])),
})


def labels(role):
    values = dict(LABELS)
    values["ra.role"] = role
    result = []
    for key, value in sorted(values.items()):
        result.extend(["--label", key + "=" + value])
    return result


COMMON = [
    "--hostname", "node04", "--network", "host", "--cgroupns", "host",
    "--ipc", "private", "--runtime", "runc", "--log-driver", "none",
    "--restart", "no", "--cpu-period", "100000", "--shm-size", "67108864",
]
DB_CREATE = DOCKER + ["create", "--name", DB] + COMMON + [
    "--memory", "34359738368", "--memory-swap", "34359738368", "--memory-swappiness", "0",
    "--pids-limit", "512", "--cpu-quota", "400000", "--cpuset-cpus", "21-24,57-60",
    "--cpuset-mems", "1", "--mount", "type=bind,src=" + ATTEMPT + "/datadir/datadir,dst=/var/lib/mysql",
] + labels("database") + [IMAGE, "--datadir=/var/lib/mysql", "--innodb-buffer-pool-size=4294967296", "--bind-address=127.0.0.1", "--port=65106"]
CLIENT_MOUNTS = [
    "type=bind,src=/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/bundles/7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77,dst=/opt/mysql-tpcc-bundle,readonly",
    "type=bind,src=/home/anonymous/recost_deploy_20260721_v1/runtime/jdk-23.0.2-oracle-x64,dst=/opt/mysql-tpcc-jdk,readonly",
    "type=bind,src=/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89,dst=/opt/mysql-tpcc-python,readonly",
    "type=bind,src=" + ATTEMPT + "/config,dst=/opt/mysql-tpcc-config,readonly",
    "type=bind,src=" + ATTEMPT + "/measurement,dst=/measurement",
    "type=bind,src=/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/bundles/7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77/benchbase_runtime/config,dst=/measurement/config,readonly",
    "type=volume,src=169d9b39dfb3f97a55bb17e8455b0816dc262163c7c79ee3ad6c414d87b29ae4,dst=/var/lib/mysql",
]
CLIENT_CREATE = DOCKER + ["create", "--name", CLIENT] + COMMON + [
    "--user", "1008:1008", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
    "--memory", "8589934592", "--memory-swap", "8589934592", "--memory-swappiness", "0",
    "--pids-limit", "1024", "--cpu-quota", "1600000", "--cpuset-cpus", "8-15,44-51",
    "--cpuset-mems", "0",
]
for value in CLIENT_MOUNTS:
    CLIENT_CREATE.extend(["--mount", value])
CLIENT_CREATE += labels("client") + ["--entrypoint", "/opt/mysql-tpcc-python/bin/python3.11", IMAGE, "-c", "import time; time.sleep(315360000)"]


TEST = r'''import ast,hashlib,json
from pathlib import Path
B=Path(__file__).resolve().parents[1]
M=B/"bundle_manifest.json";R=B/"runbook/transaction.json";C=B/"control/mysql_tpcc_replace_zero_swap_v11.py"
def test_content_address_and_scope():
 m=json.loads(M.read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\n").encode()).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only" and m["accepted_rounds_credit"]==0
def test_control_parses_and_zero_is_strict():
 s=C.read_text();ast.parse(s);assert 'state["total_swap_bytes"] != 0' in s and 'memory_swappiness"] != 0' in s and "swap_tolerance" not in s
def test_v10_no_effect_is_sealed_not_reused():
 e=json.loads((B/"evidence/v10_api_no_effect_failure.json").read_text());assert e["api_posts"]==2 and e["http_status"]==200 and e["docker_hostconfig_memory_swappiness_after"] is None and e["database_cgroup_memory_swappiness_after"]==60 and e["client_cgroup_memory_swappiness_after"]==60 and e["update_receipt_exists"] is False
def test_old_containers_are_archived_never_deleted():
 r=json.loads(R.read_text());text=json.dumps(r);assert "archive-v11" in text and '"rm"' not in text and "remove" not in text.lower();assert r["old_containers_deleted"] is False
def test_create_has_exact_zero_swap_and_isolation():
 r=json.loads(R.read_text());creates=[x for x in r["forward_steps"] if x["action"].startswith("create_")];assert len(creates)==2
 for step in creates:
  a=step["argv"];assert ["--memory-swappiness","0"]==a[a.index("--memory-swappiness"):a.index("--memory-swappiness")+2];assert "--memory-swap" in a and "--cpuset-cpus" in a and "--cpuset-mems" in a and ["--network","host"]==a[a.index("--network"):a.index("--network")+2]
def test_client_is_idle_and_no_workload_start():
 r=json.loads(R.read_text());text=json.dumps(r);assert "time.sleep(315360000)" in text and "DBWorkload" not in text and r["workload_started"] is False
def test_rollback_quarantines_new_and_restores_old_without_delete():
 r=json.loads(R.read_text());assert r["rollback"]["action"]=="rollback" and r["rollback"]["replacement_containers_deleted"] is False and r["rollback"]["old_containers_restored"] is True
def test_typed_collector_closure_and_contract():
 m=json.loads((B/"collector/bundle_manifest.json").read_text());assert m["bundle_id"]=="1878a2ca8b68e1b3917021499cda396e23f60a23baacd299496c78346e29c86c" and m["binding_count"]==34 and m["qps_resource_input_prohibited"] is True and m["required_transaction_types"]==["Delivery","NewOrder","OrderStatus","Payment","StockLevel"]
def test_pg_is_prohibited_and_target_checked():
 s=C.read_text();assert "database target is a PostgreSQL cgroup" in s and 'postgresql_mutation_performed": False' in s
'''


def main():
    collector_manifest = json.loads((COLLECTOR / "bundle_manifest.json").read_text())
    if collector_manifest.get("bundle_id") != COLLECTOR_ID:
        raise RuntimeError("collector identity differs")
    for relative, want in collector_manifest["files"].items():
        if file_sha(COLLECTOR / relative) != want:
            raise RuntimeError("collector closure differs: " + relative)
    expected = {"database": EXPECTED_DB, "client": EXPECTED_CLIENT}
    py = "[PINNED_HOST_PYTHON]"
    bundle = "[THIS_PUBLISHED_BUNDLE]"
    before = ATTEMPT + "/recreate_swappiness0_v11.preflight.json"
    rollback_output = ATTEMPT + "/recreate_swappiness0_v11.rollback.json"
    runbook = {
        "schema_version": "mysql_tpcc_recoverable_container_replacement_v11",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "main_agent_serial_execution_required": True,
        "old_containers_deleted": False, "workload_started": False,
        "forward_steps": [
            {"step": 0, "action": "preflight", "argv": [py, "-B", bundle + "/" + CONTROL, "preflight", "--output", before]},
            {"step": 1, "action": "stop_old_client", "argv": DOCKER + ["stop", "--time", "30", CLIENT]},
            {"step": 2, "action": "stop_old_database", "graceful_timeout_seconds": 120, "argv": DOCKER + ["stop", "--time", "120", DB]},
            {"step": 3, "action": "archive_old_client", "atomic_name_change": True, "argv": DOCKER + ["rename", CLIENT, CLIENT_ARCHIVE]},
            {"step": 4, "action": "archive_old_database", "atomic_name_change": True, "argv": DOCKER + ["rename", DB, DB_ARCHIVE]},
            {"step": 5, "action": "create_database", "argv": DB_CREATE},
            {"step": 6, "action": "create_client", "argv": CLIENT_CREATE},
            {"step": 7, "action": "start_database", "argv": DOCKER + ["start", DB]},
            {"step": 8, "action": "start_idle_client", "argv": DOCKER + ["start", CLIENT]},
            {"step": 9, "action": "verify_reapply_and_adopt", "argv": [py, "-B", bundle + "/" + CONTROL, "adopt", "--before", before, "--output", ATTEMPT + "/adopted_service.recreated_swappiness0_v3.json"]},
        ],
        "rollback_trigger": "any failure after forward step 1 and before step 9 PASS",
        "rollback": {"action": "rollback", "argv": [py, "-B", bundle + "/" + CONTROL, "rollback", "--output", rollback_output], "old_containers_restored": True, "replacement_containers_deleted": False, "replacement_containers_quarantined": True},
        "success_postconditions": ["old container IDs retained stopped under archive names", "new formal-name container IDs and PID/start epochs", "exact image/config/datadir/mount/network/cpu/memory/pids isolation", "HostConfig MemorySwappiness 0", "cgroup memory.swappiness 0", "database and client total_swap 0", "client is idle Python with no children", "MySQL port 65106 ready without SQL workload", "DB-only blkio 259:2 read/write 1205862400 read back", "portable typed collector 34-binding/five-transaction closure verified", "new non-overwriting authority chain", "no PostgreSQL mutation"],
        "formal_launch_authorized": False, "accepted_round_authorized": False, "accepted_rounds_credit": 0,
    }
    failure = {"schema_version": "mysql_tpcc_v10_docker_api_no_effect_failure_v1", "observed_at": "2026-08-04T15:30:00+08:00", "formal_scope": "mysql_only", "failed_bundle_id": "6dfcc1b21613f329ac15e51c517b2c9939ec252365133033600f6eca0435c646", "preflight_sha256": "0a4b538414d9a92eca7ded3498c6cf02337e80be4f9435bb6377d55823f9f304", "api_version": "v1.45", "api_posts": 2, "http_status": 200, "warnings": None, "docker_hostconfig_memory_swappiness_after": None, "database_cgroup_memory_swappiness_after": 60, "client_cgroup_memory_swappiness_after": 60, "update_receipt_exists": False, "conclusion": "Docker Engine 26.1.4 accepted but ignored MemorySwappiness in container update; v10 is failed and prohibited as authority", "accepted_rounds_credit": 0, "postgresql_mutation_performed": False}
    contents = {CONTROL: CONTROL_SOURCE.read_bytes(), "authority/expected_container_projection.json": canonical(expected), "runbook/transaction.json": canonical(runbook), "evidence/v10_api_no_effect_failure.json": canonical(failure), "tests/test_mysql_tpcc_replace_zero_swap_v11.py": TEST.encode(), "builder/build_mysql_tpcc_replace_zero_swap_v11.py": HERE.read_bytes()}
    for path in COLLECTOR.iterdir():
        if path.is_file():
            contents["collector/" + path.name] = path.read_bytes()
    files = {relative: sha(payload) for relative, payload in sorted(contents.items())}
    unsigned = {"schema_version": "mysql_tpcc_replace_zero_swap_bundle_v11", "status": "SEALED_NOT_EXECUTED", "immutable": True, "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True, "postgresql_mutation_prohibited": True, "supersedes_failed_bundle_id": "6dfcc1b21613f329ac15e51c517b2c9939ec252365133033600f6eca0435c646", "replacement_strategy": "archive_old_create_new_same_datadir_recoverable", "old_container_deletion_prohibited": True, "replacement_failure_rollback_required": True, "memory_swappiness_required": 0, "zero_swap_threshold_bytes": 0, "typed_collector_bundle_id": COLLECTOR_ID, "formal_launch_authorized": False, "accepted_round_authorized": False, "accepted_rounds_credit": 0, "remote_actions_performed": 0, "files": files}
    bundle_id = sha(canonical(unsigned))
    manifest = dict(unsigned, bundle_id=bundle_id)
    raw = canonical(manifest)
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / bundle_id
    if target.exists():
        if (target / "bundle_manifest.json").read_bytes() != raw:
            raise RuntimeError("existing manifest differs")
        for relative, payload in contents.items():
            if (target / relative).read_bytes() != payload:
                raise RuntimeError("existing closure differs: " + relative)
    else:
        stage = Path(tempfile.mkdtemp(prefix=".tpcc-replace-v11-", dir=OUT))
        try:
            for relative, payload in contents.items():
                path = stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(sha(raw) + "  bundle_manifest.json\n")
            freeze(stage)
            os.rename(stage, target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    print(bundle_id)
    print(sha(raw))
    print(target)


if __name__ == "__main__":
    main()
