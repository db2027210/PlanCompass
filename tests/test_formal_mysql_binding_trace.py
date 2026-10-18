from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys

import pytest

from formal_mysql_binding_trace import (
    BindingTraceError,
    BindingTraceWriter,
    EVENT_SCHEMA_VERSION,
    binding_trace_environment,
    canonical_sql_sha256,
    finalize_binding_trace,
)


def event(writer: BindingTraceWriter, worker: str, sequence: int, attempt: int = 1):
    return writer.record(
        benchmark="tpch", query_id="Q6", worker_or_terminal=worker,
        sequence=sequence, attempt=attempt,
        seed_state={"state_before": "java-random-state-17"},
        seed_derivation={"global_seed": 4508333, "terminal_seed": 991},
        actual_bindings=[
            {"position": 1, "jdbc_type": "DATE", "value": "1994-01-01"},
            {"position": 2, "jdbc_type": "DECIMAL", "value": "0.06"},
        ],
        template_sql="select * from lineitem where d >= ? and discount = ?",
        executable_sql="select * from lineitem where d >= '1994-01-01' and discount = 0.06",
        outcome="success",
        timestamp_utc="2026-07-23T04:00:00.000001Z",
    )


def _append(path: str, worker: str, count: int) -> None:
    writer = BindingTraceWriter(Path(path), "run-1")
    for sequence in range(1, count + 1):
        event(writer, worker, sequence)


def test_disabled_trace_does_not_change_environment_or_create_files(tmp_path: Path) -> None:
    base = {"PATH": os.environ.get("PATH", ""), "UNCHANGED": "yes"}
    result = binding_trace_environment(
        base, enabled=False, events=tmp_path / "events.jsonl", run_id="run-1"
    )
    assert result == base
    assert not list(tmp_path.iterdir())
    assert BindingTraceWriter.from_environment(result) is None


def test_fake_workload_switch_off_is_behaviorally_unchanged_and_on_emits(tmp_path: Path) -> None:
    script = r'''
from formal_mysql_binding_trace import BindingTraceWriter
w = BindingTraceWriter.from_environment()
if w is not None:
    w.record(benchmark="hybench_ap", query_id="AP-1", worker_or_terminal="T1",
      sequence=1, attempt=1, seed_state={"state_before":"s0"},
      seed_derivation={"global_seed":4508333,"worker_seed":7},
      actual_bindings=[{"position":1,"jdbc_type":"INTEGER","value":17}],
      template_sql="select ?", executable_sql="select 17", outcome="success")
print("FAKE_WORKLOAD_DONE")
'''
    base = dict(os.environ)
    disabled = binding_trace_environment(
        base, enabled=False, events=tmp_path / "off.jsonl", run_id="run-off"
    )
    off = subprocess.run([sys.executable, "-c", script], env=disabled,
                         check=True, text=True, capture_output=True)
    assert off.stdout == "FAKE_WORKLOAD_DONE\n"
    assert not (tmp_path / "off.jsonl").exists()
    enabled = binding_trace_environment(
        base, enabled=True, events=tmp_path / "on.jsonl", run_id="run-on"
    )
    on = subprocess.run([sys.executable, "-c", script], env=enabled,
                        check=True, text=True, capture_output=True)
    assert on.stdout == off.stdout
    assert finalize_binding_trace(tmp_path / "on.jsonl", expected_run_id="run-on")["event_count"] == 1


def test_enabled_trace_is_complete_and_sha_sealed(tmp_path: Path) -> None:
    path = tmp_path / "bindings.jsonl"
    env = binding_trace_environment({}, enabled=True, events=path, run_id="run-1")
    writer = BindingTraceWriter.from_environment(env)
    assert writer is not None
    row = event(writer, "terminal-1", 1)
    assert row["canonical_executable_sql_sha256"] == canonical_sql_sha256(
        "select * from lineitem where d >= '1994-01-01' and discount = 0.06"
    )
    manifest = finalize_binding_trace(path, expected_run_id="run-1")
    assert manifest["event_count"] == 1
    assert manifest["failed_attempt_count"] == 0
    sidecar = path.with_suffix(".jsonl.manifest.json.sha256")
    assert sidecar.is_file()
    assert len(sidecar.read_text().split()[0]) == 64
    with pytest.raises(BindingTraceError, match="already sealed"):
        event(writer, "terminal-1", 2)


def test_concurrent_workers_are_append_safe_and_recovery_does_not_recount(tmp_path: Path) -> None:
    path = tmp_path / "bindings.jsonl"
    workers = [multiprocessing.Process(target=_append, args=(str(path), f"T{i}", 5)) for i in range(4)]
    for process in workers:
        process.start()
    for process in workers:
        process.join(10)
        assert process.exitcode == 0
    manifest = finalize_binding_trace(path, expected_run_id="run-1")
    assert manifest["event_count"] == 20
    assert manifest["maximum_sequence_by_worker"] == {f"T{i}": 5 for i in range(4)}
    recovered = finalize_binding_trace(path, expected_run_id="run-1")
    assert recovered["event_count"] == 20


def test_duplicate_recovery_and_failure_never_publish_manifest(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.jsonl"
    writer = BindingTraceWriter(path, "run-1")
    event(writer, "T1", 1)
    with pytest.raises(BindingTraceError, match="duplicate execution event"):
        event(writer, "T1", 1)
    broken = tmp_path / "broken.jsonl"
    broken.write_text('{"not":"an event"}\n', encoding="utf-8")
    with pytest.raises(BindingTraceError):
        finalize_binding_trace(broken)
    assert not broken.with_suffix(".jsonl.manifest.json").exists()


def test_credentials_and_result_data_are_rejected(tmp_path: Path) -> None:
    writer = BindingTraceWriter(tmp_path / "events.jsonl", "run-1")
    with pytest.raises(BindingTraceError, match="forbidden key"):
        writer.record(
            benchmark="hybench_ap", query_id="AP-2", worker_or_terminal="T1",
            sequence=1, attempt=1, seed_state={"password": "CHANGE_ME"},
            seed_derivation={"global_seed": 4508333}, actual_bindings=[],
            executable_sql="select 1", template_sql="select 1", outcome="success",
        )


def test_schema_file_matches_runtime_version() -> None:
    schema = json.loads(Path("preflight/mysql_binding_trace_event_schema_v1.json").read_text())
    assert schema["$id"] == EVENT_SCHEMA_VERSION
