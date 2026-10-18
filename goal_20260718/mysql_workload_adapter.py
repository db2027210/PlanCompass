"""Isolated, opt-in adapters for replaying real MySQL workloads.

Every adapter is dry-run by default.  Configuration and results are written only
below the caller supplied output directory; shared benchmark files are never
edited.  Commands are argv lists (never a shell string).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
import xml.etree.ElementTree as ET


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
    rate_limit: float | None = None,
) -> Path:
    benchmark = benchmark.lower()
    if benchmark not in {"tpcc", "tpch"}:
        raise ValueError("BenchBase adapter supports only tpcc and tpch")
    if duration_s <= 0 or terminals <= 0 or scale_factor <= 0:
        raise ValueError("duration, terminals and scale factor must be positive")
    if rate_limit is not None and (not math.isfinite(float(rate_limit)) or float(rate_limit) <= 0.0):
        raise ValueError("rate_limit must be finite and positive")
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
    ET.SubElement(work, "time").text = str(duration_s)
    ET.SubElement(work, "rate").text = (
        "unlimited" if rate_limit is None else f"{float(rate_limit):g}"
    )
    names = (["NewOrder", "Payment", "OrderStatus", "Delivery", "StockLevel"]
             if benchmark == "tpcc" else [f"Q{i}" for i in range(1, 23)])
    if transaction_weights is None:
        weights = ([45, 43, 4, 4, 4] if benchmark == "tpcc" else [1] * len(names))
    else:
        weights = [float(value) for value in transaction_weights]
        if len(weights) != len(names):
            raise ValueError(f"{benchmark} requires exactly {len(names)} transaction weights")
        if any(not math.isfinite(value) or value < 0.0 for value in weights) or not any(weights):
            raise ValueError("transaction weights must be finite, non-negative, and not all zero")
    if benchmark == "tpch" and all(value > 0.0 for value in weights):
        ET.SubElement(work, "serial").text = "true"
    ET.SubElement(work, "weights").text = ",".join(f"{value:g}" for value in weights)
    transactiontypes = ET.SubElement(root, "transactiontypes")
    for index, name in enumerate(names, 1):
        item = ET.SubElement(transactiontypes, "transactiontype")
        ET.SubElement(item, "name").text = name
        if benchmark == "tpch":
            ET.SubElement(item, "id").text = str(index)
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
    rate_limit: float | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    output = _output_dir(output_dir)
    jar = Path(jar_path).resolve()
    config = generate_benchbase_xml(benchmark, endpoint, output, duration_s=duration_s,
                                    terminals=terminals, scale_factor=scale_factor,
                                    transaction_weights=transaction_weights,
                                    rate_limit=rate_limit)
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
    if mode not in {"ap", "tp", "xp"}:
        raise ValueError("HyBench mode must be ap, tp or xp")
    labels = {"ap": "QPS", "tp": "TPS"}
    if mode == "xp":
        xp_qps = [float(value) for value in re.findall(
            r"(?im)(?<![-\w])XP-QPS\s*(?:is|:|=)\s*([0-9]+(?:\.[0-9]+)?)",
            stdout,
        )]
        xp_tps = [float(value) for value in re.findall(
            r"(?im)(?<![-\w])XP-TPS\s*(?:is|:|=)\s*([0-9]+(?:\.[0-9]+)?)",
            stdout,
        )]
        rates = [xp_qps[-1] + xp_tps[-1]] if xp_qps and xp_tps else []
    else:
        label = re.escape(labels[mode])
        # A leading token boundary prevents plain QPS/TPS from matching XP-QPS/TPS.
        rates = [float(value) for value in re.findall(
            rf"(?im)(?<![-\w]){label}\s*(?:is|:|=)\s*([0-9]+(?:\.[0-9]+)?)",
            stdout,
        )]
    p95s = [_milliseconds(value, unit) for value, unit in _P95_RE.findall(stdout)]
    p99s = [_milliseconds(value, unit) for value, unit in _P99_RE.findall(stdout)]
    if not rates and mode == "ap":
        # Native runap writes the official per-query histogram to stdout while
        # its aggregate QPS is emitted only through Log4j.  Reconstruct the
        # same Client.java definition: apTotalCount / apTotalTime.  Because
        # every configured client executes every AP query once per round, the
        # client/round factors cancel, leaving N / sum(mean response times).
        average_ms = [float(value) for value in re.findall(
            r"(?im)^AP Query\s+\d+\s*:.*?\bavg rt\s*:\s*([0-9]+(?:\.[0-9]+)?)",
            stdout,
        )]
        if average_ms and sum(average_ms) > 0:
            rates = [len(average_ms) * 1000.0 / sum(average_ms)]
    if not rates or not p95s or not p99s:
        raise ValueError(f"HyBench {mode} output lacks its summary rate or percentiles")
    return WorkloadMetrics(rates[-1], max(p95s), max(p99s))


def generate_hybench_properties(
    endpoint: MySQLEndpoint, output_dir: Path | str, *, duration_s: int = 60,
    clients: int = 4, extra: Mapping[str, str | int | float] | None = None,
) -> Path:
    if duration_s <= 0 or clients <= 0:
        raise ValueError("duration and clients must be positive")
    jdbc_url = (
        f"jdbc:mysql://{endpoint.host}:{endpoint.port}/{endpoint.database}"
        "?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=UTC"
    )
    run_minutes = max(1, math.ceil(duration_s / 60.0))
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
    values.update(extra or {})
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
    dry_run: bool = True, timeout_s: float = 900, runner: Runner = subprocess.run,
) -> dict[str, Any]:
    mode = mode.lower()
    modes = {"ap": "runap", "tp": "runtp", "xp": "runxp"}
    if mode not in modes:
        raise ValueError("HyBench mode must be ap, tp or xp")
    output = _output_dir(output_dir)
    home = Path(hybench_home).resolve()
    script = home / "hybench"
    props = generate_hybench_properties(endpoint, output, duration_s=duration_s,
                                         clients=clients, extra=extra_properties)
    statement_file = home / "conf" / "stmt_mysql.toml"
    argv = ["bash", str(script), "-t", modes[mode], "-c", str(props), "-f", str(statement_file)]
    env = dict(os.environ)
    env["HYBENCH_CONFIG"] = str(props)
    env["HYBENCH_OUTPUT_DIR"] = str(output)
    plan = {"adapter": "hybench", "mode": mode, "dry_run": dry_run, "argv": argv,
            "cwd": str(home), "properties": str(props), "output_dir": str(output)}
    _write(output / "command.json", json.dumps(plan, indent=2) + "\n")
    if dry_run:
        return plan
    if not script.is_file():
        raise FileNotFoundError(script)
    completed = _run(argv, cwd=home, env=env, timeout_s=timeout_s, runner=runner)
    _write(output / "stdout.log", completed.stdout or "")
    _write(output / "stderr.log", completed.stderr or "")
    if completed.returncode:
        raise RuntimeError(f"HyBench exited {completed.returncode}; see {output}")
    result = plan | {"dry_run": False, "metrics": parse_hybench_mode_metrics(completed.stdout, mode).as_dict()}
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
        queries.append((path, _validate_job_sql(sql, path)))
    if not queries:
        raise ValueError("JOB manifest contains no queries")
    base = [str(mysql_binary), "--batch", "--raw", "--skip-column-names", "-h", endpoint.host,
            "-P", str(endpoint.port), "-u", endpoint.user, endpoint.database]
    plan = {"adapter": "job", "dry_run": dry_run, "manifest": str(manifest_path),
            "output_dir": str(output), "concurrency": concurrency, "repetitions": repetitions,
            "argv_prefix": base, "query_count": len(queries)}
    _write(output / "command.json", json.dumps(plan, indent=2) + "\n")
    if dry_run:
        return plan
    env = dict(os.environ)
    if endpoint.password:
        env["MYSQL_PWD"] = endpoint.password

    def invoke(sql: str) -> tuple[float, subprocess.CompletedProcess[str]]:
        started = time.monotonic()
        completed = runner(base, input=sql, cwd=str(output), env=env, text=True,
                           capture_output=True, timeout=timeout_s, check=False)
        return time.monotonic() - started, completed

    started = time.monotonic()
    latencies: list[float] = []
    tasks = [sql for _ in range(repetitions) for _, sql in queries]
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(invoke, sql) for sql in tasks]
        for future in as_completed(futures):
            elapsed, completed = future.result()
            latencies.append(elapsed * 1000.0)
            if completed.returncode:
                errors.append((completed.stderr or "mysql failed").strip())
    wall = time.monotonic() - started
    if errors:
        _write(output / "stderr.log", "\n".join(errors) + "\n")
        raise RuntimeError(f"{len(errors)} JOB queries failed; see {output}")
    explains: list[str] = []
    for _, sql in queries:
        _, completed = invoke("EXPLAIN FORMAT=JSON " + sql)
        if completed.returncode:
            raise RuntimeError("JOB EXPLAIN FORMAT=JSON failed")
        explains.append((completed.stdout or "").strip())
    metrics = WorkloadMetrics(len(tasks) / wall, _percentile(latencies, .95),
                              _percentile(latencies, .99), tuple(explains))
    result = plan | {"dry_run": False, "metrics": metrics.as_dict(), "wall_time_s": wall}
    _write(output / "adapter_result.json", json.dumps(result, indent=2) + "\n")
    return result
