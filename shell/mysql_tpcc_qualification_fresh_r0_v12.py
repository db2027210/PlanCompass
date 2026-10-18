#!/usr/bin/env python3
"""TPC-C v12 qualification then fresh-R0 one-click controller."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib/mysql_tpcc_inventory_controller_v1.py"
TPCC_ROOT = Path("/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729")
ATTEMPT = TPCC_ROOT / "tpcc-v16-7e2e13aa-20260801t121500p08"
ADOPTED = ATTEMPT / "adopted_service.recreated_swappiness0_v3.json"
ADOPTED_SHA = "b9fcd51325ea80d4284df5cf3c9df81fd022a79df5fa3002d2635fe7a8765001"
DB_ID = "fc87aaf6a954e0d5131ff695ca808247c906525af9b4fad18c94aa6387c55948"
CLIENT_ID = "51c437f082bec5a7f95c7f0ce9a54bf549d87db7aa41a45bcec69390de6801a4"
DB_PID = 100769
CLIENT_PID = 100845
DB_START = 268728753
CLIENT_START = 268728786
DB_CGROUP_SHA = "0ac085e5b6b435d7610695e5fcb2c5e3b97f2e720b845d38565d6cdfde3bae3a"
CLIENT_CGROUP_SHA = "895eb232d02a68f416ae00a73e413ae43d399f8620ba4ff11be045ebee405d8d"
DB_NAME = "ra_mysql_tpcc_v2_db_tpcc-v16-7e2e13aa-20260801t121500p08"
CLIENT_NAME = "ra_mysql_tpcc_v2_client_tpcc-v16-7e2e13aa-20260801t121500p08"
DB_CPUS = "21-24,57-60"
CLIENT_CPUS = "8-15,44-51"
TPCC_ID = "7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77"
TPCC_MANIFEST_SHA = "e9d5e2213a14d2289c07063c0b24dfb68d6059f068c5c91388862f8a873b4cba"
COLLECTOR_ID = "f5a7a688f1c981b6c92e6dae0cf6aac580d0326f7f4d1f46c7493af993bc8a23"
COLLECTOR_MANIFEST_SHA = "842fbef539b8c2f42805ed301a41b3f75900b927074b4f9c625a406109dbe4bc"
PORTABLE_TYPED_ID = "1878a2ca8b68e1b3917021499cda396e23f60a23baacd299496c78346e29c86c"
SOCKET = "unix:///run/ra-docker-mysql-tpcc-v1.sock"
PINNED = TPCC_ROOT / "runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11"
MEMORY_ROOT = Path("/sys/fs/cgroup/memory/docker")
BLKIO_ROOT = Path("/sys/fs/cgroup/blkio/docker")
DEVICE = "259:2"
BPS = 1205862400
DURATION = 180
SAMPLES = 181


class NotReady(RuntimeError):
    pass


def module():
    spec = importlib.util.spec_from_file_location("tpcc_v12_library", LIB)
    if spec is None or spec.loader is None:
        raise NotReady("sealed controller library cannot be loaded")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


L = module()


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_sealed(path, label):
    path = Path(path).resolve(strict=True)
    sidecar = Path(str(path) + ".sha256").resolve(strict=True)
    if path.is_symlink() or sidecar.is_symlink() or sidecar.read_text(encoding="ascii") != sha(path) + "  " + path.name + "\n":
        raise NotReady(label + " seal differs")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise NotReady(label + " object differs")
    return path, value


def write_sealed(path, value, mode=0o400):
    path = Path(path)
    raw = canonical(value)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sidecar = Path(str(path) + ".sha256")
    with sidecar.open("x", encoding="ascii") as stream:
        stream.write(hashlib.sha256(raw).hexdigest() + "  " + path.name + "\n")
    os.chmod(path, mode)
    os.chmod(sidecar, mode)
    return hashlib.sha256(raw).hexdigest()


def verify_self():
    manifest_path, manifest = load_sealed(ROOT / "bundle_manifest.json", "v12 manifest")
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if ROOT.name != bundle_id or hashlib.sha256(canonical(unsigned)).hexdigest() != bundle_id or manifest.get("formal_scope") != "mysql_only" or manifest.get("accepted_rounds_credit") != 0:
        raise NotReady("v12 content address/scope differs")
    for relative, want in manifest["files"].items():
        path = ROOT / relative
        if path.is_symlink() or not path.is_file() or sha(path) != want:
            raise NotReady("v12 closure differs: " + relative)
    return manifest_path, manifest


def inspect(identity):
    row = subprocess.run(["/usr/bin/docker", "--host", SOCKET, "inspect", identity], text=True, capture_output=True, timeout=20)
    try:
        values = json.loads(row.stdout)
    except json.JSONDecodeError as exc:
        raise NotReady("Docker inspect differs") from exc
    if row.returncode or len(values) != 1:
        raise NotReady("Docker inspect cardinality differs")
    return values[0]


def rate(path):
    found = []
    for line in Path(path).read_text().splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] == DEVICE:
            found.append(int(fields[1]))
    if found != [BPS]:
        raise NotReady("blkio rate differs")


def process(row, role):
    database = role == "database"
    expected_id = DB_ID if database else CLIENT_ID
    expected_pid = DB_PID if database else CLIENT_PID
    expected_start = DB_START if database else CLIENT_START
    expected_cgroup = DB_CGROUP_SHA if database else CLIENT_CGROUP_SHA
    state = row.get("State") or {}
    host = row.get("HostConfig") or {}
    pid = int(state.get("Pid") or 0)
    if row.get("Id") != expected_id or state.get("Running") is not True or pid != expected_pid:
        raise NotReady(role + " new authority identity differs")
    root = Path("/proc") / str(pid)
    raw = (root / "stat").read_text()
    start = int(raw[raw.rfind(")") + 2:].split()[19])
    cgroup = (root / "cgroup").read_text()
    if start != expected_start or hashlib.sha256(cgroup.encode()).hexdigest() != expected_cgroup:
        raise NotReady(role + " process epoch differs")
    memory = MEMORY_ROOT / expected_id
    stats = {}
    for line in (memory / "memory.stat").read_text().splitlines():
        key, value = line.split()
        stats[key] = int(value)
    expected_memory = 34359738368 if database else 8589934592
    expected_cpus = DB_CPUS if database else CLIENT_CPUS
    if host.get("MemorySwappiness") != 0 or int((memory / "memory.swappiness").read_text()) != 0 or stats.get("total_swap") != 0 or int(host.get("Memory") or 0) != expected_memory or int(host.get("MemorySwap") or 0) != expected_memory or host.get("CpusetCpus") != expected_cpus:
        raise NotReady(role + " zero-swap/resource envelope differs")
    return {"container_id": expected_id, "pid": pid, "process_start_ticks": start, "cgroup_sha256": expected_cgroup, "cpuset_cpus": expected_cpus, "memory_swappiness": 0, "total_swap_bytes": 0, "started_at": state.get("StartedAt")}


def verify_typed():
    root = ROOT / "typed_collector"
    manifest_path, manifest = load_sealed(root / "bundle_manifest.json", "typed collector")
    unsigned = dict(manifest)
    identity = unsigned.pop("bundle_id", None)
    if identity != PORTABLE_TYPED_ID or hashlib.sha256(canonical(unsigned)).hexdigest() != identity:
        raise NotReady("typed collector content address differs")
    expected = {"binding_count": 34, "required_transaction_types": ["Delivery", "NewOrder", "OrderStatus", "Payment", "StockLevel"], "sensitive_stock_level_normalized_to": "StockLevel", "qps_resource_input_prohibited": True, "jdbc_setter_type_position_required": True, "l2_transaction_rows_required": True, "l3_transaction_type_vectors_required": True}
    for key, want in expected.items():
        if manifest.get(key) != want:
            raise NotReady("typed collector contract differs: " + key)
    return {"bundle_id": identity, "manifest_sha256": sha(manifest_path), **expected}


def live():
    verify_self()
    adopted_path, adopted = load_sealed(ADOPTED, "new adopted authority")
    if sha(adopted_path) != ADOPTED_SHA or adopted.get("schema_version") != "mysql_tpcc_recreated_service_authority_v3" or adopted.get("status") != "PASS_DURABLE_ZERO_SWAP_RECREATED_IDLE_READY" or adopted.get("accepted_rounds_credit") != 0 or (adopted.get("database") or {}).get("container_id") != DB_ID or (adopted.get("client") or {}).get("container_id") != CLIENT_ID:
        raise NotReady("new adopted authority differs")
    db_row = inspect(DB_ID)
    client_row = inspect(CLIENT_ID)
    db = process(db_row, "database")
    client = process(client_row, "client")
    config = client_row.get("Config") or {}
    proc_root = Path("/proc") / str(CLIENT_PID)
    cmdline = [part.decode("utf-8", "surrogateescape") for part in (proc_root / "cmdline").read_bytes().split(b"\0") if part]
    if config.get("Entrypoint") != ["/opt/mysql-tpcc-python/bin/python3.11"] or config.get("Cmd") != ["-c", "import time; time.sleep(315360000)"] or cmdline != config["Entrypoint"] + config["Cmd"] or (proc_root / "task" / str(CLIENT_PID) / "children").read_text().split():
        raise NotReady("client is not idle Python")
    rate(BLKIO_ROOT / DB_ID / "blkio.throttle.read_bps_device")
    rate(BLKIO_ROOT / DB_ID / "blkio.throttle.write_bps_device")
    return adopted_path, adopted, db_row, client_row, db, client, verify_typed()


def resolve(identity, manifest_sha, label):
    matches = []
    for base in (TPCC_ROOT / "bundles", TPCC_ROOT / "support_bundles"):
        path = base / identity
        if path.is_dir() and not path.is_symlink():
            matches.append(path)
    if len(matches) != 1:
        raise NotReady(label + " publication cardinality differs")
    manifest_path, manifest = load_sealed(matches[0] / "bundle_manifest.json", label)
    unsigned = dict(manifest)
    if unsigned.pop("bundle_id", None) != identity or hashlib.sha256(canonical(unsigned)).hexdigest() != identity or sha(manifest_path) != manifest_sha:
        raise NotReady(label + " identity differs")
    return matches[0], manifest_path, manifest


def service_client(row, collector, collector_manifest):
    return {"name": CLIENT_NAME, "container_id": CLIENT_ID, "image_id": row["Image"], "created": row["Created"], "inspect_config_sha256": L.collector_projection_sha256(collector, collector_manifest, row)}


def materialize(output_root):
    adopted_path, adopted, db_row, client_row, db, client, typed = live()
    tpcc, tpcc_manifest_path, tpcc_manifest = resolve(TPCC_ID, TPCC_MANIFEST_SHA, "TPC-C workload bundle")
    collector, collector_manifest_path, collector_manifest = resolve(COLLECTOR_ID, COLLECTOR_MANIFEST_SHA, "dynamic collector")
    _, runtime = L.load_bundle_json(tpcc, tpcc_manifest, "distributed_runtime_contract.json", "TPC-C runtime")
    defaults = (ATTEMPT / "config/mysql.cnf").resolve(strict=True)
    measurement = (ATTEMPT / "measurement").resolve(strict=True)
    tag = "v12-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(6)
    root = Path(output_root).resolve() / tag
    root.mkdir(parents=True, mode=0o700)
    commands = {}
    authorities = {}
    args = {}
    client_binding = service_client(client_row, collector, collector_manifest)
    for phase in ("qualification", "fresh_r0"):
        attempt = "tpcc-v12-" + phase + "-" + secrets.token_hex(12)
        command = L.build_tpcc_command(tpcc, tpcc_manifest_path, tpcc_manifest, runtime, client_binding, defaults, measurement, "qualification_solo", attempt, None)
        command["v12_phase"] = phase
        command["planned_as_fresh_r0"] = phase == "fresh_r0"
        command["duration_seconds"] = DURATION
        command["expected_sample_count"] = SAMPLES
        command["accepted_rounds_credit"] = 0
        command_path = root / (phase + ".command.json")
        command_sha = write_sealed(command_path, command)
        token_path = root / (phase + ".token")
        token = secrets.token_hex(48)
        token_path.write_text(token + "\n")
        os.chmod(token_path, 0o600)
        authority = L.authority(command, command_sha, hashlib.sha256(token.encode()).hexdigest(), sha(collector_manifest_path), time.time(), 1800)
        authority["v12_phase"] = phase
        authority["raw_adapter_authority_only"] = phase == "fresh_r0"
        authority["fresh_r0_result_must_be_sealed_by_v12_controller"] = phase == "fresh_r0"
        authority["formal_r0_authorized_by_v12_controller"] = phase == "fresh_r0"
        authority["accepted_rounds_credit"] = 0
        authority_path = root / (phase + ".authority.json")
        write_sealed(authority_path, authority, 0o600)
        args[phase] = L.base_args(tpcc, DB_NAME, CLIENT_NAME, defaults) + ["--main-agent-token-file", str(token_path), "--main-agent-authority", str(authority_path), "--command-spec", str(command_path)]
        commands[phase] = str(command_path)
        authorities[phase] = str(authority_path)
    plan = {"schema_version": "mysql_tpcc_qualification_fresh_r0_plan_v12", "status": "SEALED_NOT_EXECUTED", "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True, "postgresql_mutation_prohibited": True, "new_adopted_authority_sha256": ADOPTED_SHA, "database": db, "client": client, "typed_collector": typed, "dynamic_window_seconds": DURATION, "dynamic_sample_count": SAMPLES, "qualification_must_pass_before_fresh_r0": True, "qualification_args": args["qualification"], "fresh_r0_raw_adapter_args": args["fresh_r0"], "command_paths": commands, "authority_paths": authorities, "run_root": str(root / "run"), "workload_started": False, "fresh_r0_completed": False, "formal_outer_authorized": False, "accepted_round_authorized": False, "accepted_rounds_credit": 0}
    plan_path = root / "plan.json"
    write_sealed(plan_path, plan)
    controller_token = secrets.token_hex(48)
    controller_token_path = root / "controller.token"
    controller_token_path.write_text(controller_token + "\n")
    os.chmod(controller_token_path, 0o600)
    controller_authority = {"schema_version": "mysql_tpcc_v12_controller_authority", "plan_sha256": sha(plan_path), "token_sha256": hashlib.sha256(controller_token.encode()).hexdigest(), "not_before_epoch": time.time() - 1, "expires_epoch": time.time() + 3600, "qualification_then_fresh_r0_only": True, "fresh_r0_authorized_after_qualification_pass": True, "formal_outer_authorized": False, "accepted_rounds_credit": 0}
    controller_authority_path = root / "controller.authority.json"
    write_sealed(controller_authority_path, controller_authority)
    return {"status": "PASS_V12_ONE_CLICK_MATERIALIZED_NOT_EXECUTED", "plan": str(plan_path), "authority": str(controller_authority_path), "token": str(controller_token_path), "accepted_rounds_credit": 0}


def gate(plan_path, authority_path, token_path):
    plan_path, plan = load_sealed(plan_path, "v12 plan")
    _, authority = load_sealed(authority_path, "v12 controller authority")
    token_path = Path(token_path).resolve(strict=True)
    token = token_path.read_text().strip()
    if stat.S_IMODE(token_path.stat().st_mode) != 0o600 or authority.get("plan_sha256") != sha(plan_path) or authority.get("token_sha256") != hashlib.sha256(token.encode()).hexdigest() or not float(authority.get("not_before_epoch") or 0) <= time.time() < float(authority.get("expires_epoch") or 0):
        raise NotReady("v12 controller gate differs")
    return plan_path, plan


def run_stage(plan, phase, run_root, collector, collector_manifest):
    args = plan["qualification_args"] if phase == "qualification" else plan["fresh_r0_raw_adapter_args"]
    output = run_root / (phase + ".evidence.json")
    entry = collector / "qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"
    with (run_root / (phase + ".stdout")).open("xb") as stdout, (run_root / (phase + ".stderr")).open("xb") as stderr:
        result = subprocess.run([str(PINNED), "-B", str(entry), "--qualification-solo", *args, "--output", str(output)], stdout=stdout, stderr=stderr, timeout=900)
    if result.returncode:
        raise NotReady(phase + " raw collector failed rc=" + str(result.returncode))
    _, evidence = load_sealed(output, phase + " evidence")
    dynamic = L.validate_evidence(evidence)
    if dynamic.get("sample_count") != SAMPLES or dynamic.get("pg_http_window_sample_count") != SAMPLES:
        raise NotReady(phase + " exact 181-sample closure differs")
    rows = L.workload_rows(evidence)
    if not rows:
        raise NotReady(phase + " real workload PID evidence absent")
    return output, evidence, dynamic, rows


def execute(plan_path, authority_path, token_path):
    _, plan = gate(plan_path, authority_path, token_path)
    if plan.get("new_adopted_authority_sha256") != ADOPTED_SHA or plan.get("qualification_must_pass_before_fresh_r0") is not True or plan.get("accepted_rounds_credit") != 0:
        raise NotReady("v12 plan lineage differs")
    collector, _, collector_manifest = resolve(COLLECTOR_ID, COLLECTOR_MANIFEST_SHA, "dynamic collector")
    live()
    run_root = Path(plan["run_root"])
    run_root.mkdir(parents=True, mode=0o700)
    try:
        qualification_output, _, qualification_dynamic, qualification_pids = run_stage(plan, "qualification", run_root, collector, collector_manifest)
        qualification = {"schema_version": "mysql_tpcc_v12_qualification_completion", "status": "PASS_QUALIFICATION_EXACT_180S_181_SAMPLES_ZERO_CREDIT", "evidence_sha256": sha(qualification_output), "dynamic_gate": qualification_dynamic, "real_workload_pids": qualification_pids, "fresh_r0_authorized_next": True, "accepted_rounds_credit": 0}
        qualification_path = run_root / "qualification.completion.json"
        write_sealed(qualification_path, qualification)
        _, adopted, _, _, db, client, typed = live()
        r0_output, _, r0_dynamic, r0_pids = run_stage(plan, "fresh_r0", run_root, collector, collector_manifest)
        _, adopted_after, _, _, db_after, client_after, _ = live()
        if db_after != db or client_after != client or adopted_after != adopted:
            raise NotReady("fresh R0 changed new service authority epoch")
        r0 = {"schema_version": "mysql_tpcc_fresh_r0_v12", "status": "PASS_FRESH_R0_EXACT_180S_181_SAMPLES_OUTER_BLOCKED_ZERO_CREDIT", "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True, "postgresql_mutation_prohibited": True, "qualification_completion_sha256": sha(qualification_path), "fresh_r0_phase_authority_sha256": sha(plan["authority_paths"]["fresh_r0"]), "raw_measurement_evidence_sha256": sha(r0_output), "raw_adapter_is_authority": False, "dynamic_gate": r0_dynamic, "real_workload_pids": r0_pids, "database": db_after, "client": client_after, "typed_collector": typed, "capture_result_is_r0": False, "fresh_r0_result": True, "formal_outer_authorized": False, "accepted_round_authorized": False, "accepted_rounds_credit": 0}
        r0_path = run_root / "fresh_r0.json"
        write_sealed(r0_path, r0)
        completion = {"schema_version": "mysql_tpcc_v12_one_click_completion", "status": "PASS_QUALIFICATION_AND_FRESH_R0_ZERO_CREDIT", "qualification_sha256": sha(qualification_path), "fresh_r0_sha256": sha(r0_path), "formal_outer_authorized": False, "accepted_round_authorized": False, "accepted_rounds_credit": 0}
        completion_path = run_root / "completion.json"
        write_sealed(completion_path, completion)
        return {**completion, "completion": str(completion_path), "fresh_r0": str(r0_path)}
    except BaseException as exc:
        failure = run_root / "failure.json"
        if not failure.exists():
            write_sealed(failure, {"status": "FAIL_CLOSED_ZERO_CREDIT", "error": str(exc), "formal_outer_authorized": False, "accepted_rounds_credit": 0})
        raise


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    materialize_parser = sub.add_parser("materialize")
    materialize_parser.add_argument("--output-root", type=Path, required=True)
    execute_parser = sub.add_parser("execute")
    execute_parser.add_argument("--plan", type=Path, required=True)
    execute_parser.add_argument("--authority", type=Path, required=True)
    execute_parser.add_argument("--token", type=Path, required=True)
    one = sub.add_parser("one-click")
    one.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    if socket.gethostname().split(".")[0] != "node04" or os.geteuid() != 0 or Path(sys.executable).resolve() != PINNED.resolve(strict=True):
        raise NotReady("node04 root pinned TPCC Python required")
    if args.action == "materialize":
        value = materialize(args.output_root)
    elif args.action == "execute":
        value = execute(args.plan, args.authority, args.token)
    else:
        materialized = materialize(args.output_root)
        value = execute(Path(materialized["plan"]), Path(materialized["authority"]), Path(materialized["token"]))
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (NotReady, L.MaterializationError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print("NOT_READY: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
