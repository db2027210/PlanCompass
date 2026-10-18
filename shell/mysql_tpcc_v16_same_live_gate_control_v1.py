#!/usr/bin/env python3
"""Fail-closed TPC-C v16 same-live typed-capture gate.

``snapshot`` performs read-only Docker/proc/mount inspection.  ``evaluate``
binds that snapshot to the completed qualification ledger, four fresh shared
collector flows, their bidirectional gate, and the sealed 34-binding manifest.
Neither mode launches a workload, changes a container, grants a formal launch,
or assigns accepted-round credit.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
from typing import Any, Mapping


BASE_ID = "7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77"
BASE_MANIFEST_SHA = "e9d5e2213a14d2289c07063c0b24dfb68d6059f068c5c91388862f8a873b4cba"
BINDING_SHA = "689d4bc1558d14042c3e43a2267d3cc332d71ba567c635d92180a189c3f90182"
ATTEMPT = "tpcc-v16-7e2e13aa-20260801t121500p08"
HOSTNAME = "node04"
DB_CPUSET = "21-24,57-60"
CLIENT_CPUSET = "8-15,44-51"
DEVICE = "/dev/nvme3n1"
MAJOR_MINOR = "259:2"
PORT = 65106
DOCKER = ("docker", "--host", "unix:///run/ra-docker-mysql-tpcc-v1.sock")
HEX = re.compile(r"^[0-9a-f]{64}$")
FLOW_LABELS = ("A_solo", "A_paired", "B_solo", "B_paired")
TX = {"Delivery", "NewOrder", "OrderStatus", "Payment", "StockLevel"}
QPS_KEYS = {"qps", "throughput", "transactions_per_second", "tps"}


class GateError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def regular(path: Path, label: str) -> Path:
    try:
        path = Path(path).resolve(strict=True)
    except OSError as exc:
        raise GateError(label + " is absent") from exc
    if path.is_symlink() or not path.is_file():
        raise GateError(label + " must be a regular file")
    return path


def load(path: Path, label: str, sidecar: bool = True) -> tuple[Path, dict[str, Any]]:
    path = regular(path, label)
    if sidecar:
        check_sidecar(path, label)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(label + " JSON differs") from exc
    if not isinstance(value, dict):
        raise GateError(label + " must contain one object")
    return path, value


def check_sidecar(path: Path, label: str) -> None:
    sidecar = regular(Path(str(path) + ".sha256"), label + " sidecar")
    if sidecar.read_text(encoding="ascii") != sha(path) + "  " + path.name + "\n":
        raise GateError(label + " sidecar differs")


def reject_qps(value: Any, location: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in QPS_KEYS or lowered.endswith("_qps"):
                raise GateError("QPS/throughput resource input in " + location)
            reject_qps(item, location + "." + str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_qps(item, "%s[%d]" % (location, index))


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for token in str(value).split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            low, high = [int(part) for part in token.split("-", 1)]
            if low > high:
                raise GateError("invalid CPU range")
            result.update(range(low, high + 1))
        else:
            result.add(int(token))
    if not result:
        raise GateError("empty CPU set")
    return result


def verify_bundle(bundle: Path) -> tuple[Path, dict[str, Any]]:
    bundle = Path(bundle).resolve(strict=True)
    path, manifest = load(bundle / "bundle_manifest.json", "base manifest")
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (bundle.name != BASE_ID or manifest.get("bundle_id") != BASE_ID
            or sha(path) != BASE_MANIFEST_SHA
            or sha_bytes(canonical(unsigned)) != BASE_ID
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True
            or manifest.get("postgresql_mutation_prohibited") is not True
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False):
        raise GateError("base bundle identity/scope differs")
    for relative, expected in (manifest.get("files") or {}).items():
        member = bundle / relative
        if member.is_symlink() or not member.is_file() or sha(member) != expected:
            raise GateError("base bundle closure differs: " + relative)
    return bundle, manifest


def verify_binding(path: Path) -> dict[str, Any]:
    path, value = load(path, "typed binding manifest", sidecar=False)
    if sha(path) != BINDING_SHA or value.get("schema_version") != "mysql_tpcc_jdbc_typed_binding_manifest_v1":
        raise GateError("typed binding manifest identity differs")
    statements = value.get("statements")
    if value.get("binding_count") != 34 or not isinstance(statements, list) or len(statements) != 34:
        raise GateError("typed binding count differs")
    if set(value.get("transaction_types") or []) != TX or value.get("transaction_aliases") != {"SensitiveStockLevel": "StockLevel"}:
        raise GateError("five transaction types or SensitiveStockLevel alias differs")
    for index, row in enumerate(statements, 1):
        if (not isinstance(row, dict) or row.get("order") != index
                or not isinstance(row.get("binding_id"), str)
                or not HEX.fullmatch(str(row.get("template_sql_sha256") or ""))
                or not isinstance(row.get("parameter_count"), int)
                or row.get("parameter_positions") != list(range(1, row["parameter_count"] + 1))):
            raise GateError("typed setter/type/position/SQL binding differs at %d" % index)
    sensitive = statements[-1]
    if (sensitive.get("binding_id") != "34:SensitiveStockLevel:SensitiveStockLevel/item-in-orderline-w1"
            or sensitive.get("transaction_type") != "SensitiveStockLevel"):
        raise GateError("SensitiveStockLevel binding differs")
    reject_qps(value, "typed binding manifest")
    return value


def process_start_ticks(pid: int) -> int:
    raw = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
    return int(raw[raw.rfind(")") + 2:].split()[19])


def proc_status(pid: int) -> dict[str, str]:
    result = {}
    for line in (Path("/proc") / str(pid) / "status").read_text().splitlines():
        if ":" in line:
            key, value = line.split(":", 1); result[key] = value.strip()
    return result


def proc_cgroup(pid: int) -> str:
    return (Path("/proc") / str(pid) / "cgroup").read_text(encoding="utf-8")


def docker_inspect(identifier: str) -> dict[str, Any]:
    if not HEX.fullmatch(str(identifier)):
        raise GateError("full lowercase container ID required")
    completed = subprocess.run([*DOCKER, "inspect", identifier], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False)
    if completed.returncode != 0:
        raise GateError("read-only Docker inspect failed")
    try: rows = json.loads(completed.stdout)
    except json.JSONDecodeError as exc: raise GateError("Docker inspect JSON differs") from exc
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise GateError("Docker inspect cardinality differs")
    return rows[0]


def service_identity(service: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if (service.get("schema_version") != "mysql_tpcc_node04_adopted_service_authority_v2"
            or service.get("status") != "PASS" or service.get("formal_scope") != "mysql_only"
            or service.get("cross_engine_comparison_prohibited") is not True
            or service.get("bundle_id") != BASE_ID or service.get("attempt_id") != ATTEMPT
            or service.get("formal_launch_authorized") is not False
            or service.get("accepted_round_authorized") is not False):
        raise GateError("adopted-service authority differs")
    database, client = service.get("database"), service.get("client")
    if not isinstance(database, dict) or not isinstance(client, dict):
        raise GateError("adopted-service identities are absent")
    for label, row in (("database", database), ("client", client)):
        if not HEX.fullmatch(str(row.get("container_id") or "")):
            raise GateError(label + " adopted full ID differs")
    return database, client


def verify_ledger(value: dict[str, Any], database: Mapping[str, Any]) -> dict[str, Any]:
    if (value.get("schema_version") != "mysql_tpcc_same_live_container_formal10_handoff_v2"
            or value.get("status") != "INCOMPLETE" or value.get("formal_scope") != "mysql_only"
            or value.get("cross_engine_comparison_prohibited") is not True
            or value.get("bundle_id") != BASE_ID or value.get("attempt_id") != ATTEMPT
            or value.get("accepted_round_authorized") is not False
            or value.get("container_recreated") is not False
            or value.get("datadir_replaced") is not False
            or value.get("database_restarted") is not False):
        raise GateError("qualification ledger continuity differs")
    phases = value.get("phases")
    if not isinstance(phases, dict) or set(phases) != {"qualification"}:
        raise GateError("ledger must contain only completed qualification")
    phase = phases["qualification"]
    if (not isinstance(phase, dict) or phase.get("status") != "PASS"
            or phase.get("attempt_id") != ATTEMPT or phase.get("bundle_id") != BASE_ID
            or phase.get("client_target_device_io_zero_verified") is not True
            or phase.get("client_target_device_io_delta") != {"rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0, "dbytes": 0, "dios": 0}):
        raise GateError("qualification phase did not pass fail-closed gates")
    for field in ("container_id", "pid", "process_start_ticks", "datadir"):
        wanted = database.get(field)
        if value.get("db_" + field) != wanted or phase.get("db_" + field) != wanted:
            raise GateError("qualification database continuity differs: " + field)
    observed = phase.get("observed_epoch_ms")
    if not isinstance(observed, int) or observed <= 0:
        raise GateError("qualification timestamp differs")
    reject_qps(value, "qualification ledger")
    return phase


def validate_inspect(row: dict[str, Any], authority: Mapping[str, Any], cpuset: str,
                     label: str) -> dict[str, Any]:
    state, host = row.get("State") or {}, row.get("HostConfig") or {}
    pid = int(state.get("Pid") or 0)
    if (row.get("Id") != authority.get("container_id") or state.get("Running") is not True
            or pid <= 1 or pid != int(authority.get("pid") or 0)
            or cpus(host.get("CpusetCpus", "")) != cpus(cpuset)):
        raise GateError(label + " live inspect identity/envelope differs")
    status = proc_status(pid)
    if cpus(status.get("Cpus_allowed_list", "")) != cpus(cpuset):
        raise GateError(label + " live process affinity differs")
    ticks = process_start_ticks(pid)
    if int(authority.get("process_start_ticks") or ticks) != ticks:
        raise GateError(label + " process start ticks differ")
    return {"container_id": row["Id"], "pid": pid, "process_start_ticks": ticks,
            "started_at": state.get("StartedAt"), "cpuset_cpus": cpuset,
            "cgroup_sha256": sha_bytes(proc_cgroup(pid).encode("utf-8")),
            "inspect_sha256": sha_bytes(canonical(row))}


def snapshot(args: argparse.Namespace) -> dict[str, Any]:
    verify_bundle(args.bundle)
    service_path, service = load(args.adopted_service, "adopted service")
    ledger_path, ledger = load(args.phase_ledger, "phase ledger")
    database, client = service_identity(service)
    qualification = verify_ledger(ledger, database)
    db_row = docker_inspect(database["container_id"])
    client_row = docker_inspect(client["container_id"])
    db = validate_inspect(db_row, database, DB_CPUSET, "database")
    cl = validate_inspect(client_row, client, CLIENT_CPUSET, "client")
    command = (db_row.get("Config") or {}).get("Cmd") or []
    if ((db_row.get("HostConfig") or {}).get("NetworkMode") != "host"
            or "--port=%d" % PORT not in command):
        raise GateError("database port/network readback differs")
    mounts = [row for row in db_row.get("Mounts") or [] if row.get("Destination") == "/var/lib/mysql"]
    if len(mounts) != 1 or not isinstance(mounts[0].get("Source"), str):
        raise GateError("database datadir mount differs")
    source = Path(mounts[0]["Source"]).resolve(strict=True)
    major_minor = "%d:%d" % (os.major(source.stat().st_dev), os.minor(source.stat().st_dev))
    if major_minor != MAJOR_MINOR:
        raise GateError("database datadir device differs")
    probe = socket.socket(); probe.settimeout(1.0)
    try: result = probe.connect_ex(("127.0.0.1", PORT))
    finally: probe.close()
    if result != 0:
        raise GateError("MySQL port is not reachable")
    value = {"schema_version":"mysql_tpcc_v16_same_live_readback_v1",
        "status":"PASS_READ_ONLY_LIVE_IDENTITY_NOT_CAPTURE_AUTHORITY",
        "formal_scope":"mysql_only", "cross_engine_comparison_prohibited":True,
        "postgresql_mutation_prohibited":True, "hostname":socket.gethostname(),
        "collected_at_utc":datetime.now(timezone.utc).isoformat(),
        "attempt_id":ATTEMPT, "bundle_id":BASE_ID,
        "database":dict(db, datadir=str(source), device=DEVICE, device_major_minor=major_minor, mysql_port=PORT),
        "client":cl, "qualification_observed_epoch_ms":qualification["observed_epoch_ms"],
        "inputs":{"adopted_service_sha256":sha(service_path), "phase_ledger_sha256":sha(ledger_path)},
        "capture_authorized":False,"formal_r0_authorized":False,
        "formal_launch_authorized":False,"accepted_round_authorized":False,
        "accepted_rounds_credit":0}
    reject_qps(value, "snapshot")
    return publish(args.output_root, "same_live_snapshot.json", value)


def verify_flow(path: Path, label: str) -> tuple[dict[str, Any], str]:
    path, value = load(path, label)
    mode = "qualification_solo" if label.endswith("solo") else "qualification_paired"
    if (value.get("schema_version") != "mysql_algorithm_c_debug_qualification_evidence_v2"
            or value.get("status") != "PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY"
            or value.get("mode") != mode or value.get("duration_seconds") != 180
            or int(value.get("minimum_samples") or 0) < 181
            or float(value.get("maximum_sample_gap_seconds") or 99) > 3.5
            or value.get("debug_only") is not True
            or any(value.get(key) is not False for key in ("capture_authorized", "formal_r0_authorized", "formal_authorized", "accepted_round_authorized"))):
        raise GateError(label + " qualification evidence differs")
    if not isinstance(value.get("summary"), dict) or not isinstance(value.get("samples"), list) or len(value["samples"]) < 181:
        raise GateError(label + " samples/summary incomplete")
    for sample in value["samples"]:
        streams = sample.get("required_streams") if isinstance(sample, dict) else None
        if not isinstance(streams, dict) or not isinstance(streams.get("database_counters"), dict):
            raise GateError(label + " database counter sample absent")
    reject_qps(value, label)
    return value, sha(path)


def verify_dynamic(gate_path: Path, raw_path: Path, flows: Mapping[str, tuple[dict[str, Any], str]], snapshot_value: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    gate_path, gate = load(gate_path, "dynamic gate")
    raw_path, raw = load(raw_path, "dynamic raw closure")
    if (gate.get("schema_version") != "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1"
            or gate.get("status") != "PASS" or gate.get("scenario") != "tpcc_cpu"
            or gate.get("host_id") != HOSTNAME or gate.get("formal_scope") != "mysql_only"
            or gate.get("cross_engine_comparison_prohibited") is not True
            or gate.get("accepted_round_authorized") is not False
            or gate.get("duration_seconds", 0) < 180
            or gate.get("minimum_samples_per_stream", 0) < 181
            or gate.get("maximum_sample_gap_seconds", 99) > 3.5
            or gate.get("database_counter_missing_samples") != 0
            or gate.get("database_counter_timeout_count") != 0
            or gate.get("all_tolerances_passed") is not True
            or gate.get("both_directions_same_version_verified") is not True):
        raise GateError("dynamic bidirectional gate differs")
    directions = gate.get("directions")
    if (not isinstance(directions, dict) or len(directions) != 2
            or any(row.get("passed") is not True or row.get("foreign_device_io_attribution_bytes") != 0 for row in directions.values())):
        raise GateError("dynamic directions did not pass")
    try: expires = datetime.fromisoformat(str(gate["expires_at_utc"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc: raise GateError("dynamic gate expiry differs") from exc
    if expires <= datetime.now(timezone.utc):
        raise GateError("dynamic gate is stale")
    inputs = raw.get("inputs")
    if raw.get("schema_version") != "mysql_algorithm_c_dynamic_gate_raw_evidence_closure_v1" or not isinstance(inputs, dict) or set(inputs) != set(FLOW_LABELS):
        raise GateError("dynamic raw four-flow closure differs")
    for label, (_value, digest) in flows.items():
        item = inputs[label]
        if not isinstance(item, dict) or item.get("sha256") != digest or item.get("evidence") != _value:
            raise GateError("dynamic raw input differs: " + label)
    raw_refs = gate.get("raw_artifacts") or {}
    if raw_refs.get(raw_path.name) != sha(raw_path):
        raise GateError("dynamic gate/raw hash binding differs")
    tpcc = [value for value, _digest in flows.values() if value.get("scenario") == "tpcc_cpu"]
    if len(tpcc) != 2 or {row.get("mode") for row in tpcc} != {"qualification_solo", "qualification_paired"}:
        raise GateError("four flows lack current TPC-C solo/paired evidence")
    db = snapshot_value["database"]
    for row in tpcc:
        initial = row.get("initial_live_readback") or {}
        container = initial.get("container") or {}
        storage = initial.get("storage") or {}
        envelope = initial.get("resource_envelope") or {}
        if (row.get("bundle_id") != BASE_ID or initial.get("bundle_id") != BASE_ID
                or initial.get("hostname") != HOSTNAME or initial.get("formal_scope") != "mysql_only"
                or container.get("container_id") != db["container_id"] or int(container.get("pid") or 0) != db["pid"]
                or storage.get("device") != DEVICE or storage.get("major_minor") != MAJOR_MINOR
                or cpus(envelope.get("cpuset_cpus", "")) != cpus(DB_CPUSET)
                or int(envelope.get("mysql_port") or 0) != PORT):
            raise GateError("TPC-C flow is not bound to current same-live v16")
    collectors = {value.get("collector_sha256") for value, _digest in flows.values()}
    if len(collectors) != 1 or not HEX.fullmatch(str(next(iter(collectors)) or "")):
        raise GateError("four flows did not use one collector")
    reject_qps(gate, "dynamic gate"); reject_qps(raw, "dynamic raw closure")
    return gate, raw


def verify_snapshot(value: dict[str, Any], service: dict[str, Any], ledger: dict[str, Any]) -> None:
    database, client = service_identity(service); qualification = verify_ledger(ledger, database)
    if (value.get("schema_version") != "mysql_tpcc_v16_same_live_readback_v1"
            or value.get("status") != "PASS_READ_ONLY_LIVE_IDENTITY_NOT_CAPTURE_AUTHORITY"
            or value.get("hostname") != HOSTNAME or value.get("bundle_id") != BASE_ID or value.get("attempt_id") != ATTEMPT
            or value.get("formal_scope") != "mysql_only" or value.get("cross_engine_comparison_prohibited") is not True
            or value.get("postgresql_mutation_prohibited") is not True
            or value.get("qualification_observed_epoch_ms") != qualification["observed_epoch_ms"]
            or value.get("accepted_rounds_credit") != 0):
        raise GateError("same-live snapshot authority differs")
    try: collected = datetime.fromisoformat(str(value["collected_at_utc"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc: raise GateError("same-live snapshot time differs") from exc
    age = (datetime.now(timezone.utc) - collected).total_seconds()
    if age < -5 or age > 120:
        raise GateError("same-live snapshot is stale; recollect before each gate")
    db, cl = value.get("database") or {}, value.get("client") or {}
    if (db.get("container_id") != database.get("container_id") or db.get("pid") != database.get("pid")
            or db.get("process_start_ticks") != database.get("process_start_ticks")
            or db.get("cpuset_cpus") != DB_CPUSET or db.get("device") != DEVICE
            or db.get("device_major_minor") != MAJOR_MINOR or db.get("mysql_port") != PORT
            or cl.get("container_id") != client.get("container_id") or cl.get("pid") != client.get("pid")
            or cl.get("process_start_ticks") != client.get("process_start_ticks")
            or cl.get("cpuset_cpus") != CLIENT_CPUSET):
        raise GateError("same-live snapshot identity/resource differs")


def verify_collector(value: dict[str, Any]) -> None:
    l2, l3 = value.get("l2_transactions"), value.get("l3_vectors")
    if (value.get("schema_version") != "mysql_tpcc_typed_binding_l2_l3_v1"
            or value.get("manifest_sha256") != BINDING_SHA
            or value.get("correctness_verified") is not True
            or value.get("binding_count") != 34 or value.get("observed_binding_count") != 34
            or set(value.get("transaction_types") or []) != TX or value.get("qps_included") is not False
            or not isinstance(l2, list) or not isinstance(l3, dict) or set(l3) != TX):
        raise GateError("typed collector 34/34 five-transaction L2/L3 differs")
    observed = {statement.get("binding_id") for tx in l2 if isinstance(tx, dict)
                for statement in tx.get("statements", []) if isinstance(statement, dict)}
    if "34:SensitiveStockLevel:SensitiveStockLevel/item-in-orderline-w1" not in observed:
        raise GateError("SensitiveStockLevel normalization evidence absent")
    for tx in l2:
        if not isinstance(tx, dict) or tx.get("transaction_type") not in TX:
            raise GateError("typed L2 transaction differs")
        for statement in tx.get("statements", []):
            bindings = statement.get("typed_bindings")
            if (not isinstance(bindings, list)
                    or [item.get("position") for item in bindings]
                    != list(range(1, len(bindings) + 1))
                    or any(not isinstance(item.get("setter"), str)
                           or not isinstance(item.get("jdbc_type"), str)
                           for item in bindings)
                    or not isinstance(statement.get("binding_id"), str)):
                raise GateError("setter/type/position/SQL hash evidence incomplete")
    if len(observed) != 34:
        raise GateError("collector does not close all 34 manifest-bound SQL hashes")
    reject_qps(value, "collector validation")


def publish(root: Path, filename: str, value: dict[str, Any]) -> dict[str, Any]:
    identity = sha_bytes(canonical(value)); root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=True)
    target = root / identity
    if target.exists() or target.is_symlink():
        existing_path, existing = load(target / filename, "existing authority")
        if existing != value: raise GateError("existing content-addressed authority differs")
        return {"status":"PASS_IDEMPOTENT", "authority":str(existing_path), "authority_id":identity}
    stage = root / (".stage-" + identity + "-" + secrets.token_hex(6)); stage.mkdir(mode=0o700)
    try:
        output = stage / filename; output.write_bytes(canonical(value)); os.chmod(output, 0o444)
        sidecar = Path(str(output) + ".sha256"); sidecar.write_text(sha(output) + "  " + output.name + "\n", encoding="ascii"); os.chmod(sidecar, 0o444)
        os.rename(stage, target)
    except BaseException:
        if stage.exists(): shutil.rmtree(stage)
        raise
    return {"status":value["status"], "authority":str(target / filename), "authority_id":identity,
            "capture_authorized":value.get("capture_authorized", False), "accepted_rounds_credit":0}


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    verify_bundle(args.bundle); binding = verify_binding(args.binding_manifest)
    service_path, service = load(args.adopted_service, "adopted service")
    ledger_path, ledger = load(args.phase_ledger, "phase ledger")
    snapshot_path, snap = load(args.same_live_snapshot, "same-live snapshot")
    verify_snapshot(snap, service, ledger)
    flows = {label: verify_flow(getattr(args, label.lower().replace("_", "_")), label) for label in FLOW_LABELS}
    gate, raw = verify_dynamic(args.dynamic_gate, args.dynamic_gate_raw, flows, snap)
    postcapture = args.collector_validation is not None
    collector_sha = None
    if postcapture:
        collector_path, collector = load(args.collector_validation, "collector validation", sidecar=False)
        verify_collector(collector); collector_sha = sha(collector_path)
    status = ("PASS_SAME_LIVE_REGISTRY_READY_ZERO_CREDIT" if postcapture
              else "PASS_SAME_LIVE_TYPED_CAPTURE_READY_ZERO_CREDIT")
    value = {"schema_version":"mysql_tpcc_v16_same_live_typed_gate_authority_v1",
        "status":status,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,
        "postgresql_mutation_prohibited":True,"attempt_id":ATTEMPT,"bundle_id":BASE_ID,
        "same_live_identity":{"database":snap["database"],"client":snap["client"]},
        "qualification_continuity_verified":True,"current_four_flow_dynamic_gate_verified":True,
        "binding_count":34,"transaction_types":sorted(TX),
        "sensitive_stock_level_normalized_to":"StockLevel",
        "setter_type_parameter_position_sql_hash_required":True,
        "l2_l3_required":True,"collector_validation_sha256":collector_sha,
        "registry_ready":postcapture,"qps_resource_input_prohibited":True,
        "inputs":{"base_manifest_sha256":BASE_MANIFEST_SHA,"binding_manifest_sha256":BINDING_SHA,
            "adopted_service_sha256":sha(service_path),"phase_ledger_sha256":sha(ledger_path),
            "same_live_snapshot_sha256":sha(snapshot_path),"dynamic_gate_sha256":sha(args.dynamic_gate),
            "dynamic_gate_raw_sha256":sha(args.dynamic_gate_raw),
            "flows":{label:digest for label, (_value,digest) in flows.items()}},
        "capture_authorized":not postcapture,"formal_r0_authorized":False,
        "formal_launch_authorized":False,"accepted_round_authorized":False,
        "accepted_rounds_credit":0,"capture_r0_failed_replay_credit":0,
        "algorithm_c_unchanged":True}
    reject_qps(value, "output authority")
    return publish(args.output_root, "typed_gate_authority.json", value)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__); sub = root.add_subparsers(dest="mode", required=True)
    snap = sub.add_parser("snapshot"); snap.add_argument("--bundle", type=Path, required=True); snap.add_argument("--adopted-service", type=Path, required=True); snap.add_argument("--phase-ledger", type=Path, required=True); snap.add_argument("--output-root", type=Path, required=True)
    gate = sub.add_parser("evaluate")
    for name in ("bundle","adopted_service","phase_ledger","same_live_snapshot","binding_manifest","dynamic_gate","dynamic_gate_raw","a_solo","a_paired","b_solo","b_paired","output_root"):
        gate.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    gate.add_argument("--collector-validation", type=Path)
    return root


def main() -> int:
    args = parser().parse_args()
    result = snapshot(args) if args.mode == "snapshot" else evaluate(args)
    print(json.dumps(result, indent=2, sort_keys=True)); return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (GateError, OSError, ValueError, subprocess.SubprocessError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc); raise SystemExit(2)
