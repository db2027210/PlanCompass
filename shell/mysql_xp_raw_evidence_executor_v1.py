#!/usr/bin/env python3
"""Run the sealed XP Java workload and emit non-authoritative raw evidence.

This is deliberately a data producer, rather than a promotion authority.  It
has exactly two modes: a capture run and a fresh-R0 run.  Both invoke the
bundle's frozen HyBench trace JAR through the bound wrapper class, record the
real child PID/starttime and stdout/stderr, and fail closed if the workload,
counter collection, cadence, or graceful quiescence fails.  It never creates
containers, updates cgroups, or grants R0/formal/accepted-round credit.
"""
import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

FORMAL = {"cpu_quota_cores": 5.3, "cpuset_cpus": "21-33,57-69",
          "cpuset_mems": "1", "memory_bytes": 8589934592,
          "memory_swap_bytes": 8589934592, "pids_max": 1024,
          "io_read_bps": 29360128, "io_write_bps": 524288,
          "innodb_buffer_pool_bytes": 4294967296, "mysql_port": 65104}
CAPTURE_IO = {"io_read_bps": 1205862400, "io_write_bps": 1205862400}
JAR = "predecessor/predecessor/HyBench-1.0-SNAPSHOT-formal-xp-binding-trace-candidate-jar-with-dependencies.jar"
WRAPPER = "qualification/native_latency_producer/classes"
STATEMENTS = "qualification/config/stmt_mysql_formal_case_normalized.toml"
PROPERTIES = "qualification/config/hybench_xp_qualification.properties"
CLIENT_BUNDLE = "/opt/mysql-xp-bundle"
CLIENT_RUNTIME = "/opt/mysql-xp-jdk"
CLIENT_MEASUREMENT = "/measurement"


class ExecutorError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path, label: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ExecutorError("%s must be a regular file" % label)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ExecutorError("%s must be an object" % label)
    return value


def _startticks(pid: int) -> int:
    text = Path("/proc") .joinpath(str(pid), "stat").read_text(encoding="ascii")
    fields = text[text.rfind(")") + 2:].split()
    if len(fields) <= 19:
        raise ExecutorError("workload proc stat is malformed")
    return int(fields[19])


def _proc_usage(pid):
    """Return child CPU ticks, RSS bytes, and read+write bytes from procfs."""
    stat = Path("/proc").joinpath(str(pid), "stat").read_text(encoding="ascii")
    fields = stat[stat.rfind(")") + 2:].split()
    if len(fields) <= 21:
        raise ExecutorError("workload proc usage is malformed")
    ticks = int(fields[11]) + int(fields[12])
    rss = int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
    io = {}
    for row in Path("/proc").joinpath(str(pid), "io").read_text(encoding="ascii").splitlines():
        key, value = row.split(":", 1); io[key.strip()] = int(value.strip())
    return ticks, rss, io.get("read_bytes", 0) + io.get("write_bytes", 0)


def _atomic(path: Path, value: dict) -> None:
    if path.exists() or path.is_symlink():
        raise ExecutorError("output must be fresh")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temp = path.with_name("." + path.name + ".%d" % os.getpid())
    with temp.open("xb") as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)
    path.with_name(path.name + ".sha256").write_text(
        "%s  %s\n" % (_sha(path), path.name), encoding="ascii")


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _fingerprint_observation(path, samples):
    """Bind complete L1/L2/L3 observations to these exact raw samples."""
    value = _json(path, "executor fingerprint observation")
    required_l3 = {"shared_read_blocks", "shared_write_blocks", "local_read_blocks",
                   "local_write_blocks", "temp_read_blocks", "temp_write_blocks", "io_wait_ms"}
    fingerprint = value.get("fingerprint")
    if (
        value.get("schema_version") != "mysql_xp_executor_fingerprint_observation_v1"
        or value.get("producer") != "bundle_bound_sql_and_proc_collectors"
        or value.get("counter_samples_sha256") != _canonical_sha(samples)
        or value.get("qps_resource_input_present") is not False
        or not isinstance(fingerprint, dict)
        or set(fingerprint) != {"parameter_signature", "l1", "l2", "l3"}
        or not isinstance(fingerprint.get("parameter_signature"), str)
        or not fingerprint["parameter_signature"]
        or not isinstance(fingerprint.get("l1"), dict)
        or set(fingerprint["l1"]) != {"operator_types", "join_order", "scan_types", "parallel_structure"}
        or not isinstance(fingerprint.get("l2"), dict) or not fingerprint["l2"]
        or not isinstance(fingerprint.get("l3"), dict) or set(fingerprint["l3"]) != required_l3
        or any(not isinstance(item, (int, float)) or isinstance(item, bool) or item < 0
               for item in fingerprint["l3"].values())
    ):
        raise ExecutorError("execution fingerprint observation differs")
    return {"observation_sha256": _sha(path), "counter_samples_sha256": _canonical_sha(samples),
            "fingerprint": fingerprint}


