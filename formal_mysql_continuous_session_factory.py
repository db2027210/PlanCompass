"""Fail-closed production wiring for formal MySQL continuous sessions.

The factory deliberately separates immutable, serialisable launch registration
from live services (cgroup samples and correctness/invariant providers).  It
does not connect to a database or a remote host.  Every launch input is hashed
again immediately before a session is returned.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence
import xml.etree.ElementTree as ET

from formal_benchbase_continuous_session import FormalBenchBaseContinuousSession
from formal_hybench_continuous_session import FormalHyBenchContinuousSession
from formal_job_continuous_session import FormalJobContinuousSession


DECISION_SECONDS = tuple(range(180, 601, 30))
ALLOCATED_BENCHMARKS = ("hybench_ap", "job", "tpch", "hybench_xp")
KNOWN_BENCHMARKS = (*ALLOCATED_BENCHMARKS, "tpcc")

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PROPERTY = re.compile(r"^\s*([^#!\s][^:=\s]*)\s*[:=]\s*(.*?)\s*$")
_STOP_TOKEN = "{{FORMAL_STOP_FILE}}"
_EVENT_TOKEN = "{{FORMAL_EVENT_LOG}}"
_OUTPUT_TOKEN = "{{FORMAL_OUTPUT_DIR}}"


class FormalSessionFactoryError(RuntimeError):
    """The immutable registration or runtime wiring is not launchable."""


@dataclass(frozen=True)
class FormalSessionRuntimeServices:
    """Non-serialisable services owned by the admitted local lane."""

    resource_samples: Callable[[], Sequence[Mapping[str, Any]]]
    database_qps_samples: Callable[[], Sequence[Mapping[str, Any]]]
    output_directory: Callable[[Mapping[str, Any]], str | os.PathLike[str]] | None = None
    tpch_correctness: Callable[[], Mapping[str, Any]] | None = None
    tpcc_invariant: Callable[[str], Mapping[str, Any]] | None = None
    process_factory: Callable[..., Any] | None = None


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FormalSessionFactoryError(f"{label} must be a mapping")
    return value


def _artifact(registration: Mapping[str, Any], name: str) -> Path:
    artifacts = _mapping(registration.get("artifacts"), "artifacts")
    item = _mapping(artifacts.get(name), f"artifacts.{name}")
    raw_path = item.get("path")
    digest = item.get("sha256")
    if not isinstance(raw_path, str) or not raw_path:
        raise FormalSessionFactoryError(f"artifacts.{name}.path is absent")
    path = Path(raw_path)
    if not path.is_absolute():
        raise FormalSessionFactoryError(f"artifacts.{name}.path must be absolute")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise FormalSessionFactoryError(f"artifacts.{name}.sha256 is invalid")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise FormalSessionFactoryError(f"artifacts.{name} is absent: {path}") from error
    if not resolved.is_file():
        raise FormalSessionFactoryError(f"artifacts.{name} is not a regular file")
    actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if actual != digest:
        raise FormalSessionFactoryError(
            f"artifacts.{name} checksum mismatch: expected={digest} actual={actual}"
        )
    return resolved


def _directory(launch: Mapping[str, Any], name: str) -> Path:
    value = launch.get(name)
    if not isinstance(value, str) or not value:
        raise FormalSessionFactoryError(f"launch.{name} is absent")
    path = Path(value)
    if not path.is_absolute():
        raise FormalSessionFactoryError(f"launch.{name} must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise FormalSessionFactoryError(f"launch.{name} is absent: {path}") from error
    if not resolved.is_dir():
        raise FormalSessionFactoryError(f"launch.{name} is not a directory")
    return resolved


def _validate_timing(registration: Mapping[str, Any]) -> Mapping[str, Any]:
    timing = _mapping(registration.get("timing"), "timing")
    expected = {
        "warmup_seconds": 60,
        "maximum_measurement_seconds": 600,
        "decision_seconds": list(DECISION_SECONDS),
    }
    for key, value in expected.items():
        if timing.get(key) != value:
            raise FormalSessionFactoryError(
                f"frozen timing {key} must equal {value!r}"
            )
    protocol = _mapping(registration.get("measurement_protocol"), "measurement_protocol")
    if protocol.get("immutable") is not True:
        raise FormalSessionFactoryError("measurement_protocol must be immutable")
    return protocol


def _validate_database_qps_registration(
    registration: Mapping[str, Any],
) -> Mapping[str, Any]:
    contract = _mapping(registration.get("database_qps"), "database_qps")
    if contract.get("source") != "database_status_counter":
        raise FormalSessionFactoryError(
            "database_qps.source must be database_status_counter"
        )
    scope = contract.get("attribution_scope")
    if scope not in {"dedicated_mysql_instance", "registered_workload_digest_set"}:
        raise FormalSessionFactoryError("database_qps attribution_scope is invalid")
    counter_name = contract.get("counter_name")
    if not isinstance(counter_name, str) or not counter_name.strip():
        raise FormalSessionFactoryError("database_qps.counter_name is absent")
    digest = contract.get("digest_set_sha256")
    if scope == "registered_workload_digest_set" and not (
        isinstance(digest, str) and _SHA256.fullmatch(digest) is not None
    ):
        raise FormalSessionFactoryError(
            "registered database_qps digest_set_sha256 is invalid"
        )
    return contract


def _properties(text: str) -> Mapping[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        match = _PROPERTY.match(line)
        if match is None:
            continue
        key, value = match.groups()
        if key in result:
            raise FormalSessionFactoryError(f"duplicate generated property: {key}")
        result[key] = value
    return result


def _private_config(
    template: Path,
    target: Path,
    replacements: Mapping[str, str],
) -> Mapping[str, str]:
    text = template.read_text(encoding="utf-8")
    for token, value in replacements.items():
        if text.count(token) != 1:
            raise FormalSessionFactoryError(
                f"config template token cardinality differs for {token}"
            )
        text = text.replace(token, value)
    unresolved = sorted(set(re.findall(r"\{\{[A-Z0-9_]+\}\}", text)))
    if unresolved:
        raise FormalSessionFactoryError(f"unresolved config tokens: {unresolved}")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if target.exists():
            target.unlink()
        raise
    return _properties(text)


def _require_properties(
    properties: Mapping[str, str], expected: Mapping[str, str], label: str,
) -> None:
    for key, value in expected.items():
        if properties.get(key) != value:
            raise FormalSessionFactoryError(
                f"{label} property {key} must equal {value!r}"
            )


def _validate_registration(registration: Mapping[str, Any]) -> str:
    if registration.get("schema_version") != 1 or registration.get("immutable") is not True:
        raise FormalSessionFactoryError(
            "continuous-session registration must be immutable schema_version=1"
        )
    benchmark = registration.get("benchmark")
    if benchmark not in KNOWN_BENCHMARKS:
        raise FormalSessionFactoryError(f"unknown registered benchmark: {benchmark!r}")
    _validate_timing(registration)
    _validate_database_qps_registration(registration)
    launch = _mapping(registration.get("launch"), "launch")
    source = registration.get("throughput_source")
    expected_sources = {
        "job": "formal_sql_completion_jsonl",
        "hybench_ap": "hybench_formal_sample_json",
        "hybench_xp": "hybench_formal_sample_json",
        "tpch": "benchbase_formal_completed_total",
        "tpcc": "benchbase_formal_completed_total",
    }
    if source != expected_sources[benchmark]:
        raise FormalSessionFactoryError(
            f"{benchmark} must use native throughput source {expected_sources[benchmark]}"
        )
    java = _artifact(registration, "java")
    if not os.access(java, os.X_OK):
        raise FormalSessionFactoryError("registered Java executable is not executable")
    _directory(launch, "cwd")
    return str(benchmark)


class ProductionContinuousSessionFactory:
    """Callable suitable for ``FormalPointDependencies.session_factories``."""

    def __init__(
        self,
        registration: Mapping[str, Any],
        services: FormalSessionRuntimeServices,
    ) -> None:
        if not callable(services.resource_samples):
            raise FormalSessionFactoryError("resource_samples service must be callable")
        if not callable(services.database_qps_samples):
            raise FormalSessionFactoryError(
                "database_qps_samples service must be callable"
            )
        if services.output_directory is not None and not callable(services.output_directory):
            raise FormalSessionFactoryError("output_directory service must be callable")
        self.registration = copy.deepcopy(dict(registration))
        self.services = services
        self.benchmark = _validate_registration(self.registration)

    def _database_qps_samples(self) -> Sequence[Mapping[str, Any]]:
        contract = _validate_database_qps_registration(self.registration)
        rows = self.services.database_qps_samples()
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise FormalSessionFactoryError(
                "database_qps_samples must return a sample sequence"
            )
        for row in rows:
            if not isinstance(row, Mapping):
                raise FormalSessionFactoryError("database QPS sample is not a mapping")
            for field in ("source", "attribution_scope", "counter_name"):
                if row.get(field) != contract.get(field):
                    raise FormalSessionFactoryError(
                        f"database QPS sample differs from frozen {field}"
                    )
            if row.get("digest_set_sha256") != contract.get("digest_set_sha256"):
                raise FormalSessionFactoryError(
                    "database QPS sample differs from frozen digest_set_sha256"
                )
        return rows

    def __call__(self, context: Mapping[str, Any]):
        # Revalidate all immutable bytes at the point boundary, not only when
        # the long-lived coordinator constructed this factory.
        benchmark = _validate_registration(self.registration)
        if context.get("benchmark") != benchmark:
            raise FormalSessionFactoryError(
                f"factory/context benchmark mismatch: {benchmark}/{context.get('benchmark')}"
            )
        raw_output = (
            self.services.output_directory(context)
            if self.services.output_directory is not None
            else context.get("continuous_session_output")
        )
        if not isinstance(raw_output, (str, os.PathLike)):
            raise FormalSessionFactoryError(
                "context.continuous_session_output is required"
            )
        output = Path(raw_output).resolve()
        if output.exists() and (output.is_symlink() or not output.is_dir()):
            raise FormalSessionFactoryError("continuous_session_output must be a directory")
        launch = _mapping(self.registration["launch"], "launch")
        cwd = _directory(launch, "cwd")
        protocol = _validate_timing(self.registration)
        java = _artifact(self.registration, "java")

        if benchmark == "job":
            return self._job(output, cwd, protocol, java)
        if benchmark in {"hybench_ap", "hybench_xp"}:
            return self._hybench(output, cwd, protocol, java, benchmark)
        return self._benchbase(output, cwd, protocol, java, benchmark)

    def _job(self, output: Path, cwd: Path, protocol: Mapping[str, Any], java: Path):
        classes = _directory(self.registration["launch"], "formal_classes")
        client = _artifact(self.registration, "formal_client_class")
        expected_client = (classes / "com/formal/FormalSQLWorkload.class").resolve()
        if client != expected_client:
            raise FormalSessionFactoryError("JOB formal client is outside registered class root")
        connector = _artifact(self.registration, "connector_jar")
        template = _artifact(self.registration, "config_template")

        def command(stop_file: Path) -> Sequence[str]:
            properties = _private_config(
                template, output / "private" / "formal_job.properties",
                {
                    _STOP_TOKEN: str(stop_file.resolve()),
                    _EVENT_TOKEN: str((output / "completion_events.jsonl").resolve()),
                },
            )
            _require_properties(properties, {
                "stop_file": str(stop_file.resolve()),
                "event_path": str((output / "completion_events.jsonl").resolve()),
                "warmup_seconds": "60",
                "maximum_measurement_seconds": "600",
                "duration_seconds": "660",
            }, "JOB")
            return (
                str(java), "-cp", os.pathsep.join((str(classes), str(connector))),
                "com.formal.FormalSQLWorkload", "measure",
                str(output / "private" / "formal_job.properties"),
            )

        return FormalJobContinuousSession(
            output=output, command_builder=command,
            resource_samples=self.services.resource_samples,
            database_qps_samples=self._database_qps_samples,
            measurement_protocol=protocol, cwd=cwd,
            throughput_source="formal_sql_completion_jsonl",
            **({"process_factory": self.services.process_factory}
               if self.services.process_factory is not None else {}),
        )

    def _hybench(
        self, output: Path, cwd: Path, protocol: Mapping[str, Any], java: Path,
        benchmark: str,
    ):
        classes = _directory(self.registration["launch"], "formal_classes")
        client = _artifact(self.registration, "formal_client_class")
        expected_client = (classes / "com/hybench/HyBenchFormalMain.class").resolve()
        if client != expected_client:
            raise FormalSessionFactoryError("HyBench formal client is outside registered class root")
        connector = _artifact(self.registration, "connector_jar")
        workload_jar = _artifact(self.registration, "workload_jar")
        statements = _artifact(self.registration, "statements")
        template = _artifact(self.registration, "config_template")
        mode = "ap" if benchmark == "hybench_ap" else "xp"

        def command(stop_file: Path) -> Sequence[str]:
            properties = _private_config(
                template, output / "private" / "formal_hybench.properties",
                {_STOP_TOKEN: str(stop_file.resolve())},
            )
            duration_key = "apRunSecs" if mode == "ap" else "xpRunSecs"
            _require_properties(properties, {
                "formal_stop_file": str(stop_file.resolve()),
                "formal_warmup_seconds": "60",
                "formal_maximum_measurement_seconds": "600",
                duration_key: "660",
            }, "HyBench")
            classpath = os.pathsep.join(
                (str(classes), str(connector), str(workload_jar))
            )
            return (
                str(java), "-cp", classpath, "com.hybench.HyBenchFormalMain",
                mode, str(output / "private" / "formal_hybench.properties"),
                str(statements),
            )

        return FormalHyBenchContinuousSession(
            output=output, workload=mode, command_builder=command,
            resource_samples=self.services.resource_samples,
            database_qps_samples=self._database_qps_samples,
            measurement_protocol=protocol, cwd=cwd,
            throughput_source="hybench_formal_sample_json",
            **({"process_factory": self.services.process_factory}
               if self.services.process_factory is not None else {}),
        )

    def _benchbase(
        self, output: Path, cwd: Path, protocol: Mapping[str, Any], java: Path,
        benchmark: str,
    ):
        jar = _artifact(self.registration, "benchbase_jar")
        config = _artifact(self.registration, "config")
        try:
            tree = ET.parse(config)
        except ET.ParseError as error:
            raise FormalSessionFactoryError("BenchBase config XML is invalid") from error
        times = [
            (node.text or "").strip()
            for node in tree.getroot().findall(".//works/work/time")
        ]
        if not times or any(value != "660" for value in times):
            raise FormalSessionFactoryError("BenchBase work time must be exactly 660 seconds")
        java_prefix: list[str] = [str(java)]
        if benchmark == "tpch":
            if not callable(self.services.tpch_correctness):
                raise FormalSessionFactoryError(
                    "TPC-H correctness provider is required"
                )
            seed_agent = _artifact(self.registration, "seed_agent_jar")
            java_prefix.append(f"-javaagent:{seed_agent}")
            correctness = self.services.tpch_correctness
            invariant = None
        else:
            correctness = None
            invariant = self._tpcc_invariant_provider()

        def command(stop_file: Path) -> Sequence[str]:
            # Stop-file plumbing is through the three BENCHBASE_FORMAL_*
            # environment variables installed by the session adapter.
            if stop_file != output / "formal_benchbase.stop":
                raise FormalSessionFactoryError("BenchBase stop-file wiring differs")
            result_root = output / "benchbase_results"
            return (
                *java_prefix, "-jar", str(jar), "-b", benchmark, "-c", str(config),
                "--create=false", "--load=false", "--execute=true",
                "-d", str(result_root),
            )

        return FormalBenchBaseContinuousSession(
            output=output, benchmark=benchmark, command_builder=command,
            resource_samples=self.services.resource_samples,
            database_qps_samples=self._database_qps_samples,
            measurement_protocol=protocol, tpch_correctness=correctness,
            tpcc_invariant=invariant, cwd=cwd,
            throughput_source="benchbase_formal_completed_total",
            **({"process_factory": self.services.process_factory}
               if self.services.process_factory is not None else {}),
        )

    def _tpcc_invariant_provider(self):
        environment = self.registration.get("tpcc_environment")
        if not isinstance(environment, Mapping) or environment.get("ready") is not True:
            raise FormalSessionFactoryError(
                "NOT_READY: TPC-C frozen environment is absent"
            )
        for field in ("freeze_id", "resource_envelope_freeze_id", "template_freeze_id"):
            if not isinstance(environment.get(field), str) or not environment[field].strip():
                raise FormalSessionFactoryError(
                    f"NOT_READY: TPC-C environment lacks {field}"
                )
        _artifact(self.registration, "environment_manifest")
        invariant = self.registration.get("tpcc_frozen_invariant")
        if not isinstance(invariant, Mapping) or invariant.get("immutable") is not True:
            raise FormalSessionFactoryError(
                "NOT_READY: TPC-C frozen invariant registration is absent"
            )
        if not callable(self.services.tpcc_invariant):
            raise FormalSessionFactoryError(
                "NOT_READY: TPC-C frozen invariant provider is absent"
            )
        return self.services.tpcc_invariant


def build_allocated_session_factories(
    registrations: Mapping[str, Mapping[str, Any]],
    services: Mapping[str, FormalSessionRuntimeServices],
) -> Mapping[str, ProductionContinuousSessionFactory]:
    """Build exactly the four allocated factories; never infer TPC-C readiness."""

    missing = [
        name for name in ALLOCATED_BENCHMARKS
        if name not in registrations or name not in services
    ]
    if missing:
        raise FormalSessionFactoryError(
            f"allocated continuous-session registrations/services missing: {missing}"
        )
    return {
        name: ProductionContinuousSessionFactory(registrations[name], services[name])
        for name in ALLOCATED_BENCHMARKS
    }
