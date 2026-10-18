#!/usr/bin/env python3
"""Fail-closed TPC-C same-datadir container replacement and authority sealer."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

SOCKET = "unix:///run/ra-docker-mysql-tpcc-v1.sock"
DOCKER = ["/usr/bin/docker", "--host", SOCKET]
ATTEMPT = Path("/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08")
DB = "ra_mysql_tpcc_v2_db_tpcc-v16-7e2e13aa-20260801t121500p08"
CLIENT = "ra_mysql_tpcc_v2_client_tpcc-v16-7e2e13aa-20260801t121500p08"
OLD_DB_ID = "5cdcc870ef04435bad3b44eb89135fd00b5d4825179887e2a51862712f1e3db6"
OLD_CLIENT_ID = "445c4e1732fe6cf8cb07e8f32159aac5cf0aa10bcd1fd51a9db3e5a725a0a1e6"
DB_ARCHIVE = DB + "-archive-v11-5cdcc870"
CLIENT_ARCHIVE = CLIENT + "-archive-v11-445c4e17"
DB_FAILED = DB + "-failed-v11"
CLIENT_FAILED = CLIENT + "-failed-v11"
BLKIO_ROOT = Path("/sys/fs/cgroup/blkio/docker")
MEMORY_ROOT = Path("/sys/fs/cgroup/memory/docker")
DEVICE = "259:2"
CAP = 1205862400
PREFLIGHT_V10 = ATTEMPT / "zero_swap_swappiness0_restart_v2.preflight.json"
PREFLIGHT_V10_SHA = "0a4b538414d9a92eca7ded3498c6cf02337e80be4f9435bb6377d55823f9f304"
V10_UPDATE = ATTEMPT / "zero_swap_swappiness0_restart_v2.update.json"
ADOPTED_OLD = ATTEMPT / "adopted_service.zero_swap_restart_v1.json"
ADOPTED_OLD_SHA = "8f73143b1ded2d344e7b6a577d0c438ab8705eec64a76605f214b99b93d8246e"
TARGET = ATTEMPT / "adopted_service.recreated_swappiness0_v3.json"
REAPPLY = ATTEMPT / "blkio_throttle_reapply.recreated_swappiness0_v3.json"
FRESH = ATTEMPT / "blkio_throttle_authority.recreated_swappiness0_v3.json"
IDLE_ENTRYPOINT = ["/opt/mysql-tpcc-python/bin/python3.11"]
IDLE_CMD = ["-c", "import time; time.sleep(315360000)"]


class NotReady(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic(path, value):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise NotReady("fresh output required: " + str(path))
    raw = canonical(value)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    sidecar.write_text(hashlib.sha256(raw).hexdigest() + "  " + path.name + "\n", encoding="ascii")


def docker(*args, check=True):
    row = subprocess.run(DOCKER + list(args), text=True, capture_output=True, check=False, timeout=30)
    if check and row.returncode != 0:
        raise NotReady("docker command failed: " + " ".join(args) + ": " + row.stderr.strip())
    return row


def inspect(name, required=True):
    row = docker("inspect", name, check=False)
    if row.returncode != 0:
        if required:
            raise NotReady("container absent: " + name)
        return None
    values = json.loads(row.stdout)
    if len(values) != 1:
        raise NotReady("inspect cardinality differs: " + name)
    return values[0]


def mount_projection(rows):
    projected = []
    for row in rows or []:
        projected.append({key: row.get(key) for key in ("Type", "Name", "Source", "Destination", "RW", "Propagation")})
    return sorted(projected, key=lambda value: (value["Destination"] or "", value["Source"] or ""))


def projection(row):
    config = row.get("Config") or {}
    host = row.get("HostConfig") or {}
    return {
        "image": row.get("Image"), "hostname": config.get("Hostname"), "user": config.get("User"),
        "env": config.get("Env"), "cmd": config.get("Cmd"), "entrypoint": config.get("Entrypoint"),
        "labels": config.get("Labels"), "working_dir": config.get("WorkingDir"),
        "network_mode": host.get("NetworkMode"), "cgroupns_mode": host.get("CgroupnsMode"),
        "ipc_mode": host.get("IpcMode"), "runtime": host.get("Runtime"), "log_config": host.get("LogConfig"),
        "restart_policy": host.get("RestartPolicy"), "readonly_rootfs": host.get("ReadonlyRootfs"),
        "cap_drop": host.get("CapDrop"), "security_opt": host.get("SecurityOpt"), "privileged": host.get("Privileged"),
        "memory": host.get("Memory"), "memory_swap": host.get("MemorySwap"),
        "memory_swappiness": host.get("MemorySwappiness"), "pids_limit": host.get("PidsLimit"),
        "cpu_period": host.get("CpuPeriod"), "cpu_quota": host.get("CpuQuota"),
        "cpuset_cpus": host.get("CpusetCpus"), "cpuset_mems": host.get("CpusetMems"),
        "shm_size": host.get("ShmSize"), "mounts": mount_projection(row.get("Mounts")),
    }


def expected():
    return json.loads((Path(__file__).resolve().parents[1] / "authority/expected_container_projection.json").read_text())


def proc_state(row):
    pid = int((row.get("State") or {}).get("Pid") or 0)
    if pid <= 1 or (row.get("State") or {}).get("Running") is not True:
        raise NotReady("container is not running")
    root = Path("/proc") / str(pid)
    stat = (root / "stat").read_text()
    end = stat.rfind(") ")
    start = int(stat[end + 2:].split()[19])
    raw_cgroup = (root / "cgroup").read_text()
    memory_path = None
    for line in raw_cgroup.splitlines():
        _, controllers, path = line.split(":", 2)
        if "memory" in controllers.split(","):
            memory_path = path
    if memory_path != "/docker/" + row["Id"]:
        raise NotReady("dedicated memory cgroup differs")
    cg = MEMORY_ROOT / row["Id"]
    stats = {}
    for line in (cg / "memory.stat").read_text().splitlines():
        key, value = line.split()
        stats[key] = int(value)
    return {
        "pid": pid, "process_start_ticks": start,
        "process_cgroup_sha256": hashlib.sha256(raw_cgroup.encode()).hexdigest(),
        "memory_cgroup_path": memory_path,
        "memory_swappiness": int((cg / "memory.swappiness").read_text()),
        "total_swap_bytes": int(stats.get("total_swap", -1)),
        "memory_limit_bytes": int((cg / "memory.limit_in_bytes").read_text()),
        "memory_swap_limit_bytes": int((cg / "memory.memsw.limit_in_bytes").read_text()),
    }


def client_idle(row, state):
    if (row.get("Config") or {}).get("Entrypoint") != IDLE_ENTRYPOINT or (row.get("Config") or {}).get("Cmd") != IDLE_CMD:
        raise NotReady("client idle command differs")
    root = Path("/proc") / str(state["pid"])
    cmdline = [part.decode("utf-8", "surrogateescape") for part in (root / "cmdline").read_bytes().split(b"\0") if part]
    if cmdline != IDLE_ENTRYPOINT + IDLE_CMD or (root / "task" / str(state["pid"]) / "children").read_text().split():
        raise NotReady("client is not sealed idle Python")
    members = []
    needle = "/docker/" + row["Id"]
    for directory in Path("/proc").glob("[0-9]*"):
        try:
            if needle in (directory / "cgroup").read_text():
                members.append(int(directory.name))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    if members != [state["pid"]]:
        raise NotReady("client contains additional processes")
    return {"schema_version": "mysql_tpcc_idle_python_identity_v2", "container_id": row["Id"], "pid": state["pid"], "process_start_ticks": state["process_start_ticks"], "cmdline": cmdline, "children": [], "workload_absent": True, "dbworkload_processes": 0}


def verify_collector():
    root = Path(__file__).resolve().parents[1] / "collector"
    manifest_path = root / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if bundle_id != "1878a2ca8b68e1b3917021499cda396e23f60a23baacd299496c78346e29c86c" or hashlib.sha256(canonical(unsigned)).hexdigest() != bundle_id:
        raise NotReady("typed collector content address differs")
    for relative, want in manifest.get("files", {}).items():
        path = root / relative
        if path.is_symlink() or not path.is_file() or digest(path) != want:
            raise NotReady("typed collector closure differs: " + relative)
    required = {"binding_count": 34, "required_transaction_types": ["Delivery", "NewOrder", "OrderStatus", "Payment", "StockLevel"], "sensitive_stock_level_normalized_to": "StockLevel", "jdbc_setter_type_position_required": True, "sql_template_hash_required": True, "l2_transaction_rows_required": True, "l3_transaction_type_vectors_required": True, "qps_resource_input_prohibited": True}
    for key, want in required.items():
        if manifest.get(key) != want:
            raise NotReady("typed collector authority differs: " + key)
    return {"bundle_id": bundle_id, "manifest_sha256": digest(manifest_path), **required, "trace_authority_changed": False}


def postgres_blkio_paths():
    paths = set()
    for directory in Path("/proc").glob("[0-9]*"):
        try:
            if (directory / "comm").read_text().strip() != "postgres":
                continue
            for line in (directory / "cgroup").read_text().splitlines():
                _, controllers, path = line.split(":", 2)
                if "blkio" in controllers.split(","):
                    paths.add(path)
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return sorted(paths)


def throttle(path):
    rows = {}
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) == 2:
            rows[parts[0]] = int(parts[1])
    before = rows.get(DEVICE)
    with path.open("w", encoding="ascii") as stream:
        stream.write(DEVICE + " " + str(CAP) + "\n")
    rows = {}
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) == 2:
            rows[parts[0]] = int(parts[1])
    if rows.get(DEVICE) != CAP:
        raise NotReady("blkio readback differs")
    return {"path": str(path), "target_before": before, "target_after": CAP, "write_payload": DEVICE + " " + str(CAP)}


def exact_projection(row, role, swappiness):
    want = expected()[role]
    actual = projection(row)
    if actual.get("memory_swappiness") != swappiness:
        raise NotReady(role + " MemorySwappiness differs")
    actual = dict(actual)
    actual.pop("memory_swappiness")
    want = dict(want)
    want.pop("memory_swappiness")
    if actual != want:
        raise NotReady(role + " recreated configuration projection differs")


def preflight():
    if digest(PREFLIGHT_V10) != PREFLIGHT_V10_SHA or V10_UPDATE.exists() or Path(str(V10_UPDATE) + ".sha256").exists():
        raise NotReady("sealed v10 no-effect chain differs")
    if digest(ADOPTED_OLD) != ADOPTED_OLD_SHA:
        raise NotReady("old adopted authority differs")
    db = inspect(DB)
    client = inspect(CLIENT)
    if db["Id"] != OLD_DB_ID or client["Id"] != OLD_CLIENT_ID:
        raise NotReady("old container identity differs")
    exact_projection(db, "database", None)
    exact_projection(client, "client", None)
    db_state = proc_state(db)
    client_state = proc_state(client)
    if db_state["memory_swappiness"] != 60 or client_state["memory_swappiness"] != 60:
        raise NotReady("v10 no-effect cgroup readback differs")
    idle = client_idle(client, client_state)
    for name in (DB_ARCHIVE, CLIENT_ARCHIVE, DB_FAILED, CLIENT_FAILED):
        if inspect(name, required=False) is not None:
            raise NotReady("transaction name already exists: " + name)
    for path in (TARGET, REAPPLY, FRESH):
        if path.exists() or Path(str(path) + ".sha256").exists():
            raise NotReady("fresh v11 authority output required")
    return {"schema_version": "mysql_tpcc_recreate_zero_swap_preflight_v11", "status": "PASS_OLD_EPOCH_EXACT_AND_V10_NO_EFFECT_SEALED", "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True, "postgresql_mutation_prohibited": True, "v10_failure": {"preflight_sha256": PREFLIGHT_V10_SHA, "api_posts": 2, "http_status": 200, "warnings": None, "hostconfig_memory_swappiness_after": None, "database_cgroup_swappiness_after": 60, "client_cgroup_swappiness_after": 60, "update_receipt_absent": True}, "database": {"container_id": db["Id"], "created": db["Created"], **db_state}, "client": {"container_id": client["Id"], "created": client["Created"], **client_state}, "client_idle_evidence": idle, "typed_collector": verify_collector(), "mutation_performed": False, "formal_launch_authorized": False, "accepted_round_authorized": False, "accepted_rounds_credit": 0}


def adopt(before_path):
    before = json.loads(Path(before_path).read_text())
    if before.get("schema_version") != "mysql_tpcc_recreate_zero_swap_preflight_v11" or before.get("status") != "PASS_OLD_EPOCH_EXACT_AND_V10_NO_EFFECT_SEALED":
        raise NotReady("v11 preflight differs")
    db_old = inspect(DB_ARCHIVE)
    client_old = inspect(CLIENT_ARCHIVE)
    if db_old["Id"] != OLD_DB_ID or client_old["Id"] != OLD_CLIENT_ID or (db_old.get("State") or {}).get("Running") or (client_old.get("State") or {}).get("Running"):
        raise NotReady("archived old containers differ")
    db = inspect(DB)
    client = inspect(CLIENT)
    if db["Id"] == OLD_DB_ID or client["Id"] == OLD_CLIENT_ID or db["Id"] == client["Id"]:
        raise NotReady("fresh replacement container IDs required")
    exact_projection(db, "database", 0)
    exact_projection(client, "client", 0)
    db_state = proc_state(db)
    client_state = proc_state(client)
    for role, state in (("database", db_state), ("client", client_state)):
        if state["memory_swappiness"] != 0 or state["total_swap_bytes"] != 0 or state["memory_limit_bytes"] != state["memory_swap_limit_bytes"]:
            raise NotReady(role + " durable zero-swap readback differs")
    idle = client_idle(client, client_state)
    try:
        connection = socket.create_connection(("127.0.0.1", 65106), timeout=3)
        connection.close()
    except OSError as exc:
        raise NotReady("MySQL readiness socket failed: " + str(exc))
    pg_paths = postgres_blkio_paths()
    if "/docker/" + db["Id"] in pg_paths:
        raise NotReady("database target is a PostgreSQL cgroup")
    cg = BLKIO_ROOT / db["Id"]
    if not cg.is_dir() or cg.is_symlink():
        raise NotReady("new DB blkio cgroup differs")
    read = throttle(cg / "blkio.throttle.read_bps_device")
    write = throttle(cg / "blkio.throttle.write_bps_device")
    collector = verify_collector()
    reapply = {"schema_version": "mysql_tpcc_recreated_blkio_reapply_v3", "status": "PASS", "formal_scope": "mysql_only", "database_container_id": db["Id"], "database_pid": db_state["pid"], "device": DEVICE, "read_bps": CAP, "write_bps": CAP, "read_reapply": read, "write_reapply": write, "observed_postgresql_blkio_cgroups": pg_paths, "postgresql_mutation_performed": False, "workload_started": False, "accepted_rounds_credit": 0}
    atomic(REAPPLY, reapply)
    fresh = {"schema_version": "mysql_tpcc_recreated_blkio_authority_v3", "status": "PASS_FRESH_RECREATED_CONTAINER_EPOCH", "database_container_id": db["Id"], "database_pid": db_state["pid"], "process_start_ticks": db_state["process_start_ticks"], "device": DEVICE, "read_bps": CAP, "write_bps": CAP, "reapply_receipt_sha256": digest(REAPPLY), "postgresql_mutation_performed": False, "accepted_rounds_credit": 0}
    atomic(FRESH, fresh)
    return {"schema_version": "mysql_tpcc_recreated_service_authority_v3", "status": "PASS_DURABLE_ZERO_SWAP_RECREATED_IDLE_READY", "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True, "postgresql_mutation_prohibited": True, "old_containers": {"database": {"container_id": OLD_DB_ID, "archived_name": DB_ARCHIVE, "deleted": False, "running": False}, "client": {"container_id": OLD_CLIENT_ID, "archived_name": CLIENT_ARCHIVE, "deleted": False, "running": False}}, "database": {"container_id": db["Id"], "name": DB, "created": db["Created"], **db_state}, "client": {"container_id": client["Id"], "name": CLIENT, "created": client["Created"], **client_state}, "client_idle_evidence": idle, "mysql_port_ready": True, "typed_collector": collector, "blkio_reapply_sha256": digest(REAPPLY), "fresh_blkio_authority_sha256": digest(FRESH), "same_datadir": True, "container_recreated": True, "workload_started": False, "formal_launch_authorized": False, "accepted_round_authorized": False, "accepted_rounds_credit": 0}


def rollback():
    actions = []
    for original, old_id, archive, failed in ((CLIENT, OLD_CLIENT_ID, CLIENT_ARCHIVE, CLIENT_FAILED), (DB, OLD_DB_ID, DB_ARCHIVE, DB_FAILED)):
        current = inspect(original, required=False)
        if current is not None and current["Id"] != old_id:
            if inspect(failed, required=False) is not None:
                raise NotReady("failed-container quarantine name occupied")
            if (current.get("State") or {}).get("Running"):
                docker("stop", "--time", "30" if original == CLIENT else "120", original)
            docker("rename", original, failed)
            actions.append({"container_id": current["Id"], "action": "quarantined", "name": failed})
        archived = inspect(archive, required=False)
        current = inspect(original, required=False)
        if archived is not None:
            if archived["Id"] != old_id or current is not None:
                raise NotReady("old archive rollback identity differs")
            docker("rename", archive, original)
            actions.append({"container_id": old_id, "action": "restored_name", "name": original})
    db = inspect(DB)
    client = inspect(CLIENT)
    if db["Id"] != OLD_DB_ID or client["Id"] != OLD_CLIENT_ID:
        raise NotReady("rollback old identities differ")
    if not (db.get("State") or {}).get("Running"):
        docker("start", DB)
    if not (client.get("State") or {}).get("Running"):
        docker("start", CLIENT)
    db = inspect(DB)
    client = inspect(CLIENT)
    exact_projection(db, "database", None)
    exact_projection(client, "client", None)
    db_state = proc_state(db)
    client_state = proc_state(client)
    idle = client_idle(client, client_state)
    pg_paths = postgres_blkio_paths()
    if "/docker/" + OLD_DB_ID in pg_paths:
        raise NotReady("rollback database target is a PostgreSQL cgroup")
    cg = BLKIO_ROOT / OLD_DB_ID
    if not cg.is_dir() or cg.is_symlink():
        raise NotReady("rollback DB blkio cgroup differs")
    read = throttle(cg / "blkio.throttle.read_bps_device")
    write = throttle(cg / "blkio.throttle.write_bps_device")
    return {"schema_version": "mysql_tpcc_recreate_zero_swap_rollback_v11", "status": "PASS_OLD_CONTAINERS_RESTORED_NO_DELETION", "actions": actions, "database_container_id": OLD_DB_ID, "client_container_id": OLD_CLIENT_ID, "database_pid": db_state["pid"], "client_pid": client_state["pid"], "client_idle_evidence": idle, "blkio_read_reapply": read, "blkio_write_reapply": write, "replacement_containers_deleted": False, "postgresql_mutation_performed": False, "workload_started": False, "accepted_rounds_credit": 0}


def main():
    if socket.gethostname().split(".")[0] != "node04" or os.geteuid() != 0:
        raise NotReady("node04 root required")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preflight", "adopt", "rollback"))
    parser.add_argument("--before", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "preflight":
        value = preflight()
    elif args.action == "adopt":
        if args.before is None or args.output.resolve(strict=False) != TARGET:
            raise NotReady("adopt inputs/output path differ")
        value = adopt(args.before)
    else:
        value = rollback()
    atomic(args.output, value)
    print(json.dumps({"status": value["status"], "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (NotReady, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print("NOT_READY: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