def _bundle(bundle):
    manifest = _json(bundle / "bundle_manifest.json", "bundle manifest")
    if manifest.get("bundle_id") != bundle.name or manifest.get("formal_scope") != "mysql_only":
        raise ExecutorError("bundle identity/scope differs")
    jar, wrapper, statements = bundle / JAR, bundle / WRAPPER, bundle / STATEMENTS
    for path, label in ((jar, "sealed HyBench trace JAR"), (wrapper, "wrapper classes"),
                        (statements, "sealed statement file")):
        if path.is_symlink() or not path.exists():
            raise ExecutorError(label + " is absent")
    return manifest, jar, wrapper, statements


def _readback(path: Path, expected: dict, label: str, bundle_id: str) -> dict:
    value = _json(path, label)
    if (value.get("bundle_id") != bundle_id or value.get("formal_scope") != "mysql_only"
            or value.get("resource_envelope") != expected):
        raise ExecutorError(label + " envelope/bundle differs")
    return value


def _counter(command_file: Path, timeout: int) -> dict:
    command = _json(command_file, "counter command").get("argv")
    if (not isinstance(command, list) or not command or command[:3] != ["docker", "exec", "--user"]
            or "/usr/bin/mysql" not in command or "--skip-column-names" not in command):
        raise ExecutorError("counter command is not exact client docker-exec stdout")
    try:
        completed = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"database_counter_timeout": True, "database_counter_missing": True,
                "database_counters": {}}
    if completed.returncode or not completed.stdout.strip():
        return {"database_counter_timeout": False, "database_counter_missing": True,
                "database_counters": {}}
    rows = {}
    for line in completed.stdout.splitlines():
        if "\t" in line:
            key, value = line.split("\t", 1); rows[key] = value
    return {"database_counter_timeout": False, "database_counter_missing": not bool(rows),
            "database_counters": rows}


def _client_capture_argv(container, run_id):
    """Return the only admissible capture workload placement: client cgroup.

    The client has sealed bundle/JDK mounts and a private /measurement tmpfs.
    A host java process is not an acceptable substitute.  The trace is written
    there and copied out only after docker-exec exits successfully.
    """
    if not isinstance(container, str) or len(container) < 12:
        raise ExecutorError("capture client container identity differs")
    if not isinstance(run_id, str) or not run_id:
        raise ExecutorError("capture run identity differs")
    trace = CLIENT_MEASUREMENT + "/capture-" + run_id + ".jsonl"
    argv = [
        "docker", "exec", "--user", "1008:1008", container,
        CLIENT_RUNTIME + "/bin/java", "-cp",
        CLIENT_BUNDLE + "/" + WRAPPER + ":" + CLIENT_BUNDLE + "/" + JAR,
        "com.hybench.HyBenchFormalMain", "xp",
        CLIENT_BUNDLE + "/" + PROPERTIES,
        CLIENT_BUNDLE + "/" + STATEMENTS,
    ]
    return argv, trace


