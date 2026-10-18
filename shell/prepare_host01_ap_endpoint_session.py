#!/usr/bin/env python3
"""Default-dry-run controller for the AP 17-endpoint estimator-T session.

This preparation revision deliberately seals the mutating execute branch until
the controller is given exact v5 request/runtime hashes and a later main-
controller release.  Dry-run does not connect to MySQL or start a container.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
HOST = "anonymous@192.0.2.3"
PROJECT = "/mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/hybench_ap"
DATADIR = PROJECT + "/production_binding_trace_20260723_0630/datadir"
TEMPLATE = PROJECT + "/mysql_template_data"
LANE_LOCK = PROJECT + "/locks/host01_mysql_lane.lock"
SERIAL_LOCK = PROJECT + "/locks/ap_estimator_t_endpoint.lock"
IMAGE = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
ENVELOPE = ROOT / "history/mysql_sgd_env_agents_20260723/ap/formal_mysql_normalized_envelope_readback.json"
NATIVE_GATE = ROOT / "history/mysql_sgd_env_agents_20260723/ap/native_mysqld_continuous_gate_20260723_0707.json"
COLLECTOR = ROOT / "shell/collect_mysql_trained_estimator_endpoint_evidence.py"
EXPECTED_ENVELOPE = {
    "cpu_quota_cores": 3, "memory_bytes": 6442450944,
    "memory_swap_bytes": 6442450944, "io_read_bps": 1205862400,
    "io_write_bps": 1205862400, "pids_max": 512,
    "cpu_isolation_degree": 3, "numa_isolation_degree": 1,
}


REMOTE_DRY = r'''
set -euo pipefail
data="$1"; template="$2"; lane="$3"; serial="$4"; image="$5"
test "$(hostname)" = node01
test -d "$data"; test -d "$template"
mode=$(stat -c '%a' "$data"); test "$((8#$mode & 0222))" -ne 0
test "$(realpath "$data")" != "$(realpath "$template")"
test "$(docker image inspect "$image" --format '{{.Id}}')" = "$image"
test -f "$lane"; test ! -L "$lane"; test -w "$(dirname "$serial")"
test ! -e "${lane%.lock}.owner.json"
exec 8>>"$lane"; flock -n 8; flock -u 8
if test -e "$serial"; then test ! -L "$serial"; exec 9>>"$serial"; flock -n 9; flock -u 9; fi
test -z "$(docker ps --format '{{.Names}}' | grep -E '^ra_mysql_ap01_endpoint_' || true)"
test -z "$(docker network ls --format '{{.Name}}' | grep -E '^ra_mysql_ap01_endpoint_' || true)"
test "$(docker inspect mysql_hybench_ap_formal --format '{{.State.Running}}')" = false
test "$(docker inspect mysql_hybench_ap_formal --format '{{.HostConfig.CpusetCpus}}|{{.HostConfig.CpusetMems}}')" = '24-26,60-62|1'
python3 - "$data" "$template" <<'PY'
import json, os, subprocess, sys
data, template = map(os.path.realpath, sys.argv[1:])
for cid in subprocess.check_output(['docker','ps','-aq'], universal_newlines=True).split():
    row=json.loads(subprocess.check_output(['docker','inspect',cid], universal_newlines=True))[0]
    if row['State']['Running'] and any(os.path.realpath(m['Source']) in (data,template) for m in row['Mounts']):
        raise SystemExit('AP datadir/template is mounted by a running container')
print(json.dumps({'status':'REMOTE_DRY_PASS','host':'node01','lane_lock_available':True,'serialization_lock_available':True,'session_datadir_container_writable_mode_present':True,'canonical_template_distinct':True,'canonical_template_mounted':False,'temporary_container_count':0,'temporary_network_count':0,'mysql_connection_attempted':False,'container_started':False},sort_keys=True))
PY
'''


class CleanupStack:
    def __init__(self) -> None:
        self._rows: list[tuple[str, Callable[[], None]]] = []
        self.events: list[dict[str, Any]] = []

    def push(self, label: str, action: Callable[[], None]) -> None:
        self._rows.append((label, action))

    def close(self) -> None:
        failures: list[str] = []
        for label, action in reversed(self._rows):
            try:
                action(); self.events.append({"action": label, "status": "PASS"})
            except Exception as error:
                failures.append(f"{label}:{type(error).__name__}")
                self.events.append({"action": label, "status": "FAIL", "error_type": type(error).__name__})
        self._rows.clear()
        if failures:
            raise RuntimeError("cleanup failed: " + ",".join(failures))


def digest(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"missing or unsafe file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sidecar(path: Path) -> str:
    value = digest(path); pointer = path.with_name(path.name + ".sha256")
    claimed, name = pointer.read_text(encoding="utf-8").split()
    if claimed != value or name != path.name:
        raise ValueError(f"sidecar mismatch: {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> str:
    path = path.resolve(); path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(raw, path)
    finally:
        if os.path.exists(raw): os.unlink(raw)
    value_sha = hashlib.sha256(payload.encode()).hexdigest()
    pointer = path.with_name(path.name + ".sha256")
    fd, raw = tempfile.mkstemp(prefix=f".{pointer.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(f"{value_sha}  {path.name}\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(raw, pointer)
    finally:
        if os.path.exists(raw): os.unlink(raw)
    return value_sha


def frozen_local_gate() -> dict[str, Any]:
    envelope_sha = sidecar(ENVELOPE)
    envelope = json.loads(ENVELOPE.read_text(encoding="utf-8"))
    if envelope.get("normalized_envelope") != EXPECTED_ENVELOPE or envelope.get("storage_scope", {}).get("session_owned_datadir") != DATADIR:
        raise ValueError("canonical AP envelope/datadir mismatch")
    native_sha = sidecar(NATIVE_GATE)
    native = json.loads(NATIVE_GATE.read_text(encoding="utf-8"))
    if native.get("status") != "PASS_PREPARATION_ISOLATION" or native.get("failures") != []:
        raise ValueError("native mysqld continuous gate is not passing")
    scope = native.get("qualification", {})
    if scope != {"preparation_isolation_only": True, "postgresql_counterpart": False, "formal_static_isolation_gate_passed": False, "r0": False, "sgd": False, "ready": False, "launch_authorized": False}:
        raise ValueError("native gate formal scope changed")
    return {"envelope_sha256": envelope_sha, "native_gate_sha256": native_sha, "collector_sha256": digest(COLLECTOR)}


def validate_v5(request_path: Path | None, runtime_path: Path | None, request_sha: str | None, runtime_sha: str | None) -> dict[str, Any]:
    if request_path is None and runtime_path is None and request_sha is None and runtime_sha is None:
        return {"status": "WAITING_FOR_V5_REQUEST_RUNTIME_SHA", "ready_for_execute": False}
    if None in (request_path, runtime_path, request_sha, runtime_sha):
        raise ValueError("request, runtime and both exact SHA values must be supplied together")
    assert request_path is not None and runtime_path is not None and request_sha is not None and runtime_sha is not None
    if digest(request_path) != request_sha.lower() or digest(runtime_path) != runtime_sha.lower():
        raise ValueError("v5 request/runtime SHA mismatch")
    request = json.loads(request_path.read_text(encoding="utf-8")); runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    binding = request.get("binding", {}); active = binding.get("parameter_space", {}).get("active_params", [])
    freeze = str(binding.get("benchmark_version") or "")
    if request.get("schema_version") != "mysql_trained_estimator_endpoint_collection_request_v1" or binding.get("benchmark") != "hybench_ap" or len(active) != 8 or 1 + 2 * len(active) != 17 or "v5" not in freeze:
        raise ValueError("request is not the expected AP v5 17-endpoint contract")
    if runtime.get("schema_version") not in {"mysql_endpoint_runtime_template_v1", "mysql_endpoint_runtime_config_v1"}:
        raise ValueError("runtime schema mismatch")
    mysql = runtime.get("mysql_client", {})
    if mysql.get("host") != "127.0.0.1" or not isinstance(mysql.get("port"), int) or mysql.get("database") != "hybench_ap" or mysql.get("defaults_extra_file") in (None, ""):
        raise ValueError("runtime client endpoint/defaults contract mismatch")
    return {"status": "V5_SHA_BOUND", "ready_for_execute": False, "request": {"path": str(request_path.resolve()), "sha256": request_sha.lower()}, "runtime": {"path": str(runtime_path.resolve()), "sha256": runtime_sha.lower()}, "endpoint_count": 17}


def remote_dry() -> dict[str, Any]:
    process = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", HOST, "bash", "-s", "--", DATADIR, TEMPLATE, LANE_LOCK, SERIAL_LOCK, IMAGE], input=REMOTE_DRY, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.returncode:
        raise RuntimeError("remote dry-run failed: " + process.stderr.strip())
    return json.loads(process.stdout.splitlines()[-1])


def failure_cleanup_self_test() -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="ap_endpoint_cleanup_test."))
    stack = CleanupStack(); state = {"cost": "anchor", "container": False, "network": False}
    defaults = root / "external.defaults.cnf"
    try:
        defaults.write_text("[client]\npassword=not-a-real-secret\n", encoding="utf-8"); os.chmod(defaults, 0o600)
        if stat.S_IMODE(defaults.stat().st_mode) != 0o600: raise AssertionError("defaults mode")
        stack.push("delete_external_defaults", lambda: defaults.unlink(missing_ok=True))
        state["network"] = True; stack.push("remove_network", lambda: state.__setitem__("network", False))
        state["container"] = True; stack.push("stop_remove_container", lambda: state.__setitem__("container", False))
        snapshot = state["cost"]; state["cost"] = "dirty"
        stack.push("restore_exact_cost_snapshot", lambda: state.__setitem__("cost", snapshot))
        raise RuntimeError("INJECTED_COLLECTOR_FAILURE")
    except RuntimeError as error:
        if str(error) != "INJECTED_COLLECTOR_FAILURE": raise
        stack.close()
    passed = state == {"cost": "anchor", "container": False, "network": False} and not defaults.exists()
    shutil.rmtree(root)
    if not passed: raise AssertionError("failure cleanup self-test failed")
    return {"status": "PASS", "injected_failure": "collector", "exact_cost_restored": True, "container_removed": True, "network_removed": True, "external_defaults_removed": True, "cleanup_order": stack.events}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--request-sha256")
    parser.add_argument("--runtime-sha256")
    parser.add_argument("--self-test-failure-cleanup", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-host")
    parser.add_argument("--confirm-datadir")
    parser.add_argument("--main-controller-release-sha256")
    args = parser.parse_args()
    if args.execute:
        # This check precedes every remote call and every credential/defaults-file action.
        raise ValueError("EXECUTE_NOT_ARMED_IN_PREPARATION_REVISION_WAIT_FOR_V5_MAIN_CONTROLLER_RELEASE")
    local = frozen_local_gate(); v5 = validate_v5(args.request, args.runtime, args.request_sha256, args.runtime_sha256)
    result = {
        "schema_version": "host01_ap_17_endpoint_session_dry_run_v1",
        "status": "DRY_RUN_PASS_WAITING_V5_SHA" if v5["status"].startswith("WAITING") else "DRY_RUN_PASS_V5_BOUND_EXECUTE_STILL_SEALED",
        "mode": "dry-run", "mutations": False, "mysql_connection_attempted": False,
        "container_started": False, "collector_executed": False, "endpoint_count": 17,
        "local_gate": local, "v5_gate": v5, "remote_gate": remote_dry(),
        "planned_session": {"host": HOST, "retained_writable_datadir": DATADIR, "canonical_template": TEMPLATE, "container_prefix": "ra_mysql_ap01_endpoint_", "network_prefix": "ra_mysql_ap01_endpoint_", "independent_host_port_from_runtime": True, "independent_socket_directory": True, "cpuset_cpus": "24-26,60-62", "cpuset_mems": "1", "cpu_quota_cores": 3, "memory_bytes": 6442450944, "memory_swap_bytes": 6442450944, "io_read_bps": 1205862400, "io_write_bps": 1205862400, "pids_max": 512, "lane_lock": LANE_LOCK, "serialization_lock": SERIAL_LOCK, "external_defaults_mode": "0600", "cost_restore_in_finally": True, "clean_shutdown_and_remove_container_network_defaults_in_finally": True, "template_never_mounted": True},
        "failure_cleanup_self_test": failure_cleanup_self_test() if args.self_test_failure_cleanup else {"status": "NOT_REQUESTED"},
        "qualification": {"preparation_only": True, "postgresql_counterpart": False, "formal_static_isolation_gate_passed": False, "r0": False, "sgd": False, "ready": False, "launch_authorized": False},
    }
    value_sha = atomic_json(args.output, result)
    print(json.dumps({"status": result["status"], "output": str(args.output.resolve()), "sha256": value_sha, "failure_cleanup_tested": args.self_test_failure_cleanup, "database_contacted": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "FAIL_CLOSED", "error": str(error)}, sort_keys=True))
        raise SystemExit(2)
