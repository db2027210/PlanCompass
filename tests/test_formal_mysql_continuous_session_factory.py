from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from formal_benchbase_continuous_session import FormalBenchBaseContinuousSession
from formal_hybench_continuous_session import FormalHyBenchContinuousSession
from formal_job_continuous_session import FormalJobContinuousSession
from formal_mysql_continuous_session_factory import (
    ALLOCATED_BENCHMARKS,
    DECISION_SECONDS,
    FormalSessionFactoryError,
    FormalSessionRuntimeServices,
    ProductionContinuousSessionFactory,
    build_allocated_session_factories,
)
from formal_mysql_sgd_orchestration import _run_continuous_session


def _protocol() -> dict[str, Any]:
    return {
        "immutable": True,
        "timing": {
            "sample_interval_seconds": 1,
            "stability_window_seconds": 30,
            "required_consecutive_stability_windows": 6,
            "minimum_measurement_seconds": 180,
            "maximum_measurement_seconds": 600,
            "sample_coverage_min": 0.9,
        },
        "stability": {
            "calculation": "within_each_window_from_one_second_samples",
            "all_cpu_memory_io_resources_must_be_stable": True,
            "cpu_cv_max": 0.1,
            "memory_cv_max": 0.1,
            "io_cv_max": 0.1,
            "normal_throughput_cv_max": 0.1,
            "xp_xap_qps_cv_max": 0.1,
            "xp_xtp_tps_cv_max": 0.1,
            "low_utilization_ratio_threshold": 0.01,
            "low_utilization_absolute_range_max": 0.01,
        },
    }