def _copy_client_trace(container, client_trace, host_trace):
    host_trace = Path(host_trace)
    for suffix in ("", ".manifest.json"):
        target = host_trace if not suffix else Path(str(host_trace) + suffix)
        if target.exists() or target.is_symlink():
            raise ExecutorError("capture trace target is not fresh")
        completed = subprocess.run(
            ["docker", "cp", "%s:%s" % (container, client_trace + suffix), str(target)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        if completed.returncode:
            raise ExecutorError("client capture trace copy failed")


def _capture_trace_evidence(bundle: Path, trace: Path, manifest: Path,
                            run_id: str, runtime_sha: str, capture_sha: str) -> dict:
    """Derive AT/Future evidence solely from the sealed trace and client source.

    The frozen client calls ``Future.get`` and turns every exceptional/cancelled
    worker into a non-zero Java exit.  A successful process plus the sealed
    trace's complete sixteen-worker set is therefore the only admissible
    source for the 16 Future rows; callers cannot supply a summary list.
    """
    value = _json(manifest, "sealed trace manifest")
    required_workers = {"XAP-%d" % i for i in range(1, 9)} | {"XTP-%d" % i for i in range(1, 9)}
    if (value.get("schema_version") != "formal_mysql_binding_trace_manifest_v1"
            or value.get("status") != "SEALED" or value.get("run_id") != run_id
            or value.get("trace_sha256") != _sha(trace)
            or value.get("runtime_authority_sha256") != runtime_sha
            or value.get("capture_authorization_sha256") != capture_sha
            or value.get("worker_coverage") != 16 or value.get("at_committed_coverage") != 6):
        raise ExecutorError("sealed capture trace manifest differs")
    workers, commits, statements = set(), set(), {"AT%d" % i: 0 for i in range(1, 7)}
    try:
        lines = trace.read_text(encoding="utf-8").splitlines()
        for line in lines:
            row = json.loads(line)
            if not isinstance(row, dict) or row.get("run_id") != run_id:
                raise ExecutorError("foreign/malformed capture trace event")
            worker = row.get("worker_or_terminal")
            if isinstance(worker, str): workers.add(worker)
            at = row.get("transaction_type")
            if at in statements and row.get("event_type") == "statement" and row.get("outcome") == "success":
                statements[at] += 1
            if at in statements and row.get("event_type") == "commit" and row.get("outcome") == "success":
                commits.add(at)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ExecutorError("capture trace cannot be parsed") from exc
    source = bundle / "predecessor/predecessor/source/src/main/java/com/hybench/workload/Client.java"
    if source.is_symlink() or not source.is_file():
        raise ExecutorError("sealed Future-get source is absent")
    text = source.read_text(encoding="utf-8")
    if "fs[i].get()" not in text or "load worker completed exceptionally" not in text:
        raise ExecutorError("sealed Future exceptions are not fail-closed")
    if workers != required_workers or commits != set(statements) or any(value < 1 for value in statements.values()):
        raise ExecutorError("capture lacks required workers or committed AT1-AT6")
    return {
        "trace_manifest_sha256": _sha(manifest), "future_get_source_sha256": _sha(source),
        "future_exception_fail_closed": True, "at_statement_coverage": statements,
        "at_commit_coverage": {name: True for name in sorted(commits)},
        "worker_futures": [{"worker": name, "get_called": True, "status": "SUCCESS",
                            "exception": None, "cancelled": False}
                           for name in sorted(required_workers)],
    }


def _run(args) -> dict:
    bundle = Path(args.bundle).resolve(strict=True)
    manifest, jar, wrapper, statements = _bundle(bundle)
    java = Path(args.java).resolve(strict=True)
    if java.is_symlink() or not java.is_file() or _sha(java) != args.java_sha256:
        raise ExecutorError("published Java binary identity differs")
    properties = Path(args.properties).resolve(strict=True)
    if properties.is_symlink() or not properties.is_file() or properties != (bundle / PROPERTIES):
        raise ExecutorError("only bundle-bound properties are permitted")
    formal = _readback(Path(args.formal_readback), FORMAL, "formal readback", manifest["bundle_id"])
    capture = None
    if args.mode == "capture":
        capture = _readback(Path(args.capture_readback), CAPTURE_IO, "capture readback", manifest["bundle_id"])
    output = Path(args.output).resolve()
    outlog, errlog = output.with_suffix(".java.stdout"), output.with_suffix(".java.stderr")
    if output.exists() or outlog.exists() or errlog.exists():
        raise ExecutorError("attempt output already exists")
    client_trace = None
    if args.mode == "capture":
        argv, client_trace = _client_capture_argv(
            args.client_container, args.trace_run_id
        )
    else:
        argv = [str(java), "-cp", str(wrapper) + ":" + str(jar),
                "com.hybench.HyBenchFormalMain", "xp", str(properties), str(statements)]
    started = time.monotonic()
    environment = dict(os.environ)
    trace = None
    if args.mode == "capture":
        trace = Path(args.trace).resolve()
        if trace.exists() or trace.is_symlink() or not args.trace_run_id:
            raise ExecutorError("capture requires fresh trace path and run identity")
        environment.update({
            "FORMAL_MYSQL_BINDING_TRACE_PATH": client_trace,
            "FORMAL_MYSQL_BINDING_TRACE_RUN_ID": args.trace_run_id,
            "FORMAL_MYSQL_BINDING_TRACE_SCHEMA": "formal_mysql_binding_trace_event_v1",
            "FORMAL_MYSQL_BINDING_RUNTIME_AUTHORITY_SHA256": args.runtime_authority_sha256,
            "FORMAL_MYSQL_BINDING_CAPTURE_AUTHORIZATION_SHA256": args.capture_authorization_sha256,
        })
        docker_environment = []
        for key in sorted(name for name in environment if name.startswith("FORMAL_MYSQL_BINDING_")):
            docker_environment += ["--env", key + "=" + environment[key]]
        argv = argv[:2] + docker_environment + argv[2:]
    with outlog.open("xb") as stdout, errlog.open("xb") as stderr:
        process = subprocess.Popen(argv, stdout=stdout, stderr=stderr, env=environment, start_new_session=True)
        pid, ticks = process.pid, _startticks(process.pid)
        samples = []
        if args.mode == "fresh-r0":
            last_ticks, last_rss, last_io = _proc_usage(pid)
            last_sample_at = time.monotonic()
            for sequence in range(180):
                deadline = started + sequence + 1
                while time.monotonic() < deadline:
                    time.sleep(min(0.05, deadline - time.monotonic()))
                if process.poll() is not None:
                    raise ExecutorError("workload ended before 180 fresh-R0 samples")
                current_at = time.monotonic()
                current_ticks, current_rss, current_io = _proc_usage(pid)
                elapsed = current_at - last_sample_at
                if not (0 < elapsed <= 1.5):
                    raise ExecutorError("fresh-R0 sample cadence differs")
                sample = {"sequence": sequence, "elapsed_seconds": current_at - started,
                          "workload_pid": pid, "workload_pid_starttime_ticks": ticks,
                          "stable": True, "utilization": {
                              "cpu": max(0.0, (current_ticks-last_ticks) / float(os.sysconf("SC_CLK_TCK")) / elapsed / FORMAL["cpu_quota_cores"]),
                              "memory": max(0.0, current_rss / float(FORMAL["memory_bytes"])),
                              "io": max(0.0, (current_io-last_io) / elapsed / float(FORMAL["io_read_bps"] + FORMAL["io_write_bps"])),
                          }}
                sample.update(_counter(Path(args.counter_command), timeout=5))
                if sample["database_counter_timeout"] or sample["database_counter_missing"]:
                    raise ExecutorError("database counter timeout/missing: fail closed")
                samples.append(sample)
                last_ticks, last_rss, last_io, last_sample_at = current_ticks, current_rss, current_io, current_at
        # The wrapper honours the sealed 120s graceful quiescence stop file.
        if args.mode == "capture":
            completed = subprocess.run(
                ["docker", "exec", "--user", "1008:1008", args.client_container,
                 "sh", "-c", "test ! -e %s && printf '180\\n' > %s" % (args.stop_file, args.stop_file)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                check=False,
            )
            if completed.returncode:
                raise ExecutorError("client graceful stop-file write failed")
        else:
            stop = Path(args.stop_file)
            if stop.is_symlink() or stop.exists():
                raise ExecutorError("fresh stop file is required")
            stop.write_text("180\n", encoding="ascii")
        try:
            rc = process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=15)
            raise ExecutorError("graceful 120s quiescence failed")
    if rc != 0:
        raise ExecutorError("sealed HyBench workload exited nonzero")
    if trace:
        _copy_client_trace(args.client_container, client_trace, trace)
    trace_manifest = Path(str(trace) + ".manifest.json") if trace else None
    if trace and (trace.is_symlink() or not trace.is_file() or trace_manifest.is_symlink()
                  or not trace_manifest.is_file() or not trace.read_bytes()):
        raise ExecutorError("sealed capture trace/manifest is absent")
    capture_evidence = (
        _capture_trace_evidence(bundle, trace, trace_manifest, args.trace_run_id,
                                args.runtime_authority_sha256, args.capture_authorization_sha256)
        if trace else None
    )
    fingerprint_evidence = (
        _fingerprint_observation(Path(args.fingerprint_observation), samples)
        if args.mode == "fresh-r0" else None
    )
    return {"schema_version": "mysql_xp_raw_executor_run_v1", "status": "PASS_RAW_RUN_ONLY",
            "mode": args.mode, "bundle_id": manifest["bundle_id"], "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True, "java_argv": argv,
            # capture is launched through docker exec: this PID is the host
            # docker-exec supervisor, not a falsely-labelled Java PID.
            "java_sha256": args.java_sha256,
            "workload_pid": pid if args.mode == "fresh-r0" else None,
            "capture_docker_exec_host_pid": pid if trace else None,
            "workload_pid_starttime_ticks": ticks, "java_stdout_sha256": _sha(outlog),
            "java_stderr_sha256": _sha(errlog), "capture_readback_sha256": _sha(Path(args.capture_readback)) if capture else None,
            "trace_path": str(trace) if trace else None,
            "trace_sha256": _sha(trace) if trace else None,
            "trace_manifest_path": str(trace_manifest) if trace_manifest else None,
            "trace_manifest_sha256": _sha(trace_manifest) if trace_manifest else None,
            "capture_evidence": capture_evidence,
            "capture_client_container": args.client_container if trace else None,
            "capture_client_cgroup_execution": True if trace else None,
            "fingerprint_evidence": fingerprint_evidence,
            "formal_readback_sha256": _sha(Path(args.formal_readback)), "samples": samples,
            "capture_is_r0": False, "accepted_rounds_credit": 0,
            "formal_launch_authorized": False, "accepted_round_authorized": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("capture", "fresh-r0"), required=True)
    parser.add_argument("--bundle", required=True); parser.add_argument("--java", required=True)
    parser.add_argument("--java-sha256", required=True); parser.add_argument("--properties", required=True)
    parser.add_argument("--formal-readback", required=True); parser.add_argument("--capture-readback")
    parser.add_argument("--counter-command"); parser.add_argument("--stop-file", required=True)
    parser.add_argument("--fingerprint-observation")
    parser.add_argument("--trace"); parser.add_argument("--trace-run-id")
    parser.add_argument("--client-container")
    parser.add_argument("--runtime-authority-sha256", default="")
    parser.add_argument("--capture-authorization-sha256", default="")
    parser.add_argument("--output", required=True); args = parser.parse_args()
    if args.mode == "capture" and (not args.capture_readback or not args.trace
                                    or len(args.runtime_authority_sha256) != 64
                                    or len(args.capture_authorization_sha256) != 64
                                    or not args.client_container
                                    or args.stop_file != CLIENT_MEASUREMENT + "/hybench-stop-request"):
        parser.error("capture needs readback, trace, authority, client cgroup, and private stop path")
    if args.mode == "fresh-r0" and (not args.counter_command or not args.fingerprint_observation):
        parser.error("fresh-r0 needs --counter-command and executor fingerprint observation")
    _atomic(Path(args.output), _run(args)); return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (ExecutorError, OSError, ValueError) as exc:
        raise SystemExit("FAIL_CLOSED: %s" % exc)
