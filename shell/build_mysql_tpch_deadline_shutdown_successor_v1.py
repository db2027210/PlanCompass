#!/usr/bin/env python3
"""Build FF313 SQL-adapter deadline/shutdown successor, offline only."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4"
PARENT_SHA = "af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52"
PARENT_ADAPTER = "14236c9eb868d491be356f6eece4bfa75f954022178dd955e117c5be267dbdff"
PARENT = ROOT / "artifacts/mysql_tpch_sql_native_percentile_successor_v1" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_deadline_shutdown_successor_v1"
ADAPTER = "qualification/mysql_sql_debug_metric_adapter_v1.py"
FORMAL = "tpch_formal10_contract.json"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode()


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_map(root):
    return {
        path.relative_to(root).as_posix(): sha_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.relative_to(root).as_posix()
        not in ("bundle_manifest.json", "bundle_manifest.json.sha256")
    }


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise BuildError(label + " anchor differs")
    return text.replace(old, new)


def patch_adapter(payload):
    text = payload.decode()
    text = replace_once(
        text,
        'SAMPLE_SCHEMA = "mysql_algorithm_c_debug_workload_metric_sample_v1"\n',
        '''SAMPLE_SCHEMA = "mysql_algorithm_c_debug_workload_metric_sample_v1"
# The collector's immutable graceful-exit gate is 120 seconds.  A query that
# starts immediately before the adapter deadline must therefore finish and let
# every worker join strictly inside that gate.  Ten seconds remain for Python,
# mysql-client and systemd scheduling after the 100-second server limit.
QUERY_EXECUTION_LIMIT_SECONDS = 100
QUERY_CLIENT_LIMIT_SECONDS = 110
''',
        "shutdown limit constants",
    )
    helper = '''

def _acquire_before_deadline(lock, stop, deadline):
    """Acquire the Q15 serialization lock without crossing the deadline."""
    if lock is None:
        return not stop.is_set() and time.monotonic() < deadline
    while not stop.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if lock.acquire(timeout=min(0.05, remaining)):
            if stop.is_set() or time.monotonic() >= deadline:
                lock.release()
                return False
            return True
    return False


def _effective_query_limit_seconds(requested):
    requested = int(requested)
    if requested <= 0:
        raise AdapterError("query timeout must be positive")
    return min(requested, QUERY_EXECUTION_LIMIT_SECONDS)
'''
    text = replace_once(
        text,
        "\ndef run_adapter(args):\n",
        helper + "\n\ndef run_adapter(args):\n",
        "deadline helper insertion",
    )
    old_start = text.index("    metrics = NativeMetrics()\n")
    old_end = text.index("    if metrics.failure is not None:\n", old_start)
    old_block = text[old_start:old_end]
    expected_markers = (
        "subprocess.run(",
        "timeout=args.query_timeout_seconds + 5",
        "lock.acquire()",
        "started = time.monotonic()",
        "for future in futures:",
    )
    if any(old_block.count(marker) != 1 for marker in expected_markers):
        raise BuildError("parent worker/shutdown block differs")
    new_block = '''    metrics = NativeMetrics()
    stop = threading.Event()
    q15_lock = threading.Lock()
    started = time.monotonic()
    deadline = started + args.duration_seconds

    def worker(worker_id):
        position = worker_id % len(schedule)
        while not stop.is_set():
            if time.monotonic() >= deadline:
                return
            path = schedule[position]
            position = (position + args.concurrency) % len(schedule)
            # Production TPC-H Q15 uses the sealed shared ``revenue0`` view.
            # Waiting terminals poll the stop/deadline and recheck after lock
            # acquisition, so none can submit a late CREATE/SELECT/DROP.
            lock = q15_lock if path.name == "Q15.sql" else None
            if not _acquire_before_deadline(lock, stop, deadline):
                return
            try:
                if stop.is_set() or time.monotonic() >= deadline:
                    return
                begun = time.monotonic()
                metrics.begin(worker_id, begun)
                query_limit = _effective_query_limit_seconds(
                    args.query_timeout_seconds
                )
                command = [
                    str(args.mysql_binary),
                    "--defaults-extra-file=%s" % args.defaults_extra_file,
                    "--protocol=TCP",
                    "--host=%s" % args.host,
                    "--port=%d" % args.port,
                    "--user=%s" % args.user,
                    "--batch",
                    "--raw",
                    "--skip-column-names",
                    # Continue Q15 through its sealed DROP VIEW after a SELECT
                    # error; returncode remains nonzero and fails closed.
                    "--force",
                    "--init-command=SET SESSION MAX_EXECUTION_TIME=%d"
                    % (query_limit * 1000),
                    args.database,
                    "--execute",
                    statements[path],
                ]
                try:
                    completed = subprocess.run(
                        command,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        timeout=QUERY_CLIENT_LIMIT_SECONDS,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    metrics.fail(worker_id, "%s: %s" % (path.name, exc))
                    stop.set()
                    return
                latency_ms = (time.monotonic() - begun) * 1000.0
                if completed.returncode != 0:
                    metrics.fail(
                        worker_id,
                        "%s rc=%d stderr=%s"
                        % (
                            path.name,
                            completed.returncode,
                            (completed.stderr or "")[-500:],
                        ),
                    )
                    stop.set()
                    return
                metrics.complete(worker_id, latency_ms)
            finally:
                if lock is not None:
                    lock.release()

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(worker, worker_id)
            for worker_id in range(args.concurrency)
        ]
        ready_deadline = time.monotonic() + 0.75
        while True:
            with metrics.lock:
                ready = bool(metrics.in_flight or metrics.latencies_ms)
                failure = metrics.failure
            if failure is not None:
                raise AdapterError(failure)
            if ready:
                break
            if time.monotonic() >= ready_deadline:
                raise AdapterError("SQL workers did not expose native latency")
            time.sleep(0.005)

        sequence = 0
        next_sample = time.monotonic()
        while time.monotonic() < deadline and not stop.is_set():
            now_monotonic = time.monotonic()
            if now_monotonic < next_sample:
                time.sleep(min(0.02, next_sample - now_monotonic))
                continue
            with metrics.lock:
                failure = metrics.failure
            if failure is not None:
                raise AdapterError(failure)
            document = metrics.document(
                attempt, sequence, time.time(), now_monotonic
            )
            _append_sample(args.metric_stream, document)
            print(
                "DEBUG_SQL_METRIC_SAMPLE_JSON="
                + json.dumps(document, sort_keys=True, separators=(",", ":")),
                flush=True,
            )
            sequence += 1
            next_sample = started + sequence
        # This is the sole normal submission stop.  Already-running real SQL
        # completes under the strict 100/110-second server/client ceilings;
        # Q15 waiters observe stop and never submit after this point.
        stop.set()
        for future in futures:
            future.result()
'''
    return (text[:old_start] + new_block + text[old_end:]).encode()


TEST = r'''from __future__ import annotations
import importlib.util
import threading
import time
from pathlib import Path
import pytest

B = Path(__file__).resolve().parents[1]
A = B / "qualification/mysql_sql_debug_metric_adapter_v1.py"
s = importlib.util.spec_from_file_location("deadline_adapter", A)
M = importlib.util.module_from_spec(s)
s.loader.exec_module(M)


def test_single_query_limits_are_strictly_inside_collector_gate():
    assert M.QUERY_EXECUTION_LIMIT_SECONDS == 100
    assert M.QUERY_CLIENT_LIMIT_SECONDS == 110
    assert M.QUERY_CLIENT_LIMIT_SECONDS < 120
    assert M._effective_query_limit_seconds(600) == 100
    assert M._effective_query_limit_seconds(30) == 30
    with pytest.raises(M.AdapterError):
        M._effective_query_limit_seconds(0)


def test_q15_waiter_cannot_acquire_after_deadline():
    lock = threading.Lock()
    lock.acquire()
    try:
        started = time.monotonic()
        assert M._acquire_before_deadline(
            lock, threading.Event(), started + 0.03
        ) is False
        assert time.monotonic() - started < 0.2
    finally:
        lock.release()


def test_stop_prevents_non_q15_and_q15_submission():
    stop = threading.Event()
    stop.set()
    assert M._acquire_before_deadline(None, stop, time.monotonic() + 10) is False
    lock = threading.Lock()
    assert M._acquire_before_deadline(lock, stop, time.monotonic() + 10) is False
    assert lock.acquire(blocking=False) is True
    lock.release()


def test_weighted_schedule_and_real_completion_contract_unchanged(tmp_path):
    paths = [tmp_path / "Q1.sql", tmp_path / "Q2.sql"]
    schedule = M._weighted_schedule(paths, [3.0, 1.0], 17)
    assert schedule.count(paths[0]) == 3
    assert schedule.count(paths[1]) == 1
    metrics = M.NativeMetrics()
    metrics.begin(0, time.monotonic())
    metrics.complete(0, 12.5)
    assert metrics.completed_total == 1
    assert metrics.latencies_ms == [12.5]


def test_adapter_and_collector_keep_fail_closed_shutdown_contract():
    source = A.read_text()
    manifest = __import__("json").loads((B / "bundle_manifest.json").read_text())
    assert "subprocess.run(" in source
    assert "timeout=QUERY_CLIENT_LIMIT_SECONDS" in source
    assert '"--force"' in source
    assert "_acquire_before_deadline(lock, stop, deadline)" in source
    assert "stop.set()\n        for future in futures:" in source
    assert manifest["collector_graceful_exit_gate_seconds"] == 120
    assert manifest["collector_graceful_exit_gate_relaxed"] is False
    assert "QUERY_CLIENT_LIMIT_SECONDS < 120" not in source
'''


AUDIT = {
    "schema_version": "mysql_tpch_retry8_sql_adapter_shutdown_root_cause_v1",
    "status": "SEALED_LOCAL_ROOT_CAUSE_AND_SUCCESSOR_NOT_EXECUTED",
    "retry8_attempt": "tpch-qualification-retry5-20260803T062320Z",
    "retry8_unit": "ra-mysql-tpch-retry5-971eda5d6b76480d",
    "observed_host_samples": 181,
    "observed_native_samples": 240,
    "terminal_error": "debug qualification workload did not exit gracefully within 120s",
    "root_cause": "240-second adapter deadline stopped loop submission but ThreadPoolExecutor synchronously waited for in-flight mysql clients governed by a 600+5-second query/client timeout; Q15 lock waiters also lacked a post-lock deadline check",
    "fix": "stop all submissions at the exact adapter deadline, interrupt Q15 lock waits and recheck after acquisition, and enforce 100-second server plus 110-second client limits strictly below the unchanged 120-second collector gate",
    "sql_text_changed": False,
    "weights_changed": False,
    "concurrency_changed": False,
    "completed_total_semantics_changed": False,
    "timeout_is_failure": True,
    "collector_graceful_exit_gate_seconds": 120,
    "collector_graceful_exit_gate_relaxed": False,
    "qualification_credit": 0,
    "accepted_rounds_credit": 0,
    "formal_scope": "mysql_only",
    "cross_engine_comparison_prohibited": True,
    "postgresql_mutation_prohibited": True,
    "formal_launch_authorized": False,
    "accepted_round_authorized": False,
    "remote_actions": 0,
}


def verify_parent():
    manifest = json.loads((PARENT / "bundle_manifest.json").read_text())
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != PARENT_ID
        or sha_file(PARENT / "bundle_manifest.json") != PARENT_SHA
        or sha_bytes(canonical(unsigned)) != PARENT_ID
        or file_map(PARENT) != (manifest.get("files") or {})
        or sha_file(PARENT / ADAPTER) != PARENT_ADAPTER
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("FF313 parent closure differs")
    return manifest


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def build():
    parent = verify_parent()
    content = {
        relative: (PARENT / relative).read_bytes()
        for relative in sorted(parent["files"])
    }
    content[ADAPTER] = patch_adapter(content[ADAPTER])
    adapter_sha = sha_bytes(content[ADAPTER])
    formal = json.loads(content[FORMAL].decode())
    qualification_hashes = dict(formal["qualification_hashes"])
    if qualification_hashes.get("sql_adapter") != PARENT_ADAPTER:
        raise BuildError("formal SQL adapter parent binding differs")
    qualification_hashes["sql_adapter"] = adapter_sha
    formal["qualification_hashes"] = qualification_hashes
    content[FORMAL] = canonical(formal)
    content["tests/test_mysql_tpch_deadline_shutdown_successor_v1.py"] = TEST.encode()
    content["authority/mysql_tpch_retry8_sql_adapter_shutdown_root_cause_v1.json"] = canonical(AUDIT)
    content["builder/build_mysql_tpch_deadline_shutdown_successor_v1.py"] = HERE.read_bytes()
    files = {name: sha_bytes(value) for name, value in sorted(content.items())}
    component_hashes = dict(parent["component_hashes"])
    component_hashes["sql_adapter"] = adapter_sha
    identity = {
        key: value for key, value in parent.items()
        if key not in ("bundle_id", "files")
    }
    identity.update({
        # Preserve the established target-generation selector consumed by the
        # shared collector, and add the orthogonal shutdown successor marker.
        "successor_generation": "sql_native_completed_latency_contract_v1",
        "shutdown_successor_generation": "deadline_bounded_query_join_v1",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "builder_sha256": sha_file(HERE),
        "component_hashes": component_hashes,
        "formal10_contract_sha256": files[FORMAL],
        "query_execution_limit_seconds": 100,
        "query_client_limit_seconds": 110,
        "collector_graceful_exit_gate_seconds": 120,
        "collector_graceful_exit_gate_relaxed": False,
        "submission_after_deadline_allowed": False,
        "sql_text_changed": False,
        "weights_changed": False,
        "concurrency_changed": False,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    destination = OUT / bundle_id
    if destination.exists():
        if sha_file(destination / "bundle_manifest.json") != sha_bytes(canonical(manifest)):
            raise BuildError("existing destination differs")
        return destination
    OUT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".deadline-shutdown-", dir=OUT))
    try:
        for name, value in content.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        raw = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha_bytes(raw) + "  bundle_manifest.json\n"
        )
        freeze(stage)
        os.replace(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return destination


if __name__ == "__main__":
    bundle = build()
    print(json.dumps({
        "bundle": str(bundle),
        "bundle_id": bundle.name,
        "manifest_sha256": sha_file(bundle / "bundle_manifest.json"),
        "adapter_sha256": sha_file(bundle / ADAPTER),
        "remote_actions": 0,
    }, sort_keys=True))