def _file(path: Path, payload: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path.resolve()


def _artifact(path: Path) -> dict[str, str]:
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _registration(tmp_path: Path, benchmark: str) -> dict[str, Any]:
    root = tmp_path / benchmark
    cwd = root / "cwd"
    cwd.mkdir(parents=True)
    java = _file(root / "java", "#!/bin/sh\nexit 0\n")
    java.chmod(0o700)
    artifacts: dict[str, Any] = {"java": _artifact(java)}
    launch: dict[str, Any] = {"cwd": str(cwd.resolve())}
    source = {
        "job": "formal_sql_completion_jsonl",
        "hybench_ap": "hybench_formal_sample_json",
        "hybench_xp": "hybench_formal_sample_json",
        "tpch": "benchbase_formal_completed_total",
        "tpcc": "benchbase_formal_completed_total",
    }[benchmark]
    if benchmark == "job":
        classes = root / "classes"
        client = _file(classes / "com/formal/FormalSQLWorkload.class", "job-client")
        connector = _file(root / "mysql.jar", "connector")
        config = _file(root / "job.properties", """
url=jdbc:mysql://registered-by-freeze.invalid/job
user=frozen-user
password=from-private-freeze
sql_file=/frozen/job.sql
concurrency=1
duration_seconds=660
warmup_seconds=60
maximum_measurement_seconds=600
stop_file={{FORMAL_STOP_FILE}}
event_path={{FORMAL_EVENT_LOG}}
""".lstrip())
        launch["formal_classes"] = str(classes.resolve())
        artifacts.update({
            "formal_client_class": _artifact(client),
            "connector_jar": _artifact(connector),
            "config_template": _artifact(config),
        })
    elif benchmark.startswith("hybench"):
        classes = root / "classes"
        client = _file(classes / "com/hybench/HyBenchFormalMain.class", "hy-client")
        artifacts.update({
            "formal_client_class": _artifact(client),
            "connector_jar": _artifact(_file(root / "mysql.jar", "connector")),
            "workload_jar": _artifact(_file(root / "hybench.jar", "hybench")),
            "statements": _artifact(_file(root / "statements.toml", "[AP-1]\n")),
            "config_template": _artifact(_file(root / "hybench.properties", """
formal_stop_file={{FORMAL_STOP_FILE}}
formal_warmup_seconds=60
formal_maximum_measurement_seconds=600
apRunSecs=660
xpRunSecs=660
password=from-private-freeze
""".lstrip())),
        })
        launch["formal_classes"] = str(classes.resolve())
    else:
        artifacts.update({
            "benchbase_jar": _artifact(_file(root / "benchbase.jar", "benchbase")),
            "config": _artifact(_file(
                root / f"{benchmark}.xml",
                "<parameters><works><work><time>660</time></work></works></parameters>\n",
            )),
        })
        if benchmark == "tpch":
            artifacts["seed_agent_jar"] = _artifact(
                _file(root / "tpch-seed-agent.jar", "agent")
            )
    return {
        "schema_version": 1,
        "immutable": True,
        "benchmark": benchmark,
        "timing": {
            "warmup_seconds": 60,
            "maximum_measurement_seconds": 600,
            "decision_seconds": list(DECISION_SECONDS),
        },
        "measurement_protocol": _protocol(),
        "database_qps": {
            "source": "database_status_counter",
            "attribution_scope": "dedicated_mysql_instance",
            "counter_name": "registered_statement_completions",
            "digest_set_sha256": None,
        },
        "throughput_source": source,
        "launch": launch,
        "artifacts": artifacts,
    }


class _Process:
    returncode = None

    def poll(self):
        return None


class _ProcessRecorder:
    def __init__(self):
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        return _Process()


def _services(**kwargs) -> FormalSessionRuntimeServices:
    return FormalSessionRuntimeServices(
        resource_samples=lambda: (), database_qps_samples=lambda: (), **kwargs
    )


def test_production_factory_requires_database_side_qps_sampler(tmp_path: Path) -> None:
    services = FormalSessionRuntimeServices(
        resource_samples=lambda: (), database_qps_samples=None,  # type: ignore[arg-type]
    )
    with pytest.raises(FormalSessionFactoryError, match="database_qps_samples"):
        ProductionContinuousSessionFactory(_registration(tmp_path, "job"), services)


def test_production_factory_requires_frozen_attributable_database_qps_contract(
    tmp_path: Path,
) -> None:
    registration = _registration(tmp_path, "job")
    registration.pop("database_qps")
    with pytest.raises(FormalSessionFactoryError, match="database_qps"):
        ProductionContinuousSessionFactory(registration, _services())

    registration = _registration(tmp_path / "digest", "job")
    registration["database_qps"] = {
        "source": "database_status_counter",
        "attribution_scope": "registered_workload_digest_set",
        "counter_name": "performance_schema_digest_sum_count_star",
    }
    with pytest.raises(FormalSessionFactoryError, match="digest_set_sha256"):
        ProductionContinuousSessionFactory(registration, _services())


def test_runtime_database_qps_samples_must_match_frozen_identity(
    tmp_path: Path,
) -> None:
    registration = _registration(tmp_path, "job")
    services = FormalSessionRuntimeServices(
        resource_samples=lambda: (),
        database_qps_samples=lambda: ({
            "epoch_ms": 1,
            "completed_total": 1,
            "source": "database_status_counter",
            "attribution_scope": "registered_workload_digest_set",
            "counter_name": "other_counter",
            "digest_set_sha256": "f" * 64,
        },),
    )
    session = ProductionContinuousSessionFactory(registration, services)({
        "benchmark": "job",
        "continuous_session_output": tmp_path / "output",
    })
    with pytest.raises(FormalSessionFactoryError, match="frozen attribution_scope"):
        session.database_qps_samples_provider()


@pytest.mark.parametrize(
    ("workload_name", "expected_type", "main_fragment"),
    [
        ("hybench_ap", FormalHyBenchContinuousSession, "com.hybench.HyBenchFormalMain"),
        ("job", FormalJobContinuousSession, "com.formal.FormalSQLWorkload"),
        ("tpch", FormalBenchBaseContinuousSession, "-b tpch"),
        ("hybench_xp", FormalHyBenchContinuousSession, "com.hybench.HyBenchFormalMain"),
    ],
)
def test_allocated_factory_builds_native_single_process_command(
    tmp_path: Path, workload_name: str, expected_type: type, main_fragment: str,
) -> None:
    registration = _registration(tmp_path, workload_name)
    services = _services(
        tpch_correctness=(lambda: {"passed": True}) if workload_name == "tpch" else None,
    )
    session = ProductionContinuousSessionFactory(registration, services)({
        "benchmark": workload_name,
        "continuous_session_output": tmp_path / "outputs" / workload_name,
    })
    assert isinstance(session, expected_type)
    command = [str(value) for value in session.command_builder(session.stop_file)]
    rendered = " ".join(command)
    assert main_fragment in rendered
    assert "Questions" not in rendered
    assert command.count("java") == 0  # the resolved registered executable is used once
    assert sum(Path(value).name == "java" for value in command) == 1
    if workload_name == "job":
        generated = (session.output / "private/formal_job.properties").read_text()
        assert f"stop_file={session.stop_file.resolve()}" in generated
        assert f"event_path={session.events_path.resolve()}" in generated
    elif workload_name.startswith("hybench"):
        generated = (session.output / "private/formal_hybench.properties").read_text()
        assert f"formal_stop_file={session.stop_file.resolve()}" in generated


def test_allocated_builder_registers_exactly_four_non_tpcc_factories(tmp_path: Path) -> None:
    registrations = {name: _registration(tmp_path, name) for name in ALLOCATED_BENCHMARKS}
    services = {
        name: _services(tpch_correctness=(lambda: {"passed": True}) if name == "tpch" else None)
        for name in ALLOCATED_BENCHMARKS
    }
    factories = build_allocated_session_factories(registrations, services)
    assert tuple(factories) == ALLOCATED_BENCHMARKS
    assert "tpcc" not in factories


def test_orchestration_uses_one_generated_process_and_30_second_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _ProcessRecorder()
    registration = _registration(tmp_path, "job")
    session = ProductionContinuousSessionFactory(
        registration, _services(
            process_factory=recorder,
            output_directory=lambda context: tmp_path / str(context["resume_token"]),
        ),
    )({
        "benchmark": "job",
        "resume_token": "one-process",
    })
    waits: list[int] = []
    monkeypatch.setattr(session, "wait_until_measurement", waits.append)
    monkeypatch.setattr(session, "observation", lambda seconds: {
        "measurement_seconds": seconds,
        "throughput_evidence": {
            "reliable": True, "source": "database_status_counter", "db_qps": 2.0,
        },
        "database_throughput": {
            "reliable": True, "source": "database_status_counter", "db_qps": 2.0,
        },
        "stability": {
            "stable": False,
            "decision_window_seconds": 180,
            "windows": [
                {
                    "index": index,
                    "sample_coverage": 1.0,
                    "stable": False,
                    "metrics": {
                        resource: {"stable": False}
                        for resource in ("cpu", "memory", "io")
                    },
                }
                for index in range(1, seconds // 30 + 1)
            ],
        },
    })
    stopped: list[int] = []
    monkeypatch.setattr(session, "stop", lambda seconds: stopped.append(seconds) or {})
    result = _run_continuous_session(session)
    session._close_logs()
    assert len(recorder.calls) == 1
    assert waits == list(DECISION_SECONDS)
    assert stopped == [600]
    assert result["rolling_measurement_control"]["selected_measurement_seconds"] == 600


def test_checksum_is_revalidated_at_point_boundary(tmp_path: Path) -> None:
    registration = _registration(tmp_path, "job")
    factory = ProductionContinuousSessionFactory(registration, _services())
    Path(registration["artifacts"]["config_template"]["path"]).write_text(
        "changed\n", encoding="utf-8"
    )
    with pytest.raises(FormalSessionFactoryError, match="checksum mismatch"):
        factory({
            "benchmark": "job",
            "continuous_session_output": tmp_path / "output",
        })


def test_tpcc_fails_closed_without_frozen_environment_and_provider(tmp_path: Path) -> None:
    registration = _registration(tmp_path, "tpcc")
    factory = ProductionContinuousSessionFactory(registration, _services())
    with pytest.raises(FormalSessionFactoryError, match="TPC-C frozen environment"):
        factory({
            "benchmark": "tpcc",
            "continuous_session_output": tmp_path / "tpcc-output",
        })


def test_tpcc_requires_and_accepts_both_frozen_environment_and_invariant(
    tmp_path: Path,
) -> None:
    registration = _registration(tmp_path, "tpcc")
    manifest = _file(tmp_path / "tpcc/environment.json", '{"immutable":true}\n')
    registration["artifacts"]["environment_manifest"] = _artifact(manifest)
    registration["tpcc_environment"] = {
        "ready": True,
        "freeze_id": "tpcc-env-v1",
        "resource_envelope_freeze_id": "paired-envelope-v1",
        "template_freeze_id": "tpcc-template-v1",
    }
    registration["tpcc_frozen_invariant"] = {"immutable": True, "version": "v1"}
    session = ProductionContinuousSessionFactory(
        registration,
        _services(tpcc_invariant=lambda phase: {
            "frozen_invariant_id": "v1", "passed": phase == "post",
        }),
    )({
        "benchmark": "tpcc",
        "continuous_session_output": tmp_path / "tpcc-ready-output",
    })
    assert isinstance(session, FormalBenchBaseContinuousSession)
    assert callable(session.tpcc_invariant)
