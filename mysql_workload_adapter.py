"""Isolated, opt-in adapters for replaying real MySQL workloads.

Every adapter is dry-run by default.  Configuration and results are written only
below the caller supplied output directory; shared benchmark files are never
edited.  Commands are argv lists (never a shell string).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from dataclasses import dataclass, asdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class MySQLEndpoint:
    host: str
    port: int
    database: str
    user: str
    password: str = ""

    def __post_init__(self) -> None:
        if not self.host or not self.database or not self.user:
            raise ValueError("endpoint host, database and user must be explicit")
        if not 1 <= int(self.port) <= 65535:
            raise ValueError("endpoint port must be in 1..65535")


@dataclass(frozen=True)
class WorkloadMetrics:
    qps: float
    latency_p95_ms: float
    latency_p99_ms: float
    explain_samples: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["explain_samples"] = list(self.explain_samples)
        return value


def _output_dir(path: Path | str) -> Path:
    output = Path(path).resolve()
    if output.exists() and not output.is_dir():
        raise ValueError(f"output-dir is not a directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


_JOB_WRITE_RE = re.compile(
    r"(?is)\b(?:insert|update|delete|replace|merge|create|alter|drop|truncate|rename|"
    r"grant|revoke|call|do|handler|load|lock|unlock|set|use|into\s+outfile|"
    r"into\s+dumpfile)\b"
)


def _validate_job_sql(sql: str, path: Path) -> str:
    value = sql.strip()
    if not re.match(r"(?is)^(?:select|with)\b", value):
        raise ValueError(f"JOB query must be read-only SELECT/WITH: {path}")
    body = value[:-1] if value.endswith(";") else value
    if ";" in body or _JOB_WRITE_RE.search(body):
        raise ValueError(f"JOB query contains a write or multiple statements: {path}")
    return body + ";"


def _mysqlize_job_sql(sql: str) -> str:
    """Quote JOB output aliases that are reserved words in MySQL 8."""
    return re.sub(r"(?i)\bAS\s+character\b", "AS `character`", sql)


def _run(
    argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout_s: float,
    runner: Runner,
) -> subprocess.CompletedProcess[str]:
    return runner(
        list(argv), cwd=str(cwd), env=dict(env), text=True,
        capture_output=True, timeout=timeout_s, check=False,
    )


def parse_benchbase_summary(payload: Mapping[str, Any] | Path | str) -> WorkloadMetrics:
    if isinstance(payload, (str, Path)):
        payload = json.loads(Path(payload).read_text(encoding="utf-8"))
    distribution = payload.get("Latency Distribution")
    if not isinstance(distribution, Mapping):
        raise ValueError("BenchBase summary has no Latency Distribution")
    try:
        qps = float(payload["Throughput (requests/second)"])
        p95 = float(distribution["95th Percentile Latency (microseconds)"]) / 1000.0
        p99 = float(distribution["99th Percentile Latency (microseconds)"]) / 1000.0
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid BenchBase throughput/latency fields") from exc
    if not all(math.isfinite(value) and value >= 0 for value in (qps, p95, p99)):
        raise ValueError("BenchBase metrics must be finite and non-negative")
    return WorkloadMetrics(qps, p95, p99)


def generate_benchbase_xml(
    benchmark: str, endpoint: MySQLEndpoint, output_dir: Path | str, *,
    duration_s: int = 60, terminals: int = 4, scale_factor: float = 1.0,
    transaction_weights: Sequence[float] | None = None,
    complete_query_set: bool = False,
    query_order: Sequence[int] | None = None,
) -> Path:
    benchmark = benchmark.lower()
    if benchmark not in {"tpcc", "tpch"}:
        raise ValueError("BenchBase adapter supports only tpcc and tpch")
    if duration_s <= 0 or terminals <= 0 or scale_factor <= 0:
        raise ValueError("duration, terminals and scale factor must be positive")
    if complete_query_set and benchmark != "tpch":
        raise ValueError("complete-query-set mode is supported only for tpch")
    if complete_query_set and terminals != 1:
        raise ValueError("TPC-H complete-query-set mode requires exactly one terminal")
    if query_order is not None:
        if benchmark != "tpch":
            raise ValueError("query_order is supported only for tpch")
        order = [int(value) for value in query_order]
        if (
            not order or len(order) != len(set(order))
            or not set(order) <= set(range(1, 23))
            or (not complete_query_set and set(order) != set(range(1, 23)))
        ):
            raise ValueError(
                "TPC-H query_order must contain unique IDs in 1..22; "
                "subsets require complete-query-set mode"
            )
    else:
        order = list(range(1, 23))
    root = ET.Element("parameters")
    fields = {
        "type": "MYSQL", "driver": "com.mysql.cj.jdbc.Driver",
        "url": f"jdbc:mysql://{endpoint.host}:{endpoint.port}/{endpoint.database}?useServerPrepStmts=true&allowLoadLocalInfile=true",
        "username": endpoint.user, "password": endpoint.password,
        "reconnectOnConnectionFailure": "true", "isolation": "TRANSACTION_READ_COMMITTED",
        "batchsize": "128" if benchmark == "tpcc" else "1024",
        "scalefactor": str(scale_factor), "terminals": str(terminals),
    }
    for key, value in fields.items():
        ET.SubElement(root, key).text = value
    if benchmark == "tpch":
        # BenchBase deliberately keeps the TPC-H indexes out of the DDL so
        # bulk loading remains fast.  Omitting this hook produces a valid but
        # effectively unusable schema whose long scans dominate every tuning
        # decision.
        ET.SubElement(root, "afterload").text = "/benchmarks/tpch/postload-mysql.sql"
    works = ET.SubElement(root, "works")
    work = ET.SubElement(works, "work")
    ET.SubElement(work, "rate").text = "unlimited"
    names = (["NewOrder", "Payment", "OrderStatus", "Delivery", "StockLevel"]
             if benchmark == "tpcc" else [f"Q{i}" for i in order])
    if transaction_weights is None:
        weights = ([45, 43, 4, 4, 4] if benchmark == "tpcc" else [1] * len(names))
    else:
        weights = [float(value) for value in transaction_weights]
        if len(weights) != len(names):
            raise ValueError(f"{benchmark} requires exactly {len(names)} transaction weights")
        if any(not math.isfinite(value) or value < 0.0 for value in weights) or not any(weights):
            raise ValueError("transaction weights must be finite, non-negative, and not all zero")
    if complete_query_set and not all(value > 0.0 for value in weights):
        raise ValueError("TPC-H complete-query-set mode requires every selected query")
    if benchmark == "tpch" and (complete_query_set or all(value > 0.0 for value in weights)):
        ET.SubElement(work, "serial").text = "true"
    # BenchBase gives serial phases two different meanings.  A serial phase
    # with <time> loops until the timer expires and may stop before Q22.  An
    # untimed serial phase executes every enabled query exactly once.  The
    # latter is the only mode that can provide fail-closed AP SQL coverage.
    if not complete_query_set:
        ET.SubElement(work, "time").text = str(duration_s)
    ET.SubElement(work, "weights").text = ",".join(f"{value:g}" for value in weights)
    transactiontypes = ET.SubElement(root, "transactiontypes")
    for index, name in enumerate(names, 1):
        item = ET.SubElement(transactiontypes, "transactiontype")
        ET.SubElement(item, "name").text = name
        # Do not assign the canonical TPC-H query number as BenchBase's
        # internal transaction id.  Complete-suite lanes intentionally carry
        # sparse and reordered query lists (for example, a one-query Q9
        # lane).  BenchBase's ResultWriter treats the transaction id as a
        # dense, one-based list index, so an explicit id=9 for that lane makes
        # the query finish successfully and then crashes while writing the raw
        # result.  Leaving <id> absent gives the lane dense ids 1..N while the
        # procedure name still selects the exact Q1..Q22 implementation and
        # preserves the frozen lane order.
    ET.indent(root)
    path = _output_dir(output_dir) / f"benchbase_{benchmark}.xml"
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    path.chmod(0o600)
    return path


def run_benchbase(
    benchmark: str, endpoint: MySQLEndpoint, output_dir: Path | str, *,
    jar_path: Path | str, duration_s: int = 60, terminals: int = 4,
    scale_factor: float = 1.0, dry_run: bool = True, timeout_s: float = 900,
    transaction_weights: Sequence[float] | None = None,
    complete_query_set: bool = False,
    query_order: Sequence[int] | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    output = _output_dir(output_dir)
    jar = Path(jar_path).resolve()
    config = generate_benchbase_xml(benchmark, endpoint, output, duration_s=duration_s,
                                    terminals=terminals, scale_factor=scale_factor,
                                    transaction_weights=transaction_weights,
                                    complete_query_set=complete_query_set,
                                    query_order=query_order)
    argv = ["java", "-jar", str(jar), "-b", benchmark.lower(), "-c", str(config),
            "--execute=true", "-d", str(output)]
    plan = {"adapter": "benchbase", "dry_run": dry_run, "argv": argv,
            "cwd": str(jar.parent), "config": str(config), "output_dir": str(output)}
    _write(output / "command.json", json.dumps(plan, indent=2) + "\n")
    if dry_run:
        return plan
    if not jar.is_file():
        raise FileNotFoundError(jar)
    completed = _run(argv, cwd=jar.parent, env=os.environ, timeout_s=timeout_s, runner=runner)
    _write(output / "stdout.log", completed.stdout or "")
    _write(output / "stderr.log", completed.stderr or "")
    if completed.returncode:
        raise RuntimeError(f"BenchBase exited {completed.returncode}; see {output}")
    summaries = sorted(output.glob("*.summary.json"), key=lambda item: item.stat().st_mtime)
    if not summaries:
        raise RuntimeError(f"BenchBase produced no summary JSON below {output}")
    result = plan | {"dry_run": False, "summary": str(summaries[-1]),
                     "metrics": parse_benchbase_summary(summaries[-1]).as_dict()}
    if complete_query_set:
        raw_files = sorted(output.glob("*.raw.csv"), key=lambda item: item.stat().st_mtime)
        if not raw_files:
            raise RuntimeError(f"BenchBase produced no raw CSV below {output}")
        with raw_files[-1].open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        completed = {
            str(row.get("Transaction Name", "")).strip()
            for row in rows
            if str(row.get("Transaction Name", "")).strip()
        }
        required_ids = list(query_order) if query_order is not None else list(range(1, 23))
        required = {f"Q{query_id}" for query_id in required_ids}
        missing = sorted(required - completed, key=lambda value: int(value[1:]))
        unexpected = sorted(completed - required)
        coverage = {
            "required_queries": sorted(required, key=lambda value: int(value[1:])),
            "completed_queries": sorted(completed & required, key=lambda value: int(value[1:])),
            "missing_queries": missing,
            "unexpected_transaction_names": unexpected,
            "coverage_complete": not missing,
            "raw_csv": str(raw_files[-1]),
        }
        if missing:
            _write(output / "query_coverage.json", json.dumps(coverage, indent=2) + "\n")
            raise RuntimeError(f"TPC-H query coverage incomplete; missing {missing}; see {output}")
        result["query_coverage"] = coverage
        _write(output / "query_coverage.json", json.dumps(coverage, indent=2) + "\n")
    _write(output / "adapter_result.json", json.dumps(result, indent=2) + "\n")
    return result


_RATE_RE = re.compile(r"(?i)\b(?:qps|tps|throughput)\b\s*(?:is|:|=)\s*([0-9]+(?:\.[0-9]+)?)")
_P95_RE = re.compile(r"(?i)(?:\bp95\b|\b95(?:th)?\s*percentile\b|95%\s*rt)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(us|ms|s)?")
_P99_RE = re.compile(r"(?i)(?:\bp99\b|\b99(?:th)?\s*percentile\b|99%\s*rt)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(us|ms|s)?")


def _milliseconds(value: str, unit: str | None) -> float:
    number = float(value)
    return number / 1000.0 if (unit or "ms").lower() == "us" else number * 1000.0 if unit == "s" else number


def parse_hybench_stdout(stdout: str) -> WorkloadMetrics:
    rates = [float(value) for value in _RATE_RE.findall(stdout)]
    p95s = [_milliseconds(value, unit) for value, unit in _P95_RE.findall(stdout)]
    p99s = [_milliseconds(value, unit) for value, unit in _P99_RE.findall(stdout)]
    if not rates or not p95s or not p99s:
        raise ValueError("HyBench stdout lacks QPS/TPS, P95 or P99")
    return WorkloadMetrics(rates[-1], max(p95s), max(p99s))


def parse_hybench_mode_metrics(stdout: str, mode: str) -> WorkloadMetrics:
    """Parse the authoritative summary rate for one native HyBench mode."""
    mode = mode.lower()
    labels = {"ap": "QPS", "tp": "TPS", "xp": "XP-QPS"}
    if mode not in labels:
        raise ValueError("HyBench mode must be ap, tp or xp")
    label = re.escape(labels[mode])
    # A leading token boundary prevents plain QPS/TPS from matching XP-QPS/TPS.
    rates = [float(value) for value in re.findall(
        rf"(?im)(?<![-\w]){label}\s*(?:is|:|=)\s*([0-9]+(?:\.[0-9]+)?)",
        stdout,
    )]
    if mode == "xp" and not rates:
        for encoded in re.findall(r"(?m)^HYBENCH_METRICS_JSON=(\{[^\n]+\})$", stdout):
            try:
                rates.append(float(json.loads(encoded)["qps"]))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
    p95s = [_milliseconds(value, unit) for value, unit in _P95_RE.findall(stdout)]
    p99s = [_milliseconds(value, unit) for value, unit in _P99_RE.findall(stdout)]
    if not rates or not p95s or not p99s:
        raise ValueError(f"HyBench {mode} output lacks its summary rate or percentiles")
    return WorkloadMetrics(rates[-1], max(p95s), max(p99s))


def parse_hybench_formal_metrics(stdout: str) -> tuple[WorkloadMetrics, dict[str, Any]]:
    """Parse the adaptive wrapper's censored-at-boundary XP metrics."""
    documents: list[dict[str, Any]] = []
    for encoded in re.findall(r"(?m)^HYBENCH_FORMAL_METRICS_JSON=(\{[^\n]+\})$", stdout):
        try:
            document = json.loads(encoded)
        except json.JSONDecodeError:
            continue
        if isinstance(document, dict):
            documents.append(document)
    if not documents:
        raise ValueError("HyBench adaptive output lacks HYBENCH_FORMAL_METRICS_JSON")
    formal = documents[-1]
    try:
        metrics = WorkloadMetrics(
            float(formal["xap_qps"]),
            float(formal["latency_p95_ms"]),
            float(formal["latency_p99_ms"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("HyBench adaptive formal metrics are incomplete") from error
    return metrics, formal


def generate_hybench_properties(
    endpoint: MySQLEndpoint, output_dir: Path | str, *, duration_s: int = 60,
    clients: int = 4, extra: Mapping[str, str | int | float] | None = None,
    mode: str | None = None,
) -> Path:
    if duration_s <= 0 or clients <= 0:
        raise ValueError("duration and clients must be positive")
    jdbc_url = (
        f"jdbc:mysql://{endpoint.host}:{endpoint.port}/{endpoint.database}"
        "?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=UTC"
    )
    run_minutes = max(1, math.ceil(duration_s / 60.0))
    overrides = dict(extra or {})
    if mode == "xp":
        forbidden_exact = {
            "ap_query_ids", "ap_query_weights", "ap_query_sequence",
            "ap_query_sequence_limit", "fresh_percent",
        }
        forbidden_mix = {
            key for key in overrides
            if re.fullmatch(r"(?:at[1-6]|tp(?:[1-9]|1[0-8]))_percent", key)
        }
        forbidden = sorted((forbidden_exact & overrides.keys()) | forbidden_mix)
        if forbidden:
            raise ValueError(
                "native HyBench XP forbids AP selection and AT/TP mix overrides: "
                + ", ".join(forbidden)
            )
        ratio_keys = {"xapclient", "xtpclient"} & overrides.keys()
        if ratio_keys and ratio_keys != {"xapclient", "xtpclient"}:
            raise ValueError("native HyBench XP requires xapclient and xtpclient together")
        if ratio_keys:
            try:
                xap_clients = int(overrides["xapclient"])
                xtp_clients = int(overrides["xtpclient"])
            except (TypeError, ValueError) as exc:
                raise ValueError("native HyBench XP client counts must be integers") from exc
            if (
                xap_clients <= 0 or xtp_clients <= 0
                or str(xap_clients) != str(overrides["xapclient"])
                or str(xtp_clients) != str(overrides["xtpclient"])
            ):
                raise ValueError("native HyBench XP client counts must be positive integers")
            # The 2026-08-06 workload freeze derives its baseline from the
            # last sealed 8-XAP/8-XTP contract.  Absolute counts may scale,
            # but the reduced 1:1 ratio is immutable.
            if xap_clients != xtp_clients:
                raise ValueError("native HyBench XP must preserve the frozen 1:1 XAP:XTP ratio")
    values: dict[str, Any] = {
        "db": "mysql", "classname": "com.mysql.jdbc.Driver",
        "username": endpoint.user, "password": endpoint.password,
        "url": jdbc_url, "url_ap": jdbc_url,
        "classname_ap": "com.mysql.jdbc.Driver",
        "username_ap": endpoint.user, "password_ap": endpoint.password,
        "sf": "1x", "apclient": clients, "tpclient": clients,
        "xapclient": clients, "xtpclient": clients,
        "apRunMins": run_minutes, "tpRunMins": run_minutes,
        "xpRunMins": run_minutes, "apround": 1,
        "at1_percent": 35, "at2_percent": 25, "at3_percent": 15,
        "at4_percent": 15, "at5_percent": 7, "at6_percent": 3,
        "risk_rate": 0.1, "fresh_interval": 20, "contention_num": 100,
        "dist": "uniform", "power_alpha": 0.1,
    }
    values.update(overrides)
    if any("\n" in str(key) or "\n" in str(value) for key, value in values.items()):
        raise ValueError("HyBench property keys/values cannot contain newlines")
    text = "".join(f"{key}={value}\n" for key, value in sorted(values.items()))
    path = _output_dir(output_dir) / "hybench_mysql.properties"
    _write(path, text)
    path.chmod(0o600)
    return path


def run_hybench(
    mode: str, endpoint: MySQLEndpoint, output_dir: Path | str, *,
    hybench_home: Path | str, duration_s: int = 60, clients: int = 4,
    extra_properties: Mapping[str, str | int | float] | None = None,
    runtime_jar: Path | str | None = None,
    formal_control: Mapping[str, Any] | None = None,
    dry_run: bool = True, timeout_s: float = 900, runner: Runner = subprocess.run,
) -> dict[str, Any]:
    mode = mode.lower()
    modes = {"ap": "runap", "tp": "runtp", "xp": "runxp"}
    if mode not in modes:
        raise ValueError("HyBench mode must be ap, tp or xp")
    output = _output_dir(output_dir)
    home = Path(hybench_home).resolve()
    script = home / "hybench"
    bound_runtime_jar = Path(runtime_jar).resolve() if runtime_jar is not None else None
    if mode == "xp" and (bound_runtime_jar is None or not bound_runtime_jar.is_file()):
        raise ValueError("native HyBench XP requires a sealed runtime JAR")
    control: dict[str, Any] | None = None
    if formal_control is not None:
        if mode != "xp":
            raise ValueError("adaptive formal control is supported only for native XP")
        control = dict(formal_control)
        required = {"runner_jar", "stop_file"}
        if not required.issubset(control):
            raise ValueError("adaptive formal control requires runner_jar and stop_file")
        control.setdefault("minimum_measurement_seconds", 300)
        control.setdefault("maximum_measurement_seconds", 900)
        control.setdefault("graceful_quiescence_seconds", 60)
        if (
            int(control["minimum_measurement_seconds"]) != 300
            or int(control["maximum_measurement_seconds"]) != 900
            or not 30 <= int(control["graceful_quiescence_seconds"]) <= 120
            or duration_s != 900
        ):
            raise ValueError("adaptive native XP requires 300..900 seconds and duration_s=900")
        control["runner_jar"] = str(Path(control["runner_jar"]).resolve())
        control["stop_file"] = str(Path(control["stop_file"]).resolve())
        if not Path(control["runner_jar"]).is_file():
            raise ValueError("adaptive native XP runner JAR is missing")
        if not Path(control["stop_file"]).is_absolute():
            raise ValueError("adaptive native XP stop file must be absolute")
        if Path(control["stop_file"]).exists():
            raise ValueError("adaptive native XP stop file already exists")
    if mode == "xp":
        authority_path = bound_runtime_jar.parent.parent / "manifest.json"
        if not authority_path.is_file():
            raise ValueError("native HyBench XP runtime lacks an execution-authority manifest")
        try:
            authority = json.loads(authority_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("native HyBench XP execution-authority manifest is invalid") from exc
        runtime_sha = hashlib.sha256(bound_runtime_jar.read_bytes()).hexdigest()
        iq7_semantics = (
            authority.get("workload_semantics", {}).get("iq_selection")
            == "uniform_random_IQ1_IQ7"
            and authority.get("workload_semantics", {}).get("exclusive_random_range")
            == [1, 8]
        )
        externally_authorized_iq7 = False
        if iq7_semantics:
            workload_authority_path = (
                ROOT / "config/formal"
                / "frozen_mysql_four_scene_native_xp_iq1_iq7_20260809_v1"
                / "workload_successor.json"
            )
            if workload_authority_path.is_file():
                workload_authority = json.loads(
                    workload_authority_path.read_text(encoding="utf-8")
                )
                native = workload_authority.get("native_hybench_xp", {})
                externally_authorized_iq7 = (
                    workload_authority.get("status") == "FROZEN"
                    and workload_authority.get("execution_authorized") is True
                    and native.get("iq_selection") == "uniform_random_IQ1_IQ7"
                    and native.get("runtime_manifest", {}).get("sha256")
                    == hashlib.sha256(authority_path.read_bytes()).hexdigest()
                    and native.get("runtime_jar", {}).get("sha256") == runtime_sha
                )
        if (
            authority.get("runtime_jar_sha256") != runtime_sha
            or not (authority.get("execution_authorized") is True or externally_authorized_iq7)
        ):
            raise ValueError("native HyBench XP runtime is not execution-authorized")
    property_overrides = dict(extra_properties or {})
    if control is not None:
        property_overrides.update({
            "formal_warmup_seconds": 0,
            "formal_minimum_measurement_seconds": int(control["minimum_measurement_seconds"]),
            "formal_maximum_measurement_seconds": int(control["maximum_measurement_seconds"]),
            "formal_graceful_quiescence_seconds": int(control["graceful_quiescence_seconds"]),
            "formal_stop_file": control["stop_file"],
            "xpRunSecs": duration_s,
        })
    props = generate_hybench_properties(endpoint, output, duration_s=duration_s,
                                         clients=clients, extra=property_overrides,
                                         mode=mode)
    statement_source = home / "conf" / "stmt_mysql.toml"
    statement_file = statement_source
    # The upstream MySQL statements mix upper/lower-case table identifiers.
    # Linux MySQL installations commonly use lower_case_table_names=0, so
    # CUSTOMER and customer are different tables.  Keep the shared install
    # read-only and write a private, reproducible case-normalized statement
    # file for this run.
    if not dry_run:
        if not statement_source.is_file():
            raise FileNotFoundError(statement_source)
        statement_text = statement_source.read_text(encoding="utf-8")
        canonical_tables = (
            "checkingAccount", "savingAccount", "company", "customer",
            "checking", "loanapps", "loantrans", "transfer",
        )
        for table in canonical_tables:
            statement_text = re.sub(rf"\b{re.escape(table)}\b", table, statement_text,
                                    flags=re.IGNORECASE)
        statement_file = output / "stmt_mysql_case_normalized.toml"
        _write(statement_file, statement_text)
    argv = ["bash", str(script), "-t", modes[mode], "-c", str(props), "-f", str(statement_file)]
    env = dict(os.environ)
    env["HYBENCH_CONFIG"] = str(props)
    env["HYBENCH_OUTPUT_DIR"] = str(output)
    plan = {"adapter": "hybench", "mode": mode, "dry_run": dry_run, "argv": argv,
            "cwd": str(home), "properties": str(props), "output_dir": str(output)}
    if bound_runtime_jar is not None:
        plan["runtime_jar"] = str(bound_runtime_jar)
        plan["runtime_jar_sha256"] = hashlib.sha256(bound_runtime_jar.read_bytes()).hexdigest()
    if control is not None:
        plan["formal_control"] = control
        plan["formal_runner_jar_sha256"] = hashlib.sha256(
            Path(control["runner_jar"]).read_bytes()
        ).hexdigest()
    if mode == "xp" and control is None:
        preview_runtime = output / "hybench_runtime"
        argv = [
            "java", "-cp", f"{bound_runtime_jar}:{preview_runtime / 'lib'}/*",
            "com.hybench.HyBench", "-t", modes[mode], "-c", str(props),
            "-f", str(statement_file),
        ]
        plan["argv"] = argv
    elif mode == "xp":
        preview_runtime = output / "hybench_runtime"
        argv = [
            "java", "-cp",
            f"{control['runner_jar']}:{bound_runtime_jar}:{preview_runtime / 'lib'}/*",
            "com.hybench.HyBenchAdaptiveMain", mode, str(props), str(statement_file),
        ]
        plan["argv"] = argv
    _write(output / "command.json", json.dumps(plan, indent=2) + "\n")
    if dry_run:
        return plan
    if mode != "xp" and not script.is_file():
        raise FileNotFoundError(script)
    # HyBench writes hybench-error.log relative to cwd.  Give every logical run
    # a private runtime cwd so concurrent AP/TP/XP runs never share that file.
    runtime = output / "hybench_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    for name in ("lib", f"Data_{(extra_properties or {}).get('sf', '1x')}"):
        source = home / name
        target = runtime / name
        if not source.exists():
            raise FileNotFoundError(source)
        if not target.exists():
            target.symlink_to(source, target_is_directory=True)
    log_source = home / "conf" / "log4j2.properties"
    if not log_source.is_file():
        raise FileNotFoundError(log_source)
    _write(runtime / "conf" / "log4j2.properties", log_source.read_text(encoding="utf-8"))
    if mode == "xp" and control is None:
        argv = [
            "java", "-cp", f"{bound_runtime_jar}:{runtime / 'lib'}/*",
            "com.hybench.HyBench", "-t", modes[mode], "-c", str(props),
            "-f", str(statement_file),
        ]
        plan["argv"] = argv
    elif mode == "xp":
        argv = [
            "java", "-cp",
            f"{control['runner_jar']}:{bound_runtime_jar}:{runtime / 'lib'}/*",
            "com.hybench.HyBenchAdaptiveMain", mode, str(props), str(statement_file),
        ]
        plan["argv"] = argv
    plan["cwd"] = str(runtime)
    _write(output / "command.json", json.dumps(plan, indent=2) + "\n")
    completed = _run(argv, cwd=runtime, env=env, timeout_s=timeout_s, runner=runner)
    _write(output / "stdout.log", completed.stdout or "")
    _write(output / "stderr.log", completed.stderr or "")
    if completed.returncode:
        raise RuntimeError(f"HyBench exited {completed.returncode}; see {output}")
    runtime_error_log = runtime / "hybench-error.log"
    error_text = "\n".join((completed.stdout or "", completed.stderr or "",
                             runtime_error_log.read_text(encoding="utf-8", errors="replace")
                             if runtime_error_log.is_file() else ""))
    if re.search(r"java\.sql\.[A-Za-z]*Exception|SQLSyntaxErrorException", error_text):
        raise RuntimeError(f"HyBench reported a SQL/JDBC exception; see {output}")
    if re.search(r"java\.lang\.NoSuchMethodException|ReflectiveOperationException", error_text):
        raise RuntimeError(f"HyBench reported a missing workload method; see {output}")
    if control is not None:
        parsed_metrics, formal_metrics = parse_hybench_formal_metrics(completed.stdout or "")
    else:
        parsed_metrics = parse_hybench_mode_metrics(completed.stdout, mode)
        formal_metrics = None
    result = plan | {"dry_run": False, "metrics": parsed_metrics.as_dict()}
    if formal_metrics is not None:
        result["formal_metrics"] = formal_metrics
    if mode == "xp":
        completed_iq = {int(value) for value in re.findall(
            r"(?m)^Interative Query (\d+)\s*:", completed.stdout or ""
        )}
        completed_at = {int(value) for value in re.findall(
            r"(?m)^Analytical Transaction AT(\d+)\s*:", completed.stdout or ""
        )}
        required_iq = set(range(1, 8)) if iq7_semantics else set(range(1, 7))
        required_at = set(range(1, 7))
        coverage = {
            "required_iq": [f"IQ{value}" for value in sorted(required_iq)],
            "completed_iq": [f"IQ{value}" for value in sorted(completed_iq & required_iq)],
            "required_at": [f"AT{value}" for value in sorted(required_at)],
            "completed_at": [f"AT{value}" for value in sorted(completed_at & required_at)],
            "unexpected_iq": sorted(completed_iq - required_iq),
            "unexpected_at": sorted(completed_at - required_at),
            "coverage_complete": completed_iq == required_iq and completed_at == required_at,
        }
        _write(output / "native_xp_coverage.json", json.dumps(coverage, indent=2) + "\n")
        if not coverage["coverage_complete"]:
            raise RuntimeError(f"native HyBench XP IQ/AT coverage is incomplete; see {output}")
        result["workload_coverage"] = coverage
    _write(output / "adapter_result.json", json.dumps(result, indent=2) + "\n")
    return result


def build_job_manifest(sql_files: Iterable[Path | str], output_dir: Path | str) -> Path:
    rows = []
    for raw in sql_files:
        path = Path(raw).resolve()
        sql = path.read_text(encoding="utf-8").strip()
        _validate_job_sql(sql, path)
        rows.append({"path": str(path), "sha256": hashlib.sha256(sql.encode()).hexdigest()})
    if not rows:
        raise ValueError("JOB manifest needs at least one SQL file")
    manifest = _output_dir(output_dir) / "job_sql_manifest.json"
    _write(manifest, json.dumps({"queries": rows}, indent=2) + "\n")
    return manifest


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def run_job(
    manifest_path: Path | str, endpoint: MySQLEndpoint, output_dir: Path | str, *,
    concurrency: int = 4, repetitions: int = 1, mysql_binary: Path | str = "mysql",
    dry_run: bool = True, timeout_s: float = 900, runner: Runner = subprocess.run,
    estimated_duration_s: Mapping[str, float] | None = None,
    heavy_query_threshold_s: float | None = None,
    max_concurrent_heavy_queries: int = 1,
    dedicated_heavy_pool: bool = False,
    heavy_start_stagger_s: float = 0.0,
    heavy_query_order: str = "shortest_first",
    capture_explain: bool = True,
) -> dict[str, Any]:
    if concurrency <= 0 or repetitions <= 0:
        raise ValueError("concurrency and repetitions must be positive")
    output = _output_dir(output_dir)
    manifest_path = Path(manifest_path).resolve()
    rows = json.loads(manifest_path.read_text(encoding="utf-8")).get("queries", [])
    queries: list[tuple[Path, str]] = []
    for row in rows:
        path = Path(row["path"]).resolve()
        sql = path.read_text(encoding="utf-8").strip()
        if hashlib.sha256(sql.encode()).hexdigest() != row["sha256"]:
            raise ValueError(f"JOB query changed after manifest creation: {path}")
        queries.append((path, _mysqlize_job_sql(_validate_job_sql(sql, path))))
    if not queries:
        raise ValueError("JOB manifest contains no queries")
    estimates = {str(key): float(value) for key, value in (estimated_duration_s or {}).items()}
    if any(not math.isfinite(value) or value < 0 for value in estimates.values()):
        raise ValueError("JOB duration estimates must be finite and non-negative")
    if heavy_query_threshold_s is not None and heavy_query_threshold_s <= 0:
        raise ValueError("JOB heavy-query threshold must be positive")
    if max_concurrent_heavy_queries <= 0:
        raise ValueError("maximum concurrent JOB heavy queries must be positive")
    if heavy_start_stagger_s < 0 or not math.isfinite(heavy_start_stagger_s):
        raise ValueError("JOB heavy-query start stagger must be finite and non-negative")
    if heavy_query_order not in {"shortest_first", "longest_first"}:
        raise ValueError("unsupported JOB heavy-query order")
    if dedicated_heavy_pool and heavy_query_threshold_s is None:
        raise ValueError("dedicated JOB heavy pool requires a heavy-query threshold")
    if dedicated_heavy_pool and max_concurrent_heavy_queries >= concurrency:
        raise ValueError("dedicated JOB heavy pool must leave at least one short worker")
    if estimates:
        # Shortest-first is intentional for the paired AP run.  JOB's heavy
        # tail is delayed while TPC-H drains its short prefix, preventing the
        # longest query from each suite from starting together.
        queries.sort(key=lambda item: (estimates.get(item[0].name, 0.0), item[0].name))
    base = [str(mysql_binary), "--no-defaults", "--get-server-public-key",
            "--ssl-mode=DISABLED", "--batch", "--raw", "--skip-column-names", "-h", endpoint.host,
            "-P", str(endpoint.port), "-u", endpoint.user, endpoint.database]
    plan = {"adapter": "job", "dry_run": dry_run, "manifest": str(manifest_path),
            "output_dir": str(output), "concurrency": concurrency, "repetitions": repetitions,
            "argv_prefix": base, "query_count": len(queries),
            "scheduling_policy": "shortest_estimated_first" if estimates else "manifest_order",
            "scheduled_query_order": [path.name for path, _ in queries],
            "heavy_query_threshold_s": heavy_query_threshold_s,
            "maximum_concurrent_heavy_queries": (
                max_concurrent_heavy_queries if heavy_query_threshold_s is not None else None
            ),
            "dedicated_heavy_pool": dedicated_heavy_pool,
            "heavy_start_stagger_s": heavy_start_stagger_s,
            "heavy_query_order": heavy_query_order,
            "capture_explain": capture_explain}
    _write(output / "command.json", json.dumps(plan, indent=2) + "\n")
    if dry_run:
        return plan
    env = dict(os.environ)
    if endpoint.password:
        env["MYSQL_PWD"] = endpoint.password

    heavy_slot = threading.Semaphore(max_concurrent_heavy_queries)
    heavy_start_lock = threading.Lock()
    next_heavy_start = [0.0]

    def invoke(
        name: str, sql: str,
    ) -> tuple[dict[str, Any], subprocess.CompletedProcess[str]]:
        is_heavy = (
            heavy_query_threshold_s is not None
            and estimates.get(name, 0.0) >= heavy_query_threshold_s
        )
        if is_heavy:
            heavy_slot.acquire()
        try:
            if is_heavy and heavy_start_stagger_s > 0:
                with heavy_start_lock:
                    scheduled = max(time.monotonic(), next_heavy_start[0])
                    next_heavy_start[0] = scheduled + heavy_start_stagger_s
                delay = scheduled - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
            query_started = time.monotonic()
            completed = runner(base, input=sql, cwd=str(output), env=env, text=True,
                               capture_output=True, timeout=timeout_s, check=False)
        finally:
            if is_heavy:
                heavy_slot.release()
        query_finished = time.monotonic()
        return {
            "query": name,
            "estimated_duration_s": estimates.get(name),
            "heavy": is_heavy,
            "started_offset_s": query_started - started,
            "finished_offset_s": query_finished - started,
            "elapsed_s": query_finished - query_started,
            "returncode": completed.returncode,
        }, completed

    started = time.monotonic()
    latencies: list[float] = []
    tasks = [(path.name, sql) for _ in range(repetitions) for path, sql in queries]
    errors: list[str] = []
    executions: list[dict[str, Any]] = []

    def collect(futures: Sequence[Any]) -> None:
        for future in as_completed(futures):
            execution, completed = future.result()
            executions.append(execution)
            latencies.append(float(execution["elapsed_s"]) * 1000.0)
            if completed.returncode:
                errors.append((completed.stderr or "mysql failed").strip())

    if dedicated_heavy_pool:
        heavy_tasks = [
            task for task in tasks if estimates.get(task[0], 0.0) >= heavy_query_threshold_s
        ]
        short_tasks = [
            task for task in tasks if estimates.get(task[0], 0.0) < heavy_query_threshold_s
        ]
        heavy_tasks.sort(
            key=lambda task: (estimates.get(task[0], 0.0), task[0]),
            reverse=heavy_query_order == "longest_first",
        )
        short_workers = concurrency - max_concurrent_heavy_queries
        with (
            ThreadPoolExecutor(
                max_workers=max_concurrent_heavy_queries,
                thread_name_prefix="job-heavy",
            ) as heavy_pool,
            ThreadPoolExecutor(
                max_workers=short_workers,
                thread_name_prefix="job-short",
            ) as short_pool,
        ):
            futures = [
                heavy_pool.submit(invoke, name, sql) for name, sql in heavy_tasks
            ] + [
                short_pool.submit(invoke, name, sql) for name, sql in short_tasks
            ]
            collect(futures)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            collect([pool.submit(invoke, name, sql) for name, sql in tasks])
    wall = time.monotonic() - started
    if errors:
        _write(output / "stderr.log", "\n".join(errors) + "\n")
        raise RuntimeError(f"{len(errors)} JOB queries failed; see {output}")
    explains: list[str] = []
    if capture_explain:
        for path, sql in queries:
            _, completed = invoke(path.name, "EXPLAIN FORMAT=JSON " + sql)
            if completed.returncode:
                raise RuntimeError("JOB EXPLAIN FORMAT=JSON failed")
            explains.append((completed.stdout or "").strip())
    metrics = WorkloadMetrics(len(tasks) / wall, _percentile(latencies, .95),
                              _percentile(latencies, .99), tuple(explains))
    completed_names = [path.name for path, _ in queries]
    result = plan | {
        "dry_run": False,
        "metrics": metrics.as_dict(),
        "wall_time_s": wall,
        "query_executions": sorted(
            executions, key=lambda row: (row["started_offset_s"], row["query"])
        ),
        "query_coverage": {
            "required_queries": completed_names,
            "completed_queries": completed_names,
            "missing_queries": [],
            "coverage_complete": True,
        },
    }
    _write(output / "adapter_result.json", json.dumps(result, indent=2) + "\n")
    return result
