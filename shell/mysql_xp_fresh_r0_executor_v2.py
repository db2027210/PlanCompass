#!/usr/bin/env python3
"""Run sealed fresh R0 inside the adopted XP client and post-bind evidence."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


DOCKER = ["docker", "--host", "unix:///run/ra-docker-mysql-xp-v2.sock"]
CLIENT = {"cpu_quota_cores": 18, "cpuset_cpus": "3-11,39-47",
          "cpuset_mems": "0", "memory_bytes": 8589934592,
          "memory_swap_bytes": 8589934592, "pids_max": 1024}
FORMAL = {"cpu_quota_cores": 5.3, "cpuset_cpus": "21-33,57-69",
          "cpuset_mems": "1", "memory_bytes": 8589934592,
          "memory_swap_bytes": 8589934592, "pids_max": 1024,
          "io_read_bps": 29360128, "io_write_bps": 524288,
          "innodb_buffer_pool_bytes": 4294967296, "mysql_port": 65104}
CLIENT_JAVA = "/opt/mysql-xp-jdk/bin/java"
CLIENT_BUNDLE = "/opt/mysql-xp-bundle"
CLIENT_MEASUREMENT = "/measurement"
JAR = "predecessor/predecessor/HyBench-1.0-SNAPSHOT-formal-xp-binding-trace-candidate-jar-with-dependencies.jar"
WRAPPER = "qualification/native_latency_producer/classes"
PROPERTIES = "qualification/config/hybench_xp_qualification.properties"
PRIVATE_PROPERTIES = "/run/mysql-xp-private/hybench_xp_qualification.properties"
STATEMENTS = "qualification/config/stmt_mysql_formal_case_normalized.toml"
JAVA_CLASS = "com.hybench.HyBenchFormalMain"


class ExecutorError(RuntimeError):
    pass


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha(value) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sealed(path: Path, label: str) -> dict:
    path = Path(path); sidecar = Path(str(path) + ".sha256")
    if path.is_symlink() or not path.is_file() or sidecar.is_symlink() or not sidecar.is_file():
        raise ExecutorError(label + " is not sealed")
    if sidecar.read_text(encoding="ascii") != "%s  %s\n" % (sha(path), path.name):
        raise ExecutorError(label + " sidecar differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise ExecutorError(label + " is not an object")
    return value


def atomic(path: Path, value: dict) -> None:
    path = Path(path)
    if path.exists() or path.is_symlink(): raise ExecutorError("fresh output required")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    temporary = path.with_name("." + path.name + ".%d" % os.getpid())
    with temporary.open("xb") as stream:
        stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    Path(str(path) + ".sha256").write_text(
        "%s  %s\n" % (sha(path), path.name), encoding="ascii"
    )


def load_collector(bundle: Path):
    path = bundle / "launch/mysql_xp_fresh_r0_fingerprint_collector_v1.py"
    spec = importlib.util.spec_from_file_location("xp_v25_fingerprint_collector", path)
    if spec is None or spec.loader is None: raise ExecutorError("collector import failed")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def docker(*args, timeout=15) -> str:
    completed = subprocess.run(DOCKER + list(args), text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=timeout, check=False)
    if completed.returncode: raise ExecutorError("dedicated Docker command failed")
    return completed.stdout


def inspect_client(client: str, init_pid: int) -> dict:
    try: rows = json.loads(docker("inspect", client))
    except json.JSONDecodeError as exc: raise ExecutorError("client inspect differs") from exc
    if not isinstance(rows, list) or len(rows) != 1: raise ExecutorError("client inspect differs")
    row = rows[0]; host = row.get("HostConfig") or {}; state = row.get("State") or {}
    quota, period = int(host.get("CpuQuota") or 0), int(host.get("CpuPeriod") or 0)
    exact = {"CpusetCpus": CLIENT["cpuset_cpus"], "CpusetMems": CLIENT["cpuset_mems"],
             "Memory": CLIENT["memory_bytes"], "MemorySwap": CLIENT["memory_swap_bytes"],
             "PidsLimit": CLIENT["pids_max"]}
    if (row.get("Id") != client or state.get("Running") is not True
            or int(state.get("Pid") or 0) != int(init_pid) or period <= 0
            or abs(float(quota) / period - CLIENT["cpu_quota_cores"]) > 1e-9
            or any(str(host.get(key)) != str(value) for key, value in exact.items())
            or host.get("NetworkMode") != "host" or host.get("ReadonlyRootfs") is not True):
        raise ExecutorError("adopted client resource envelope differs")
    return {"container_id": client, "init_pid": init_pid, "cpu_quota": quota,
            "cpu_period": period, "cpuset_cpus": host["CpusetCpus"],
            "cpuset_mems": host["CpusetMems"], "memory_bytes": host["Memory"],
            "memory_swap_bytes": host["MemorySwap"], "pids_max": host["PidsLimit"]}


def proc_start(pid: int) -> int:
    text = Path("/proc/%d/stat" % int(pid)).read_text(encoding="ascii")
    fields = text[text.rfind(")") + 2:].split()
    if len(fields) <= 21: raise ExecutorError("process stat differs")
    return int(fields[19])


def proc_usage(pid: int):
    text = Path("/proc/%d/stat" % int(pid)).read_text(encoding="ascii")
    fields = text[text.rfind(")") + 2:].split()
    ticks, rss = int(fields[11]) + int(fields[12]), int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
    values = {}
    for line in Path("/proc/%d/io" % int(pid)).read_text(encoding="ascii").splitlines():
        key, raw = line.split(":", 1); values[key.strip()] = int(raw.strip())
    return ticks, rss, values.get("read_bytes", 0) + values.get("write_bytes", 0)


def cgroups(pid: int) -> dict[str, str]:
    result = {}
    for line in Path("/proc/%d/cgroup" % int(pid)).read_text(encoding="ascii").splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3:
            for controller in fields[1].split(","):
                if controller: result[controller] = fields[2]
    return result


def status_lists(pid: int) -> tuple[str, str]:
    values = {}
    for line in Path("/proc/%d/status" % int(pid)).read_text(encoding="ascii").splitlines():
        if ":" in line:
            key, value = line.split(":", 1); values[key] = value.strip()
    return values.get("Cpus_allowed_list", ""), values.get("Mems_allowed_list", "")


def verify_java_cgroup(init_pid: int, java_pid: int) -> dict:
    init, child = cgroups(init_pid), cgroups(java_pid)
    required = {"cpu", "cpuacct", "cpuset", "memory", "pids"}
    if not required.issubset(init) or any(child.get(name) != init[name] for name in required):
        raise ExecutorError("Java workload is outside adopted client cgroups")
    cpus, mems = status_lists(java_pid)
    if cpus != CLIENT["cpuset_cpus"] or mems != CLIENT["cpuset_mems"]:
        raise ExecutorError("Java CPU/NUMA affinity differs")
    return {"controllers": {name: child[name] for name in sorted(required)},
            "cpus_allowed_list": cpus, "mems_allowed_list": mems,
            "cpu_quota_cores": CLIENT["cpu_quota_cores"],
            "memory_bytes": CLIENT["memory_bytes"],
            "memory_swap_bytes": CLIENT["memory_swap_bytes"],
            "pids_max": CLIENT["pids_max"]}


def java_argv(client: str) -> list[str]:
    return DOCKER + ["exec", "--user", "1008:1008", client, CLIENT_JAVA,
        "-cp", CLIENT_BUNDLE + "/" + WRAPPER + ":" + CLIENT_BUNDLE + "/" + JAR,
        JAVA_CLASS, "xp", PRIVATE_PROPERTIES,
        CLIENT_BUNDLE + "/" + STATEMENTS]


def private_properties_binding(adoption: dict) -> dict:
    credentials = adoption.get("private_credentials") or {}
    hashes = credentials.get("content_sha256") or {}
    if (credentials.get("destination") != "/run/mysql-xp-private"
            or credentials.get("qualification_config") != "hybench_xp_qualification.properties"
            or credentials.get("directory_mode") != "0700"
            or credentials.get("file_mode") != "0600"
            or credentials.get("user") != "ra_xp_qual_" + hashlib.sha256(
                str(adoption.get("attempt_id") or "").encode()).hexdigest()[:20]
            or not isinstance(hashes.get("hybench_xp_qualification.properties"), str)
            or len(hashes["hybench_xp_qualification.properties"]) != 64):
        raise ExecutorError("private workload properties binding differs")
    return {"path_in_client": PRIVATE_PROPERTIES,
            "sha256": hashes["hybench_xp_qualification.properties"],
            "directory_mode": "0700", "file_mode": "0600",
            "qualification_user": credentials["user"],
            "public_bundle_properties_prohibited": True}


def client_private_properties_readback(client: str, adoption: dict) -> dict:
    expected = private_properties_binding(adoption)
    digest_output = docker("exec", "--user", "1008:1008", client,
                           "sha256sum", PRIVATE_PROPERTIES).strip()
    fields = digest_output.split()
    mode = docker("exec", "--user", "1008:1008", client,
                  "stat", "-c", "%a", PRIVATE_PROPERTIES).strip()
    if (len(fields) != 2 or fields[1] != PRIVATE_PROPERTIES
            or fields[0] != expected["sha256"] or mode != "600"):
        raise ExecutorError("client private workload properties readback differs")
    return {**expected, "observed_sha256": fields[0], "observed_mode": mode,
            "readback_transport": "exact_adopted_client_docker_exec_stdout_v1"}


def java_pids(client: str) -> set[int]:
    output = docker("top", client, "-eo", "pid,args")
    result = set()
    for line in output.splitlines()[1:]:
        fields = line.strip().split(None, 1)
        if len(fields) == 2 and JAVA_CLASS in fields[1] and CLIENT_JAVA in fields[1]:
            try: result.add(int(fields[0]))
            except ValueError: pass
    return result


def wait_new_java(client: str, before: set[int], timeout=30) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = java_pids(client) - before
        if len(current) == 1: return next(iter(current))
        if len(current) > 1: raise ExecutorError("multiple new Java PIDs")
        time.sleep(0.1)
    raise ExecutorError("real client-cgroup Java PID was not observed")


def validate_postbind(raw: dict, bundle: Path, manifest: dict, adoption_path: Path,
                      registry_path: Path, identity: dict, samples: list, collector) -> dict:
    relative = "launch/mysql_xp_fresh_r0_fingerprint_collector_v1.py"
    producer = bundle / relative; fingerprint = raw.get("fingerprint")
    required_l3 = {"shared_read_blocks", "shared_write_blocks", "local_read_blocks",
                   "local_write_blocks", "temp_read_blocks", "temp_write_blocks", "io_wait_ms"}
    if (raw.get("schema_version") != "mysql_xp_fresh_r0_raw_fingerprint_v1"
            or raw.get("status") != "PASS_CONCURRENT_RAW_OBSERVATION"
            or raw.get("bundle_id") != bundle.name
            or raw.get("producer_relative_path") != relative
            or raw.get("producer_sha256") != sha(producer)
            or manifest.get("files", {}).get(relative) != sha(producer)
            or raw.get("adopted_service_sha256") != sha(adoption_path)
            or raw.get("trace_registry_sha256") != sha(registry_path)
            or raw.get("same_live_identity") != identity
            or raw.get("client_container_id") != identity.get("client_container_id")
            or raw.get("counter_sql") != collector.COUNTER_SQL
            or raw.get("counter_sql_sha256") != hashlib.sha256(collector.COUNTER_SQL.encode()).hexdigest()
            or raw.get("counter_transport") != "exact_adopted_client_docker_exec_stdout_v2"
            or raw.get("defaults_file_in_client") != collector.DEFAULTS
            or raw.get("mysql_port") != collector.PORT
            or raw.get("qps_resource_input_present") is not False
            or not isinstance(fingerprint, dict)
            or set(fingerprint) != {"parameter_signature", "l1", "l2", "l3"}
            or not fingerprint.get("parameter_signature") or not fingerprint.get("l1")
            or not fingerprint.get("l2") or set(fingerprint.get("l3") or {}) != required_l3):
        raise ExecutorError("concurrent fingerprint evidence differs")
    return {"schema_version": "mysql_xp_fresh_r0_fingerprint_postbind_v1",
            "raw_observation_sha256": raw["_sha256"],
            "producer_sha256": raw["producer_sha256"],
            "counter_samples_sha256": canonical_sha(samples),
            "binding_order": "raw_observation_then_executor_sample_postbind",
            "fingerprint": fingerprint}


def run(args) -> dict:
    bundle = Path(args.bundle).resolve(strict=True)
    manifest = sealed(bundle / "bundle_manifest.json", "bundle manifest")
    adoption_path, registry_path = Path(args.adopted_service), Path(args.trace_registry)
    adoption = sealed(adoption_path, "adopted service"); registry = sealed(registry_path, "trace registry")
    readback = sealed(Path(args.formal_readback), "formal readback")
    identity = adoption.get("same_live_identity") or {}
    client, init_pid = identity.get("client_container_id"), int(identity.get("client_init_pid") or 0)
    if (manifest.get("bundle_id") != bundle.name or adoption.get("bundle_id") != bundle.name
            or adoption.get("status") != "PASS_ADOPTED_SAME_LIVE_SERVICE"
            or registry.get("bundle_id") != bundle.name or registry.get("same_live_identity") != identity
            or registry.get("status") != "SEALED_REAL_TRACE"
            or readback.get("bundle_id") != bundle.name or readback.get("resource_envelope") != FORMAL
            or proc_start(init_pid) != int(identity.get("client_init_starttime_ticks") or 0)):
        raise ExecutorError("same-live fresh R0 prerequisites differ")
    client_readback = inspect_client(client, init_pid)
    properties_binding = private_properties_binding(adoption)
    properties_before = client_private_properties_readback(client, adoption)
    output = Path(args.output).resolve(); tmpfs = Path((adoption.get("host_output_tmpfs") or {}).get("path") or "").resolve()
    if output == tmpfs or tmpfs not in output.parents or output.exists() or output.is_symlink():
        raise ExecutorError("output must be fresh inside adopted tmpfs")
    output.parent.mkdir(parents=True, exist_ok=True)
    ready, stop, raw_fp = (output.with_suffix(output.suffix + suffix)
                           for suffix in (".fingerprint.ready.json", ".fingerprint.stop", ".fingerprint.raw.json"))
    stdout_path, stderr_path = output.with_suffix(".java.stdout"), output.with_suffix(".java.stderr")
    collector_path = bundle / "launch/mysql_xp_fresh_r0_fingerprint_collector_v1.py"
    collector_cmd = [sys.executable, "-B", str(collector_path), "--bundle", str(bundle),
        "--adopted-service", str(adoption_path), "--trace-registry", str(registry_path),
        "--ready", str(ready), "--stop", str(stop), "--output", str(raw_fp)]
    collector_process = subprocess.Popen(collector_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         text=True, start_new_session=True)
    deadline = time.monotonic() + 60
    while not ready.is_file():
        if collector_process.poll() is not None or time.monotonic() >= deadline:
            raise ExecutorError("concurrent fingerprint collector did not become ready")
        time.sleep(0.1)
    ready_doc = sealed(ready, "fingerprint ready")
    if ready_doc.get("bundle_id") != bundle.name or ready_doc.get("client_container_id") != client:
        raise ExecutorError("fingerprint ready binding differs")
    before = java_pids(client); started = time.monotonic()
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        java_process = subprocess.Popen(java_argv(client), stdout=stdout, stderr=stderr,
                                        text=False, start_new_session=True)
        java_pid = wait_new_java(client, before); java_ticks = proc_start(java_pid)
        cgroup_readback = verify_java_cgroup(init_pid, java_pid)
        last_ticks, last_rss, last_io = proc_usage(java_pid); last_at = time.monotonic()
        collector = load_collector(bundle); samples = []
        for sequence in range(180):
            deadline = started + sequence + 1
            while time.monotonic() < deadline: time.sleep(min(0.05, deadline - time.monotonic()))
            if java_process.poll() is not None or not Path("/proc/%d" % java_pid).is_dir():
                raise ExecutorError("client Java ended before 180 samples")
            now = time.monotonic(); ticks, rss, io_bytes = proc_usage(java_pid); elapsed = now - last_at
            if not (0 < elapsed <= 1.5): raise ExecutorError("fresh R0 cadence differs")
            counter_rows = collector.counters(client)
            samples.append({"sequence": sequence, "elapsed_seconds": now-started,
                "workload_pid": java_pid, "workload_pid_starttime_ticks": java_ticks,
                "stable": True, "utilization": {
                    "cpu": max(0.0, (ticks-last_ticks)/os.sysconf("SC_CLK_TCK")/elapsed/FORMAL["cpu_quota_cores"]),
                    "memory": max(0.0, rss/FORMAL["memory_bytes"]),
                    "io": max(0.0, (io_bytes-last_io)/elapsed/(FORMAL["io_read_bps"]+FORMAL["io_write_bps"]))},
                "database_counter_timeout": False, "database_counter_missing": False,
                "database_counters": counter_rows})
            last_ticks, last_rss, last_io, last_at = ticks, rss, io_bytes, now
        stop.write_text("STOP\n", encoding="ascii")
        stop_completed = subprocess.run(DOCKER + ["exec", "--user", "1008:1008", client,
            "sh", "-c", "test ! -e %s && printf '180\\n' > %s" %
            (CLIENT_MEASUREMENT + "/hybench-stop-request", CLIENT_MEASUREMENT + "/hybench-stop-request")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if stop_completed.returncode: raise ExecutorError("sealed client stop-file write failed")
        try: rc = java_process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            os.killpg(java_process.pid, signal.SIGTERM); java_process.wait(timeout=15)
            raise ExecutorError("graceful 120s quiescence failed")
    try: collector_rc = collector_process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        os.killpg(collector_process.pid, signal.SIGTERM); collector_process.wait(timeout=10)
        raise ExecutorError("concurrent fingerprint collector did not finish")
    if rc or collector_rc or Path("/proc/%d" % java_pid).exists():
        raise ExecutorError("fresh R0 workload/collector completion differs")
    raw = sealed(raw_fp, "raw concurrent fingerprint"); raw["_sha256"] = sha(raw_fp)
    postbind = validate_postbind(raw, bundle, manifest, adoption_path, registry_path,
                                 identity, samples, collector)
    properties_after = client_private_properties_readback(client, adoption)
    if properties_after != properties_before:
        raise ExecutorError("private workload properties changed during fresh R0")
    return {"schema_version": "mysql_xp_raw_executor_run_v2", "status": "PASS_RAW_RUN_ONLY",
        "mode": "fresh-r0", "bundle_id": bundle.name, "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True, "adopted_service_sha256": sha(adoption_path),
        "same_live_identity": identity, "formal_readback_sha256": sha(Path(args.formal_readback)),
        "trace_registry_sha256": sha(registry_path), "workload_execution": "adopted_client_cgroup_docker_exec_v1",
        "workload_pid": java_pid, "workload_pid_starttime_ticks": java_ticks,
        "client_container_readback": client_readback, "client_cgroup_readback": cgroup_readback,
        "private_workload_properties": {**properties_binding,
            "pre_readback": properties_before, "post_readback": properties_after,
            "unchanged_during_workload": True},
        "java_argv": java_argv(client), "java_stdout_sha256": sha(stdout_path),
        "java_stderr_sha256": sha(stderr_path), "samples": samples,
        "fingerprint_evidence": postbind, "counter_command_identity": {
            "kind": "exact_adopted_client_docker_exec_stdout_v2", "client_container_id": client,
            "defaults_file_in_client": collector.DEFAULTS, "mysql_port": collector.PORT,
            "counter_sql_sha256": hashlib.sha256(collector.COUNTER_SQL.encode()).hexdigest(),
            "caller_supplied_argv": False, "qps_resource_input_present": False},
        "capture_is_r0": False, "accepted_rounds_credit": 0,
        "formal_launch_authorized": False, "accepted_round_authorized": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True); parser.add_argument("--adopted-service", required=True)
    parser.add_argument("--formal-readback", required=True); parser.add_argument("--trace-registry", required=True)
    parser.add_argument("--output", required=True); args = parser.parse_args()
    atomic(Path(args.output), run(args)); return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (ExecutorError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit("FAIL_CLOSED: " + str(exc))
