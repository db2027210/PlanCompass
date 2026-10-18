"""Module."""


import time
import threading
from typing import Callable, Dict, List, Tuple, Optional, Any
import urllib.parse
import urllib.request
import urllib.error
import json
import subprocess
import re
import os
import shlex
import signal
import math

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    psutil = None


def _terminate_process_group(proc: subprocess.Popen, *, timeout_s: float = 10.0) -> None:
    """Terminate a subprocess tree started with start_new_session=True."""
    # The session leader can exit while descendants in its process group remain
    # alive.  ``proc.poll()`` therefore is not sufficient proof that the whole
    # measured workload has stopped.
    process_group_id = proc.pid

    def group_exists() -> bool:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except Exception:
        if proc.poll() is None:
            proc.terminate()

    deadline = time.monotonic() + max(0.0, timeout_s)
    while group_exists() and time.monotonic() < deadline:
        if proc.poll() is None:
            try:
                proc.wait(timeout=min(0.1, max(0.01, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
        else:
            time.sleep(0.05)

    if group_exists():
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:
            if proc.poll() is None:
                proc.kill()
    if proc.poll() is None:
        proc.wait()


def _is_hybench_sigterm_with_samples(
    *,
    returncode: int,
    workload_cmd: str,
    workload_output_lines: List[str],
    stable_sample_counts: Dict[str, int],
) -> bool:
    """Treat HyBench timer SIGTERM as completion only when metrics were sampled."""
    if "hybench" not in (workload_cmd or "").lower():
        return False
    if not workload_output_lines:
        return False
    if min(stable_sample_counts.values() or [0]) < 5:
        return False
    if returncode in (-signal.SIGTERM, 128 + signal.SIGTERM):
        return True
    # HyBench can finish all declared progress windows and then return 2 when
    # one long AP statement reaches statement_timeout during executor drain.
    # The fixed-horizon measurement is still complete and contains hundreds
    # of stable samples.  Accept only explicit N/N completion evidence; an
    # early benchmark failure remains an error.
    completion_index: Optional[int] = None
    first_error_index: Optional[int] = None
    serious_error_patterns = (
        "exception",
        "caused by:",
        "error:",
        "no space left on device",
    )
    for index, line in enumerate(workload_output_lines):
        normalized = line.lower()
        if first_error_index is None and any(
            pattern in normalized for pattern in serious_error_patterns
        ):
            first_error_index = index
        match = re.search(r"Current\s+(\d+)\s*/\s*(\d+)\s+time", line)
        if match and int(match.group(1)) == int(match.group(2)):
            completion_index = index
    if completion_index is None:
        return False
    # A statement_timeout/error emitted while the executor is draining after
    # N/N is the tolerated HyBench tail case.  Any serious error observed
    # before full progress means the measured workload itself was incomplete.
    return first_error_index is None or completion_index < first_error_index


def monitor_system_resources_during(
    workload_func: Callable[[], None],
    interval_s: float = 1.0,
    warmup_s: float = 0.0
) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]]]:
    """monitor system resources during."""


    if psutil is None:
        raise RuntimeError("psutil  requirements.txt  psutil")

    cpu_count = psutil.cpu_count(logical=True) or 1

    def to_gb(bytes_value: float) -> float:
        return bytes_value / (1024 ** 3)

    def to_mb(bytes_value: float) -> float:
        return bytes_value / (1024 ** 2)


    psutil.cpu_percent(interval=None)
    if warmup_s > 0:
        time.sleep(warmup_s)

    samples_cpu_cores: List[float] = []
    samples_mem_gb: List[float] = []
    samples_io_read_mb_s: List[float] = []
    samples_io_write_mb_s: List[float] = []

    io_prev = psutil.disk_io_counters()
    t_prev = time.perf_counter()

    def run_workload():
        workload_func()

    worker = threading.Thread(target=run_workload)
    worker.start()

    while worker.is_alive():
        time.sleep(interval_s)
        t_now = time.perf_counter()
        dt = max(1e-6, t_now - t_prev)

        cpu_percent = psutil.cpu_percent(interval=None)
        mem_used = psutil.virtual_memory().used
        io_now = psutil.disk_io_counters()

        cpu_cores = cpu_percent / 100.0 * cpu_count
        samples_cpu_cores.append(cpu_cores)
        samples_mem_gb.append(to_gb(mem_used))

        read_mb_s = to_mb(io_now.read_bytes - io_prev.read_bytes) / dt
        write_mb_s = to_mb(io_now.write_bytes - io_prev.write_bytes) / dt
        samples_io_read_mb_s.append(read_mb_s)
        samples_io_write_mb_s.append(write_mb_s)

        io_prev = io_now
        t_prev = t_now

    worker.join()

    def stats(values: List[float]) -> Dict[str, float]:
        if not values:
            return {'min': 0.0, 'max': 0.0, 'avg': 0.0}
        return {
            'min': float(min(values)),
            'max': float(max(values)),
            'avg': float(sum(values) / len(values))
        }

    cpu_stats = stats(samples_cpu_cores)
    mem_stats = stats(samples_mem_gb)
    io_read_stats = stats(samples_io_read_mb_s)
    io_write_stats = stats(samples_io_write_mb_s)

    resource_avg = {
        'cpu': cpu_stats['avg'],
        'memory': mem_stats['avg'],
        'io': io_read_stats['avg'] + io_write_stats['avg']
    }

    detailed_stats = {
        'cpu_cores': cpu_stats,
        'memory_gb': mem_stats,
        'io_read_mb_s': io_read_stats,
        'io_write_mb_s': io_write_stats
    }

    return resource_avg, detailed_stats


def parse_hybench_benchmark_output(workload_output_lines: List[str]) -> Dict[str, Any]:
    """Parse HyBench output plus the generic benchmark JSON marker."""
    benchmark_results: Dict[str, Optional[str]] = {}
    marker_benchmark: Dict[str, Any] = {}
    marker_latency: Dict[str, Any] = {}
    marker_formal_resource: Dict[str, Any] = {}
    marker_measurement_protocol: Dict[str, Any] = {}
    latency_points: Dict[str, Dict[str, Dict[str, float]]] = {
        "tp": {},
        "xp_iq": {},
        "xp_at": {},
    }
    job_latencies_ms: List[float] = []

    latency_line_re = re.compile(
        r"^(?P<label>.+?)\s*:\s*max rt\s*:\s*(?P<max>[0-9.]+)\s*\|\s*"
        r"min rt\s*:\s*(?P<min>[0-9.]+)\s*\|\s*avg rt\s*:\s*"
        r"(?P<avg>[0-9.]+)(?P<rest>.*)$"
    )
    latency_percentile_re = re.compile(r"(90|95|99)% rt\s*:\s*([0-9.]+)")
    job_latency_re = re.compile(
        r"\bJOB\s+round=\d+\s+query=\d+/\d+\s+file=\S+\s+"
        r"elapsed_ms=(?P<elapsed>[0-9.]+)\s+returncode=(?P<returncode>-?\d+)\b"
    )
    metric_patterns = [
        ("xp_qps", re.compile(r"\bXP-QPS\s*(?::|is)\s*(?P<value>[0-9.]+|Infinity|NaN)\b")),
        ("xp_tps", re.compile(r"\bXP-TPS\s*(?::|is)\s*(?P<value>[0-9.]+|Infinity|NaN)\b")),
        ("ap_qps", re.compile(r"(?<!XP-)\bQPS\s*(?::|is)\s*(?P<value>[0-9.]+|Infinity|NaN)\b")),
        ("tp_tps", re.compile(r"(?<!XP-)\bTPS\s*(?::|is)\s*(?P<value>[0-9.]+|Infinity|NaN)\b")),
        ("freshness_ms", re.compile(r"\bFreshness\(ms\)\s*(?::|is)\s*(?P<value>[0-9.]+|Infinity|NaN)\b")),
        ("htap_score", re.compile(r"\bGeometric Mean\s*(?::|is)\s*(?P<value>[0-9.]+|Infinity|NaN)\b")),
    ]

    def _classify_latency_point(label: str) -> Optional[Tuple[str, str]]:
        lowered = label.lower()
        tp_match = re.search(r"\btp transaction\s+([^:|]+?)\s*$", lowered)
        if tp_match:
            return "tp", tp_match.group(1).strip()
        query_match = re.search(r"\b(?:interative\s+|interactive\s+)?query\s+(\d+)\s*$", lowered)
        if query_match:
            # Fixed-horizon VM runs may end while one AP query is still active.
            # Their timestamped periodic lines contain the authoritative latest
            # cumulative percentiles even though HyBench emits no final summary.
            return "xp_iq", query_match.group(1)
        at_match = re.search(r"\banalytical transaction\s+([^:|]+?)\s*$", lowered)
        if at_match:
            return "xp_at", at_match.group(1).strip()
        return None

    for line in workload_output_lines:
        marker_prefix = "RESOURCE_ADAPTIVE_BENCHMARK_JSON="
        marker_index = line.find(marker_prefix)
        if marker_index >= 0:
            try:
                marker = json.loads(line[marker_index + len(marker_prefix):].strip())
            except (json.JSONDecodeError, TypeError):
                marker = {}
            if isinstance(marker, dict):
                if isinstance(marker.get("benchmark"), dict):
                    marker_benchmark.update(marker["benchmark"])
                if isinstance(marker.get("benchmark_latency"), dict):
                    marker_latency.update(marker["benchmark_latency"])
                if isinstance(marker.get("formal_pg_resource"), dict):
                    marker_formal_resource.update(marker["formal_pg_resource"])
                if isinstance(marker.get("candidate_measurement_protocol"), dict):
                    marker_measurement_protocol.update(marker["candidate_measurement_protocol"])
        latency_match = latency_line_re.match(line.strip())
        if latency_match:
            latency_point = _classify_latency_point(latency_match.group("label").strip())
            if latency_point:
                latency_group, point_key = latency_point
                point_values: Dict[str, float] = {}
                for percentile_name, value in latency_percentile_re.findall(line):
                    try:
                        latency_value = float(value)
                    except ValueError:
                        continue
                    point_values[percentile_name] = latency_value
                if point_values:
                    latency_points[latency_group][point_key] = point_values

        job_match = job_latency_re.search(line)
        if job_match and int(job_match.group("returncode")) == 0:
            try:
                job_latencies_ms.append(float(job_match.group("elapsed")))
            except ValueError:
                pass

        for metric_key, metric_re in metric_patterns:
            metric_match = metric_re.search(line)
            if metric_match:
                benchmark_results[metric_key] = metric_match.group("value").strip()
                break

    benchmark_latency: Dict[str, Optional[float]] = {}
    all_points = {
        f"{group_name}:{point_key}": point
        for group_name, points in latency_points.items()
        for point_key, point in points.items()
    }
    grouped_points = {**latency_points, "all": all_points}
    for group_name, points in grouped_points.items():
        for percentile_name in ("90", "95", "99"):
            values = [point[percentile_name] for point in points.values() if percentile_name in point]
            benchmark_latency[f"{group_name}_p{percentile_name}_avg"] = (
                sum(values) / len(values) if values else None
            )
            benchmark_latency[f"{group_name}_p{percentile_name}_max"] = max(values) if values else None

    # Fixed-horizon JOB replays deliberately terminate the client after a
    # common observation window.  They therefore cannot emit the end-of-run
    # JSON marker, but every completed query is already printed with its real
    # elapsed time.  Use those completed-query observations for the same
    # all-workload latency fields consumed by the double-loop audit/exporter.
    # A clean end-of-run marker, when present, still overrides these values
    # below.
    if job_latencies_ms:
        ordered_job = sorted(job_latencies_ms)

        def job_percentile(fraction: float) -> float:
            position = fraction * (len(ordered_job) - 1)
            lower = int(math.floor(position))
            upper = int(math.ceil(position))
            if lower == upper:
                return ordered_job[lower]
            weight = position - lower
            return ordered_job[lower] * (1.0 - weight) + ordered_job[upper] * weight

        benchmark_results["query_count"] = len(job_latencies_ms)
        for percentile_name, fraction in (("90", 0.90), ("95", 0.95), ("99", 0.99)):
            value = job_percentile(fraction)
            benchmark_latency[f"all_p{percentile_name}_avg"] = value
            benchmark_latency[f"all_p{percentile_name}_max"] = value

    for percentile_name in ("90", "95", "99"):
        overall_value = benchmark_latency.get(f"all_p{percentile_name}_avg")
        if overall_value is not None:
            benchmark_results[f"latency_p{percentile_name}"] = str(overall_value)

    benchmark_results.update(marker_benchmark)
    benchmark_latency.update(marker_latency)
    for percentile_name in ("90", "95", "99"):
        marker_value = benchmark_latency.get(f"all_p{percentile_name}_avg")
        if marker_value is not None:
            benchmark_results[f"latency_p{percentile_name}"] = str(marker_value)

    return {
        "benchmark": benchmark_results,
        "benchmark_latency": benchmark_latency,
        "formal_pg_resource": marker_formal_resource,
        "candidate_measurement_protocol": marker_measurement_protocol,
        "workload_output": "\n".join(workload_output_lines[-30:]),
    }


def _has_stable_structured_measurement(payload: Dict[str, Any]) -> bool:
    protocol = payload.get("candidate_measurement_protocol") or {}
    aggregate = (payload.get("formal_pg_resource") or {}).get("aggregate") or {}
    if protocol.get("stable") is not True:
        return False
    for field in ("cpu_utilization", "memory_utilization", "io_utilization"):
        try:
            value = float(aggregate[field])
        except (KeyError, TypeError, ValueError):
            return False
        if not math.isfinite(value) or value < 0.0:
            return False
    return True


def monitored_resource_estimation(
    workload_func: Callable[[], None],
    interval_s: float = 1.0,
    warmup_s: float = 0.0
) -> Callable[[object, Dict[str, float]], Dict[str, float]]:
    """monitored resource estimation."""


    def estimator(sql_set: object, params: Dict[str, float]) -> Dict[str, float]:
        _ = sql_set
        _ = params
        resource_avg, _ = monitor_system_resources_during(
            workload_func=workload_func,
            interval_s=interval_s,
            warmup_s=warmup_s
        )
        return resource_avg

    return estimator


def _prom_query_range(
    prom_url: str,
    promql: str,
    start_ts: float,
    end_ts: float,
    step: str = "5s"
) -> List[float]:
    params = {
        "query": promql,
        "start": f"{start_ts:.3f}",
        "end": f"{end_ts:.3f}",
        "step": step
    }
    url = f"{prom_url}/api/v1/query_range?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Prometheus HTTPError {exc.code} for query_range. "
            f"url={url} body={body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Prometheus URLError for query_range. url={url} error={exc}. "
            "Check Prometheus is running and prom_url is correct."
        ) from exc
    if payload.get("status") != "success":
        return []
    result = payload.get("data", {}).get("result", [])
    if not result:
        return []
    return [float(v[1]) for v in result[0].get("values", [])]


def check_prometheus_available(
    prom_url: str,
    timeout: float = 2.0
) -> Tuple[bool, str]:
    """
    Best-effort Prometheus availability check.
    Returns (ok, error_message).
    """
    ready_url = f"{prom_url}/-/ready"
    try:
        with urllib.request.urlopen(ready_url, timeout=timeout) as resp:
            if resp.getcode() == 200:
                return True, ""
    except urllib.error.HTTPError as exc:
        # Fallback for older versions without /-/ready.
        if exc.code != 404:
            return False, f"{exc.code} {exc.reason}"
    except urllib.error.URLError as exc:
        return False, str(exc)

    info_url = f"{prom_url}/api/v1/status/runtimeinfo"
    try:
        with urllib.request.urlopen(info_url, timeout=timeout) as resp:
            if resp.getcode() == 200:
                return True, ""
    except urllib.error.URLError as exc:
        return False, str(exc)
    except urllib.error.HTTPError as exc:
        return False, f"{exc.code} {exc.reason}"

    return False, "unexpected response"


def _stats(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"min": 0.0, "max": 0.0, "avg": 0.0, "p99": 0.0}
    sorted_vals = sorted(values)
    p99_idx = min(int(len(sorted_vals) * 0.99), len(sorted_vals) - 1)
    return {
        "min": float(min(values)),
        "max": float(max(values)),
        "avg": float(sum(values) / len(values)),
        "p99": float(sorted_vals[p99_idx]),
    }


def monitor_resources_from_prometheus(
    prom_url: str,
    start_ts: float,
    end_ts: float,
    *,
    step: str = "5s",
    cpu_promql: Optional[str] = None,
    mem_promql: Optional[str] = None,
    io_read_promql: Optional[str] = None,
    io_write_promql: Optional[str] = None,
    qps_promql: Optional[str] = None,
    concurrency_promql: Optional[str] = None,
    query_count_promql: Optional[str] = None,
    query_time_promql: Optional[str] = None
) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]]]:
    """monitor resources from prometheus."""


    cpu_vals = _prom_query_range(prom_url, cpu_promql, start_ts, end_ts, step) if cpu_promql else []
    mem_vals = _prom_query_range(prom_url, mem_promql, start_ts, end_ts, step) if mem_promql else []
    io_r_vals = _prom_query_range(prom_url, io_read_promql, start_ts, end_ts, step) if io_read_promql else []
    io_w_vals = _prom_query_range(prom_url, io_write_promql, start_ts, end_ts, step) if io_write_promql else []
    qps_vals = _prom_query_range(prom_url, qps_promql, start_ts, end_ts, step) if qps_promql else []
    concurrency_vals = _prom_query_range(prom_url, concurrency_promql, start_ts, end_ts, step) if concurrency_promql else []
    query_count_vals = _prom_query_range(prom_url, query_count_promql, start_ts, end_ts, step) if query_count_promql else []
    query_time_vals = _prom_query_range(prom_url, query_time_promql, start_ts, end_ts, step) if query_time_promql else []

    cpu_stats = _stats(cpu_vals)
    mem_stats = _stats(mem_vals)
    io_read_stats = _stats(io_r_vals)
    io_write_stats = _stats(io_w_vals)
    qps_stats = _stats(qps_vals) if qps_promql else None
    concurrency_stats = _stats(concurrency_vals) if concurrency_promql else None
    query_count_stats = _stats(query_count_vals) if query_count_promql else None
    query_time_stats = _stats(query_time_vals) if query_time_promql else None

    resource_avg = {
        "cpu": cpu_stats["avg"],
        "memory": mem_stats["avg"],
        "io": io_read_stats["avg"] + io_write_stats["avg"],
    }

    detailed_stats = {
        "cpu": cpu_stats,
        "memory": mem_stats,
        "io_read": io_read_stats,
        "io_write": io_write_stats,
        "qps": qps_stats,
        "concurrency": concurrency_stats,
        "query_count": query_count_stats,
        "query_time": query_time_stats
    }

    return resource_avg, detailed_stats


def monitored_resource_estimation_prometheus(
    prom_url: str,
    mem_promql: str,
    *,
    cpu_promql: Optional[str] = None,
    io_read_promql: Optional[str] = None,
    io_write_promql: Optional[str] = None,
    qps_promql: Optional[str] = None,
    concurrency_promql: Optional[str] = None,
    query_count_promql: Optional[str] = None,
    query_time_promql: Optional[str] = None,
    step: str = "5s"
) -> Callable[[object, Dict[str, float]], Dict[str, float]]:
    """monitored resource estimation prometheus."""


    def estimator(sql_set: object, params: Dict[str, float]) -> Dict[str, float]:
        _ = sql_set
        _ = params
        end_ts = time.time()
        start_ts = end_ts - 20.0
        resource_avg, _ = monitor_resources_from_prometheus(
            prom_url=prom_url,
            start_ts=start_ts,
            end_ts=end_ts,
            step=step,
            cpu_promql=cpu_promql,
            mem_promql=mem_promql,
            io_read_promql=io_read_promql,
            io_write_promql=io_write_promql,
            qps_promql=qps_promql,
            concurrency_promql=concurrency_promql,
            query_count_promql=query_count_promql,
            query_time_promql=query_time_promql
        )
        return resource_avg

    return estimator


def monitored_resource_estimation_prometheus_workload(
    workload_cmd: str,
    prom_url: str,
    mem_promql: str,
    *,
    cpu_promql: Optional[str] = None,
    io_read_promql: Optional[str] = None,
    io_write_promql: Optional[str] = None,
    qps_promql: Optional[str] = None,
    concurrency_promql: Optional[str] = None,
    query_count_promql: Optional[str] = None,
    query_time_promql: Optional[str] = None,
    step: str = "5s"
) -> Callable[[object, Dict[str, float]], Dict[str, float]]:
    """monitored resource estimation prometheus workload."""


    def estimator(sql_set: object, params: Dict[str, float]) -> Dict[str, float]:
        _ = sql_set
        _ = params
        start_ts = time.time()
        subprocess.run(workload_cmd, shell=True, check=True)
        end_ts = time.time()
        resource_avg, _ = monitor_resources_from_prometheus(
            prom_url=prom_url,
            start_ts=start_ts,
            end_ts=end_ts,
            step=step,
            cpu_promql=cpu_promql,
            mem_promql=mem_promql,
            io_read_promql=io_read_promql,
            io_write_promql=io_write_promql,
            qps_promql=qps_promql,
            concurrency_promql=concurrency_promql,
            query_count_promql=query_count_promql,
            query_time_promql=query_time_promql
        )
        return resource_avg

    return estimator


def _parse_human_bytes(value: str) -> float:
    s = (value or "").strip().upper()
    if not s:
        return 0.0
    if s == "0B":
        return 0.0
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*([KMGTP]?I?B)$", s)
    if not m:
        return 0.0
    num = float(m.group(1))
    unit = m.group(2)
    factors = {
        "B": 1.0,
        "KB": 1000.0,
        "MB": 1000.0 ** 2,
        "GB": 1000.0 ** 3,
        "TB": 1000.0 ** 4,
        "KIB": 1024.0,
        "MIB": 1024.0 ** 2,
        "GIB": 1024.0 ** 3,
        "TIB": 1024.0 ** 4,
    }
    return num * factors.get(unit, 1.0)


def _find_first_existing(base_dirs: List[str], filenames: List[str]) -> Optional[str]:
    for base in base_dirs:
        for filename in filenames:
            candidate = os.path.join(base, filename)
            try:
                with open(candidate) as f:
                    f.read(1)
                return candidate
            except OSError:
                continue
    return None


def _find_cgroup_paths(container_name: str) -> Dict[str, str]:
    """Locate cgroup v1/v2 file paths for a Docker container."""
    cmd = ["docker", "inspect", "--format", "{{.Id}}", container_name]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"docker inspect failed for {container_name}")
    cid = proc.stdout.strip()

    v2_bases = [
        f"/sys/fs/cgroup/system.slice/docker-{cid}.scope",
        f"/sys/fs/cgroup/docker/{cid}",
        f"/sys/fs/cgroup/docker-limited/{cid}",
        f"/sys/fs/cgroup/{cid}",
    ]
    v2_cpu = _find_first_existing(v2_bases, ["cpu.stat"])
    v2_mem = _find_first_existing(v2_bases, ["memory.current"])
    if v2_cpu and v2_mem:
        return {
            "version": "v2",
            "cpu": v2_cpu,
            "mem": v2_mem,
            "mem_dir": os.path.dirname(v2_mem),
            "io": _find_first_existing(v2_bases, ["io.stat"]),
        }

    v1_cpu_bases = [
        f"/sys/fs/cgroup/cpu,cpuacct/docker-limited/{cid}",
        f"/sys/fs/cgroup/cpu,cpuacct/docker/{cid}",
        f"/sys/fs/cgroup/cpuacct/docker/{cid}",
    ]
    v1_mem_bases = [
        f"/sys/fs/cgroup/memory/docker-limited/{cid}",
        f"/sys/fs/cgroup/memory/docker/{cid}",
    ]
    v1_blkio_bases = [
        f"/sys/fs/cgroup/blkio/docker-limited/{cid}",
        f"/sys/fs/cgroup/blkio/docker/{cid}",
        f"/sys/fs/cgroup/blkio/{cid}",
    ]

    cpu_path = _find_first_existing(v1_cpu_bases, ["cpuacct.usage"])
    mem_path = _find_first_existing(v1_mem_bases, ["memory.usage_in_bytes"])
    io_bytes_path = _find_first_existing(
        v1_blkio_bases,
        [
            "blkio.throttle.io_service_bytes",
            "blkio.throttle.io_service_bytes_recursive",
            "blkio.io_service_bytes",
            "blkio.io_service_bytes_recursive",
        ],
    )
    io_ios_path = _find_first_existing(
        v1_blkio_bases,
        [
            "blkio.throttle.io_serviced",
            "blkio.throttle.io_serviced_recursive",
            "blkio.io_serviced",
            "blkio.io_serviced_recursive",
        ],
    )

    if not cpu_path or not mem_path:
        raise RuntimeError(
            f"cgroup paths not found for container={container_name} (cid={cid}). "
            f"cpu={cpu_path}, mem={mem_path}"
        )
    return {
        "version": "v1",
        "cpu": cpu_path,
        "mem": mem_path,
        "mem_dir": os.path.dirname(mem_path),
        "io_bytes": io_bytes_path or "",
        "io_ios": io_ios_path or "",
    }


def _read_cgroup_memory_stat_value(cgroup_paths: Dict[str, str], keys: Tuple[str, ...]) -> float:
    mem_dir = cgroup_paths.get("mem_dir")
    if not mem_dir:
        return 0.0
    stat_path = os.path.join(mem_dir, "memory.stat")
    values = set(keys)
    try:
        with open(stat_path) as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[0] in values:
                    return float(parts[1])
    except OSError:
        return 0.0
    return 0.0


def _read_cgroup_pss_bytes(cgroup_paths: Dict[str, str]) -> Optional[Dict[str, float]]:
    mem_dir = cgroup_paths.get("mem_dir")
    if not mem_dir:
        return None

    procs_file = None
    for candidate in (
        os.path.join(mem_dir, "cgroup.procs"),
        os.path.join(mem_dir, "tasks"),
    ):
        if os.path.exists(candidate):
            procs_file = candidate
            break
    if not procs_file:
        return None

    total_pss_bytes = 0.0
    matched_pid_count = 0
    found_any = False
    try:
        with open(procs_file) as f:
            pids = [line.strip() for line in f if line.strip()]
    except OSError:
        return None

    for pid in pids:
        comm = ""
        cmdline = ""
        try:
            with open(f"/proc/{pid}/comm") as f:
                comm = f.read().strip().lower()
        except OSError:
            pass
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().decode("utf-8", errors="replace").replace("\x00", " ").strip().lower()
        except OSError:
            pass

        # Restrict PSS aggregation to PostgreSQL-related processes inside the container.
        # PostgreSQL uses a multi-process architecture, so we sum PSS over all postgres backends,
        # postmaster, background workers, etc., but skip unrelated helper processes.
        if "postgres" not in comm and "postgres" not in cmdline:
            continue
        matched_pid_count += 1

        smaps_rollup = f"/proc/{pid}/smaps_rollup"
        try:
            with open(smaps_rollup) as f:
                for line in f:
                    if line.startswith("Pss:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            total_pss_bytes += float(parts[1]) * 1024.0
                            found_any = True
                        break
        except OSError:
            continue

    if not found_any:
        return None
    return {
        "memory_bytes": total_pss_bytes,
        "pid_count": float(matched_pid_count),
    }


def _read_cgroup_v2_cpu_ns(cpu_stat_path: str) -> float:
    with open(cpu_stat_path) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) == 2 and parts[0] == "usage_usec":
                return float(parts[1]) * 1000.0
    return 0.0


def _read_cgroup_v2_io(io_stat_path: Optional[str]) -> Dict[str, float]:
    counters = {
        "io_read_bytes": 0.0,
        "io_write_bytes": 0.0,
    }
    if not io_stat_path:
        return counters
    try:
        with open(io_stat_path) as f:
            for line in f:
                parts = line.strip().split()
                if not parts:
                    continue
                for token in parts[1:]:
                    if "=" not in token:
                        continue
                    key, value = token.split("=", 1)
                    try:
                        numeric = float(value)
                    except ValueError:
                        continue
                    if key == "rbytes":
                        counters["io_read_bytes"] += numeric
                    elif key == "wbytes":
                        counters["io_write_bytes"] += numeric
    except OSError:
        return counters
    return counters


def _read_cgroup_v1_blkio(path: Optional[str]) -> Dict[str, float]:
    counters = {
        "read": 0.0,
        "write": 0.0,
    }
    if not path:
        return counters
    try:
        with open(path) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 3:
                    continue
                op = parts[-2].lower()
                try:
                    value = float(parts[-1])
                except ValueError:
                    continue
                if op == "read":
                    counters["read"] += value
                elif op == "write":
                    counters["write"] += value
    except OSError:
        return counters
    return counters


def _read_container_postgres_proc_io(container_name: str) -> Dict[str, float]:
    script = r'''
set -e
pids="$(pgrep -x postgres || true)"
if [ -z "$pids" ]; then
  printf "read_bytes=0\nwrite_bytes=0\n"
  exit 0
fi
awk '
  $1 == "read_bytes:" {
    read_sum += $2
  }
  $1 == "write_bytes:" {
    write_sum += $2
  }
  END {
    printf("read_bytes=%.0f\nwrite_bytes=%.0f\n", read_sum, write_sum)
  }
' $(for pid in $pids; do printf "/proc/%s/io " "$pid"; done)
'''
    try:
        proc = subprocess.run(
            ["docker", "exec", container_name, "bash", "-lc", script],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return {
            "io_read_bytes": 0.0,
            "io_write_bytes": 0.0,
            "io_source": "container_procfs_pg_io_error",
        }

    io_read_bytes = 0.0
    io_write_bytes = 0.0
    for line in proc.stdout.splitlines():
        text = line.strip()
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        try:
            numeric = float(value.strip())
        except ValueError:
            continue
        if key.strip() == "read_bytes":
            io_read_bytes = numeric
        elif key.strip() == "write_bytes":
            io_write_bytes = numeric

    return {
        "io_read_bytes": io_read_bytes,
        "io_write_bytes": io_write_bytes,
        "io_source": "container_procfs_pg_io",
    }


def _build_ssh_target(
    *,
    host: str,
    ssh_target: str = "",
    ssh_user: str = "",
) -> str:
    explicit_target = str(ssh_target or "").strip()
    if explicit_target:
        return explicit_target
    host_text = str(host or "").strip()
    user_text = str(ssh_user or "").strip()
    if user_text:
        return f"{user_text}@{host_text}"
    return host_text


def _ssh_control_path(
    *,
    target: str,
    ssh_port: int,
    identity_file: str = "",
) -> str:
    """Stable ControlPath for multiplexed SSH to one VM target."""
    import hashlib
    import tempfile

    key = f"{target}|{int(ssh_port)}|{str(identity_file or '').strip()}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    base = str(
        os.environ.get(
            "VM_RUNTIME_SSH_CONTROL_DIR",
            os.path.join(tempfile.gettempdir(), "bonect_vm_runtime_ssh"),
        )
    ).strip()
    os.makedirs(base, mode=0o700, exist_ok=True)
    return os.path.join(base, f"cm_{digest}")


def _run_ssh_command(
    *,
    host: str,
    remote_cmd: str,
    ssh_target: str = "",
    ssh_user: str = "",
    ssh_port: int = 22,
    identity_file: str = "",
    connect_timeout_s: float = 5.0,
    retries: int = 3,
    retry_sleep_s: float = 1.0,
    ssh_control_master: bool = False,
) -> str:
    target = _build_ssh_target(host=host, ssh_target=ssh_target, ssh_user=ssh_user)
    identity_text = str(identity_file or "").strip()
    # ControlMaster is opt-in only (0949): default keeps legacy one-shot SSH so
    # in-flight CPU waves on other VMs retain the original sampling cadence.
    control_path = ""
    if bool(ssh_control_master):
        control_path = _ssh_control_path(
            target=target,
            ssh_port=ssh_port,
            identity_file=identity_text,
        )
    persist_s = max(
        30,
        int(float(os.environ.get("VM_RUNTIME_SSH_CONTROL_PERSIST_SEC", "300"))),
    )

    def _build_cmd() -> List[str]:
        built = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={max(1, int(connect_timeout_s))}",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3",
            "-p", str(int(ssh_port)),
        ]
        known_hosts_file = str(
            os.environ.get("VM_RUNTIME_SSH_USER_KNOWN_HOSTS_FILE", "")
        ).strip()
        if known_hosts_file:
            # Disposable replay VMs can legitimately reuse a DHCP address with
            # a different host key.  This remains opt-in so unrelated SSH
            # monitoring keeps the user's normal host-key policy.
            built.extend(
                [
                    "-o", "StrictHostKeyChecking=no",
                    "-o", f"UserKnownHostsFile={known_hosts_file}",
                ]
            )
        if control_path:
            built.extend(
                [
                    "-o", f"ControlPath={control_path}",
                    "-o", "ControlMaster=auto",
                    "-o", f"ControlPersist={persist_s}",
                ]
            )
        if identity_text:
            built.extend(["-i", identity_text])
        built.extend([target, remote_cmd])
        return built

    cmd = _build_cmd()
    attempts = max(1, int(retries))
    last_detail = ""
    command_timeout_s = float(
        os.environ.get(
            "VM_RUNTIME_SSH_COMMAND_TIMEOUT_SEC",
            max(10.0, float(connect_timeout_s) * 4.0),
        )
    )
    for attempt in range(1, attempts + 1):
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=command_timeout_s if command_timeout_s > 0 else None,
            )
        except subprocess.TimeoutExpired as exc:
            last_detail = f"ssh command timed out after {command_timeout_s:.1f}s"
            if exc.stderr:
                last_detail = f"{last_detail}; stderr={exc.stderr!r}"
            if attempt < attempts:
                time.sleep(max(0.0, float(retry_sleep_s)))
                continue
            raise RuntimeError(
                f"ssh command failed for target={target}. cmd={remote_cmd!r}. detail={last_detail}"
            ) from exc
        except KeyboardInterrupt as exc:
            last_detail = "ssh command interrupted while collecting VM runtime snapshot"
            if attempt < attempts:
                time.sleep(max(0.0, float(retry_sleep_s)))
                continue
            raise RuntimeError(
                f"ssh command failed for target={target}. cmd={remote_cmd!r}. detail={last_detail}"
            ) from exc
        if proc.returncode == 0:
            return proc.stdout
        last_detail = (proc.stderr or proc.stdout or "").strip()
        # Drop a dead multiplex socket once, then rebuild cmd for a clean master.
        if control_path and attempt < attempts:
            try:
                subprocess.run(
                    [
                        "ssh",
                        "-o", f"ControlPath={control_path}",
                        "-O", "exit",
                        target,
                    ],
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5.0,
                )
            except Exception:
                pass
            try:
                if os.path.exists(control_path):
                    os.unlink(control_path)
            except OSError:
                pass
            cmd = _build_cmd()
        if attempt < attempts:
            time.sleep(max(0.0, float(retry_sleep_s)))
    raise RuntimeError(
        f"ssh command failed for target={target}. cmd={remote_cmd!r}. detail={last_detail}"
    )


def _read_vm_runtime_snapshot(
    *,
    host: str,
    ssh_target: str = "",
    ssh_user: str = "",
    ssh_port: int = 22,
    identity_file: str = "",
    connect_timeout_s: float = 5.0,
    disk_devices: Optional[List[str]] = None,
    cpu_mode: str = "pg_proc",
    io_mode: str = "pg_proc",
    memory_mode: str = "pg_proc_pss",
    ssh_control_master: bool = False,
    light_remote_script: bool = False,
) -> Dict[str, float]:
    normalized_cpu_mode = (cpu_mode or "pg_proc").strip().lower()
    normalized_io_mode = (io_mode or "pg_proc").strip().lower()
    normalized_memory_mode = (memory_mode or "pg_proc_pss").strip().lower()
    collect_proc_cpu = normalized_cpu_mode == "pg_proc"
    collect_proc_io = normalized_io_mode == "pg_proc"
    collect_proc_memory = normalized_memory_mode in ("pg_proc_pss", "pg_proc_rss")
    collect_proc_pss = normalized_memory_mode == "pg_proc_pss"
    devices = [str(item).strip() for item in (disk_devices or []) if str(item).strip()]
    # Light remote script is opt-in (0949). Legacy path always walks postgres pids
    # so CPU/IO/memory process counters stay available even when unused.
    need_proc = True
    if bool(light_remote_script):
        need_proc = bool(
            collect_proc_cpu or collect_proc_io or collect_proc_memory or collect_proc_pss
        )
    if devices:
        device_filter = " ".join(shlex.quote(item) for item in devices)
        device_loop = f"""
read_sectors=0
write_sectors=0
for dev in {device_filter}; do
  if [ ! -r "/sys/class/block/$dev/stat" ]; then
    continue
  fi
  set -- $(cat "/sys/class/block/$dev/stat")
  read_sectors=$((read_sectors + $3))
  write_sectors=$((write_sectors + $7))
done
"""
    else:
        device_loop = """
read_sectors=0
write_sectors=0
for dev in $(lsblk -dn -o NAME,TYPE | awk '$2 == "disk" {print $1}'); do
  if [ ! -r "/sys/class/block/$dev/stat" ]; then
    continue
  fi
  set -- $(cat "/sys/class/block/$dev/stat")
  read_sectors=$((read_sectors + $3))
  write_sectors=$((write_sectors + $7))
done
"""
    if need_proc:
        proc_block = f"""
collect_proc_cpu={int(collect_proc_cpu)}
collect_proc_io={int(collect_proc_io)}
collect_proc_memory={int(collect_proc_memory)}
collect_proc_pss={int(collect_proc_pss)}
pids="$(pgrep -x postgres || true)"
pg_pid_count=0
pg_rss_kb=0
pg_pss_kb=0
pg_pss_pid_count=0
pg_cpu_ticks=0
pg_io_read_bytes=0
pg_io_write_bytes=0
pg_io_pid_count=0
if [ -n "$pids" ]; then
  pg_pid_count="$(printf '%s\\n' "$pids" | wc -l | awk '{{print $1}}')"
  if [ "$collect_proc_memory" -eq 1 ]; then
    pg_rss_kb="$(ps -C postgres -o rss= | awk '{{sum+=$1}} END {{print sum+0}}')"
  fi
  for pid in $pids; do
    if [ "$collect_proc_pss" -eq 1 ] && [ -r "/proc/$pid/smaps_rollup" ]; then
      pss_val="$(awk '/^Pss:/ {{print $2}}' "/proc/$pid/smaps_rollup" 2>/dev/null | head -n 1)"
      if [ -n "$pss_val" ]; then
        pg_pss_kb=$((pg_pss_kb + pss_val))
        pg_pss_pid_count=$((pg_pss_pid_count + 1))
      fi
    fi
    if [ "$collect_proc_cpu" -eq 1 ] && [ -r "/proc/$pid/stat" ]; then
      stat_line=""
      if IFS= read -r stat_line < "/proc/$pid/stat"; then
        stat_tail="${{stat_line#*) }}"
        set -- $stat_tail
        pg_cpu_ticks=$((pg_cpu_ticks + ${{12:-0}} + ${{13:-0}}))
      fi
    fi
    if [ "$collect_proc_io" -eq 1 ] && [ -r "/proc/$pid/io" ]; then
      pg_io_pid_count=$((pg_io_pid_count + 1))
      read_val="$(awk '$1 == "read_bytes:" {{print $2}}' "/proc/$pid/io" 2>/dev/null | head -n 1)"
      write_val="$(awk '$1 == "write_bytes:" {{print $2}}' "/proc/$pid/io" 2>/dev/null | head -n 1)"
      if [ -z "$read_val" ]; then read_val=0; fi
      if [ -z "$write_val" ]; then write_val=0; fi
      case "$read_val" in *[!0-9]*) read_val=0 ;; esac
      case "$write_val" in *[!0-9]*) write_val=0 ;; esac
      pg_io_read_bytes=$((pg_io_read_bytes + read_val))
      pg_io_write_bytes=$((pg_io_write_bytes + write_val))
    fi
  done
fi
"""
    else:
        # Light path for vm_busy/vm_used/disk_device: avoid pgrep/smaps under load.
        proc_block = """
pg_pid_count=0
pg_rss_kb=0
pg_pss_kb=0
pg_pss_pid_count=0
pg_cpu_ticks=0
pg_io_read_bytes=0
pg_io_write_bytes=0
pg_io_pid_count=0
"""
    script = f"""
set -eu
cpu_line="$(grep '^cpu ' /proc/stat)"
mem_total_kb="$(awk '/^MemTotal:/ {{print $2}}' /proc/meminfo)"
mem_available_kb="$(awk '/^MemAvailable:/ {{print $2}}' /proc/meminfo)"
cpu_count="$(nproc)"
clock_ticks="$(getconf CLK_TCK 2>/dev/null || printf '100')"
{proc_block}
{device_loop}
printf 'cpu_total=%s\\n' "$(printf '%s\\n' "$cpu_line" | awk '{{sum=0; for (i=2; i<=NF; ++i) sum+=$i; print sum}}')"
printf 'cpu_idle=%s\\n' "$(printf '%s\\n' "$cpu_line" | awk '{{print $5 + $6}}')"
printf 'cpu_count=%s\\n' "$cpu_count"
printf 'clock_ticks=%s\\n' "$clock_ticks"
printf 'mem_total_kb=%s\\n' "$mem_total_kb"
printf 'mem_available_kb=%s\\n' "$mem_available_kb"
printf 'pg_pid_count=%s\\n' "$pg_pid_count"
printf 'pg_rss_kb=%s\\n' "$pg_rss_kb"
printf 'pg_pss_kb=%s\\n' "$pg_pss_kb"
printf 'pg_pss_pid_count=%s\\n' "$pg_pss_pid_count"
printf 'pg_cpu_ticks=%s\\n' "$pg_cpu_ticks"
printf 'pg_io_read_bytes=%s\\n' "$pg_io_read_bytes"
printf 'pg_io_write_bytes=%s\\n' "$pg_io_write_bytes"
printf 'pg_io_pid_count=%s\\n' "$pg_io_pid_count"
printf 'io_read_sectors=%s\\n' "$read_sectors"
printf 'io_write_sectors=%s\\n' "$write_sectors"
"""

    stdout = _run_ssh_command(
        host=host,
        ssh_target=ssh_target,
        ssh_user=ssh_user,
        ssh_port=ssh_port,
        identity_file=identity_file,
        connect_timeout_s=connect_timeout_s,
        remote_cmd="bash -lc " + shlex.quote(script),
        ssh_control_master=bool(ssh_control_master),
    )
    parsed: Dict[str, float] = {
        "cpu_total": 0.0,
        "cpu_idle": 0.0,
        "cpu_count": 1.0,
        "clock_ticks": 100.0,
        "mem_total_kb": 0.0,
        "mem_available_kb": 0.0,
        "pg_pid_count": 0.0,
        "pg_rss_kb": 0.0,
        "pg_pss_kb": 0.0,
        "pg_pss_pid_count": 0.0,
        "pg_cpu_ticks": 0.0,
        "pg_io_read_bytes": 0.0,
        "pg_io_write_bytes": 0.0,
        "pg_io_pid_count": 0.0,
        "io_read_sectors": 0.0,
        "io_write_sectors": 0.0,
    }
    for line in stdout.splitlines():
        text = line.strip()
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        if key not in parsed:
            continue
        try:
            parsed[key] = float(value.strip())
        except ValueError:
            continue
    return parsed


def _cgroup_snapshot(
    cgroup_paths: Dict[str, str],
    *,
    container_name: Optional[str] = None,
    memory_mode: str = "usage",
) -> Dict[str, float]:
    """Read CPU/memory/IO cumulative counters from cgroup files."""
    version = str(cgroup_paths.get("version") or "v1").lower()
    if version == "v2":
        cpu_ns = _read_cgroup_v2_cpu_ns(cgroup_paths["cpu"])
        with open(cgroup_paths["mem"]) as f:
            usage_bytes = float(f.read().strip())
        io_counters = _read_cgroup_v2_io(cgroup_paths.get("io"))
        io_source = "cgroup_v2_io"
    else:
        with open(cgroup_paths["cpu"]) as f:
            cpu_ns = float(f.read().strip())
        with open(cgroup_paths["mem"]) as f:
            usage_bytes = float(f.read().strip())
        io_bytes = _read_cgroup_v1_blkio(cgroup_paths.get("io_bytes"))
        io_ios = _read_cgroup_v1_blkio(cgroup_paths.get("io_ios"))
        io_counters = {
            "io_read_bytes": float(io_bytes.get("read", 0.0)),
            "io_write_bytes": float(io_bytes.get("write", 0.0)),
        }
        io_source = "cgroup_v1_blkio"

    if container_name:
        proc_io = _read_container_postgres_proc_io(container_name)
        if proc_io.get("io_source") != "container_procfs_pg_io_error":
            io_counters = {
                "io_read_bytes": float(proc_io.get("io_read_bytes", 0.0)),
                "io_write_bytes": float(proc_io.get("io_write_bytes", 0.0)),
            }
            io_source = str(proc_io.get("io_source") or io_source)

    normalized_memory_mode = (memory_mode or "usage").strip().lower()
    if normalized_memory_mode in ("docker_usage", "cgroup_usage"):
        normalized_memory_mode = "usage"
    elif normalized_memory_mode in ("docker_stats", "working-set", "workingset"):
        normalized_memory_mode = "working_set"

    mem_bytes = usage_bytes
    mem_source = "cgroup_usage"
    mem_pid_count = 0.0
    if normalized_memory_mode == "working_set":
        inactive_file = _read_cgroup_memory_stat_value(cgroup_paths, ("inactive_file", "total_inactive_file"))
        mem_bytes = max(0.0, usage_bytes - inactive_file)
        mem_source = "cgroup_working_set"
    elif normalized_memory_mode == "pss":
        pss_info = _read_cgroup_pss_bytes(cgroup_paths)
        if pss_info is not None:
            mem_bytes = float(pss_info["memory_bytes"])
            mem_pid_count = float(pss_info.get("pid_count", 0.0))
            mem_source = "pss"

    return {
        "cpu_ns": cpu_ns,
        "memory_bytes": mem_bytes,
        "memory_current_bytes": usage_bytes,
        "memory_working_set_bytes": mem_bytes,
        "memory_source": mem_source,
        "memory_pid_count": mem_pid_count,
        "io_read_bytes": float(io_counters.get("io_read_bytes", 0.0)),
        "io_write_bytes": float(io_counters.get("io_write_bytes", 0.0)),
        "io_source": io_source,
    }


def monitor_resources_from_docker_runtime_workload(
    workload_cmd: str,
    container_name: str,
    *,
    interval_s: float = 2.0,
    warmup_s: float = 600.0,
    memory_mode: str = "usage",
    io_scope: str = "postgres_process",
    max_runtime_s: float = 0.0,
) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]]]:
    """
    Run workload and collect container runtime metrics via cgroup files.
    CPU is computed from cumulative nanoseconds delta between samples.
    Memory is read from cgroup usage_in_bytes, cgroup working set, or aggregated PSS.
    Only samples collected after the warmup window are included in averages.
    Returns resource_avg/resource_stats compatible with optimizer usage.
    """
    memory_mode = (memory_mode or "usage").strip().lower()
    if memory_mode in ("docker_usage", "cgroup_usage"):
        memory_mode = "usage"
    elif memory_mode in ("docker_stats", "working-set", "workingset"):
        memory_mode = "working_set"
    if memory_mode not in ("usage", "working_set", "pss"):
        raise RuntimeError(f"unsupported docker runtime memory_mode={memory_mode!r}")
    io_scope = (io_scope or "postgres_process").strip().lower()
    if io_scope not in ("postgres_process", "cgroup"):
        raise RuntimeError(f"unsupported docker runtime io_scope={io_scope!r}")

    cgroup_paths = _find_cgroup_paths(container_name)

    samples_cpu: List[float] = []
    samples_mem: List[float] = []
    samples_io_read: List[float] = []
    samples_io_write: List[float] = []
    samples_io: List[float] = []

    proc = subprocess.Popen(
        workload_cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    workload_output_lines: List[str] = []
    monitor_start_ts = time.time()

    prev_snap = None
    prev_ts = None

    import select
    import os
    import fcntl

    # Make stdout non-blocking so we can read without stalling the monitor loop
    fd = proc.stdout.fileno()
    fl = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

    memory_source = "cgroup_usage"
    memory_pid_count = 0.0
    io_source = "unknown"
    completed_normally = False
    monitor_error: Optional[BaseException] = None
    workload_returncode: Optional[int] = None
    workload_sigterm_treated_as_success = False
    benchmark_parsed: Dict[str, Any] = {}
    stable_structured_measurement = False
    try:
        while True:
            now = time.time()

            if max_runtime_s > 0 and (now - monitor_start_ts) >= max_runtime_s:
                raise subprocess.TimeoutExpired(workload_cmd, max_runtime_s)

            # Drain available stdout lines
            try:
                while True:
                    raw = proc.stdout.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace").rstrip("\n")
                    workload_output_lines.append(line)
                    print(line, flush=True)  # echo to terminal in real time
            except (IOError, OSError):
                pass

            try:
                snap = _cgroup_snapshot(
                    cgroup_paths,
                    container_name=(container_name if io_scope == "postgres_process" else None),
                    memory_mode=memory_mode,
                )
                memory_source = str(snap.get("memory_source") or memory_source)
                memory_pid_count = float(snap.get("memory_pid_count") or memory_pid_count)
                io_source = str(snap.get("io_source") or io_source)
            except Exception:
                snap = {
                    "cpu_ns": 0.0,
                    "memory_bytes": 0.0,
                    "memory_source": memory_source,
                    "memory_pid_count": memory_pid_count,
                    "io_read_bytes": 0.0,
                    "io_write_bytes": 0.0,
                    "io_source": "unknown",
                }

            stable_phase = (now - monitor_start_ts) >= max(0.0, warmup_s)

            if stable_phase:
                samples_mem.append(snap["memory_bytes"])

            if prev_snap is not None and prev_ts is not None:
                dt = max(1e-6, now - prev_ts)
                # CPU: delta nanoseconds -> cores
                cpu_delta_ns = max(0.0, snap["cpu_ns"] - prev_snap["cpu_ns"])
                cpu_cores = cpu_delta_ns / (dt * 1e9)
                io_read_rate = max(0.0, snap["io_read_bytes"] - prev_snap.get("io_read_bytes", 0.0)) / dt
                io_write_rate = max(0.0, snap["io_write_bytes"] - prev_snap.get("io_write_bytes", 0.0)) / dt
                if stable_phase:
                    samples_cpu.append(cpu_cores)
                    samples_io_read.append(io_read_rate)
                    samples_io_write.append(io_write_rate)
                    samples_io.append(io_read_rate + io_write_rate)

            prev_snap = snap
            prev_ts = now

            if proc.poll() is not None:
                # Read remaining output after process exits
                remaining = proc.stdout.read()
                if remaining:
                    for line in remaining.decode("utf-8", errors="replace").splitlines():
                        workload_output_lines.append(line)
                        print(line, flush=True)
                completed_normally = True
                break
            time.sleep(max(0.1, interval_s))
    except BaseException as exc:
        monitor_error = exc
        raise
    finally:
        if not completed_normally and proc.poll() is None:
            _terminate_process_group(proc)
        workload_returncode = proc.wait()
        workload_sigterm_treated_as_success = _is_hybench_sigterm_with_samples(
            returncode=workload_returncode,
            workload_cmd=workload_cmd,
            workload_output_lines=workload_output_lines,
            stable_sample_counts={
                "cpu": len(samples_cpu),
                "memory": len(samples_mem),
                "io": len(samples_io),
            },
        )
        benchmark_parsed = parse_hybench_benchmark_output(workload_output_lines)
        stable_structured_measurement = _has_stable_structured_measurement(
            benchmark_parsed
        )
        if (
            monitor_error is None
            and workload_returncode != 0
            and not workload_sigterm_treated_as_success
            and not stable_structured_measurement
        ):
            raise subprocess.CalledProcessError(workload_returncode, workload_cmd)

    cpu_stats = _stats(samples_cpu)
    mem_stats = _stats(samples_mem)
    io_read_stats = _stats(samples_io_read)
    io_write_stats = _stats(samples_io_write)
    io_stats = _stats(samples_io)

    resource_avg = {
        "cpu": cpu_stats["avg"],
        "memory": mem_stats["avg"],
        "io": io_stats["avg"],
    }
    detailed_stats = {
        "cpu": cpu_stats,
        "memory": mem_stats,
        "io": io_stats,
        "io_read": io_read_stats,
        "io_write": io_write_stats,
        "warmup_s": {"value": float(max(0.0, warmup_s))},
        "cpu_source": {"value": "cgroup_cpu"},
        "memory_source": {"value": memory_source},
        "memory_pg_pid_count": {"value": float(memory_pid_count)},
        "io_source": {"value": io_source},
        "io_scope": {"value": io_scope},
        "stable_sample_count": {
            "cpu": float(len(samples_cpu)),
            "memory": float(len(samples_mem)),
            "io": float(len(samples_io)),
        },
        "workload_exit": {
            "returncode": float(workload_returncode if workload_returncode is not None else 0),
            "sigterm_treated_as_success": bool(workload_sigterm_treated_as_success),
            "stable_structured_measurement": bool(stable_structured_measurement),
            "algorithmic_gate_rejection": bool(
                stable_structured_measurement
                and workload_returncode not in (None, 0)
            ),
            "consumes_technical_failure_budget": False
            if stable_structured_measurement
            else bool(workload_returncode not in (None, 0)),
        },
        "qps": None,
        "concurrency": None,
        "query_count": None,
        "query_time": None,
    }

    if not benchmark_parsed:
        benchmark_parsed = parse_hybench_benchmark_output(workload_output_lines)
    detailed_stats["benchmark"] = benchmark_parsed.get("benchmark") or {}
    detailed_stats["benchmark_latency"] = benchmark_parsed.get("benchmark_latency") or {}
    detailed_stats["formal_pg_resource"] = benchmark_parsed.get("formal_pg_resource") or {}
    detailed_stats["candidate_measurement_protocol"] = (
        benchmark_parsed.get("candidate_measurement_protocol") or {}
    )
    detailed_stats["workload_output"] = str(benchmark_parsed.get("workload_output") or "")

    return resource_avg, detailed_stats


def monitor_resources_from_vm_runtime_workload(
    workload_cmd: str,
    vm_host: str,
    *,
    ssh_target: str = "",
    ssh_user: str = "",
    ssh_port: int = 22,
    ssh_identity_file: str = "",
    interval_s: float = 2.0,
    warmup_s: float = 0.0,
    connect_timeout_s: float = 5.0,
    disk_devices: Optional[List[str]] = None,
    cpu_mode: str = "pg_proc",
    io_mode: str = "disk_device",
    memory_mode: str = "pg_proc_pss",
    max_runtime_s: float = 0.0,
    fixed_horizon_success: bool = False,
    ssh_control_master: bool = False,
    light_remote_script: bool = False,
    interval_correct_sleep: bool = False,
) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]]]:
    """
    Run workload locally while polling VM runtime metrics over SSH.
    By default CPU and memory are PostgreSQL process scoped, while IO is read
    from VM disk device counters. PostgreSQL process IO remains available via
    io_mode=pg_proc, but disk_device is the safer default when /proc/<pid>/io
    is not readable by the SSH user.

    Sampler accelerations (SSH ControlMaster, light remote script, interval-correct
    sleep) are opt-in only so in-flight multi-VM batches keep legacy cadence unless
    metrics.vm_runtime explicitly enables them (0949).
    """
    samples_cpu: List[float] = []
    samples_mem: List[float] = []
    samples_io_read: List[float] = []
    samples_io_write: List[float] = []
    samples_io: List[float] = []
    workload_output_lines: List[str] = []

    # Validate all modes before starting the measured process.  An invalid mode
    # used to raise after Popen and before entering the cleanup block.
    cpu_mode = (cpu_mode or "pg_proc").strip().lower()
    if cpu_mode not in ("pg_proc", "vm_busy"):
        raise RuntimeError(f"unsupported vm runtime cpu_mode={cpu_mode!r}")
    io_mode = (io_mode or "disk_device").strip().lower()
    if io_mode not in ("pg_proc", "disk_device"):
        raise RuntimeError(f"unsupported vm runtime io_mode={io_mode!r}")
    memory_mode = (memory_mode or "pg_proc_pss").strip().lower()
    if memory_mode not in ("pg_proc_pss", "pg_proc_rss", "vm_used"):
        raise RuntimeError(f"unsupported vm runtime memory_mode={memory_mode!r}")

    proc = subprocess.Popen(
        workload_cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    monitor_start_ts = time.time()
    prev_snap: Optional[Dict[str, float]] = None
    prev_ts: Optional[float] = None

    import fcntl

    fd = proc.stdout.fileno()
    fl = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

    ssh_metric_source = "vm_runtime_ssh"
    cpu_source = "vm_pg_proc_cpu"
    io_source = "vm_disk_device" if io_mode == "disk_device" else "vm_pg_proc_io"
    io_metric_warning = ""
    memory_source = "vm_meminfo_used"
    memory_pg_pid_count = 0.0
    io_pg_pid_count = 0.0
    next_progress_log_ts = monitor_start_ts + 30.0
    max_snapshot_errors = max(
        1,
        int(float(os.environ.get("VM_RUNTIME_SNAPSHOT_MAX_CONSECUTIVE_ERRORS", "20"))),
    )
    consecutive_snapshot_errors = 0
    completed_normally = False
    monitor_error: Optional[BaseException] = None
    workload_returncode: Optional[int] = None
    workload_sigterm_treated_as_success = False
    fixed_horizon_reached = False
    try:
        while True:
            now = time.time()
            if max_runtime_s > 0 and (now - monitor_start_ts) >= max_runtime_s:
                if fixed_horizon_success:
                    fixed_horizon_reached = True
                    break
                raise subprocess.TimeoutExpired(workload_cmd, max_runtime_s)
            try:
                while True:
                    raw = proc.stdout.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace").rstrip("\n")
                    workload_output_lines.append(line)
                    print(line, flush=True)
            except (IOError, OSError):
                pass

            if now >= next_progress_log_ts:
                elapsed_s = now - monitor_start_ts
                print(f"[workload] running elapsed={elapsed_s:.0f}s", flush=True)
                next_progress_log_ts = now + 30.0

            if proc.poll() is not None:
                remaining = proc.stdout.read()
                if remaining:
                    for line in remaining.decode("utf-8", errors="replace").splitlines():
                        workload_output_lines.append(line)
                        print(line, flush=True)
                completed_normally = True
                break

            loop_start = time.perf_counter()
            try:
                snap = _read_vm_runtime_snapshot(
                    host=vm_host,
                    ssh_target=ssh_target,
                    ssh_user=ssh_user,
                    ssh_port=ssh_port,
                    identity_file=ssh_identity_file,
                    connect_timeout_s=connect_timeout_s,
                    disk_devices=disk_devices,
                    cpu_mode=cpu_mode,
                    io_mode=io_mode,
                    memory_mode=memory_mode,
                    ssh_control_master=bool(ssh_control_master),
                    light_remote_script=bool(light_remote_script),
                )
                # Timestamp after counters are collected so dt tracks counter age, not loop entry.
                snap_ts = time.time()
                consecutive_snapshot_errors = 0
            except (RuntimeError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
                consecutive_snapshot_errors += 1
                print(
                    "[workload] VM runtime snapshot skipped "
                    f"({consecutive_snapshot_errors}/{max_snapshot_errors}): {exc}",
                    flush=True,
                )
                if consecutive_snapshot_errors >= max_snapshot_errors:
                    raise RuntimeError(
                        "too many consecutive VM runtime snapshot failures"
                    ) from exc
                time.sleep(max(0.1, min(float(interval_s), 1.0)))
                continue
            stable_phase = (snap_ts - monitor_start_ts) >= max(0.0, warmup_s)

            pg_pid_count = float(snap.get("pg_pid_count", 0.0) or 0.0)
            pg_pss_pid_count = float(snap.get("pg_pss_pid_count", 0.0) or 0.0)
            io_pg_pid_count = float(snap.get("pg_io_pid_count", 0.0) or 0.0)
            memory_pg_pid_count = pg_pid_count
            if memory_mode == "pg_proc_pss" and pg_pss_pid_count > 0:
                mem_used_bytes = max(0.0, float(snap.get("pg_pss_kb", 0.0) or 0.0) * 1024.0)
                memory_source = "vm_pg_proc_pss"
            elif memory_mode in ("pg_proc_pss", "pg_proc_rss") and pg_pid_count > 0:
                mem_used_bytes = max(0.0, float(snap.get("pg_rss_kb", 0.0) or 0.0) * 1024.0)
                memory_source = "vm_pg_proc_rss"
            else:
                mem_used_bytes = max(
                    0.0,
                    (snap.get("mem_total_kb", 0.0) - snap.get("mem_available_kb", 0.0)) * 1024.0,
                )
                memory_source = "vm_meminfo_used"
            if stable_phase:
                samples_mem.append(mem_used_bytes)

            if prev_snap is not None and prev_ts is not None:
                dt = max(1e-6, snap_ts - prev_ts)
                if cpu_mode == "pg_proc" and snap.get("pg_pid_count", 0.0) > 0:
                    clock_ticks = max(1.0, snap.get("clock_ticks", prev_snap.get("clock_ticks", 100.0)))
                    pg_cpu_delta = max(0.0, snap.get("pg_cpu_ticks", 0.0) - prev_snap.get("pg_cpu_ticks", 0.0))
                    cpu_cores = pg_cpu_delta / (dt * clock_ticks)
                    cpu_source = "vm_pg_proc_cpu"
                else:
                    cpu_total_delta = max(0.0, snap.get("cpu_total", 0.0) - prev_snap.get("cpu_total", 0.0))
                    cpu_idle_delta = max(0.0, snap.get("cpu_idle", 0.0) - prev_snap.get("cpu_idle", 0.0))
                    cpu_busy_ratio = 0.0
                    if cpu_total_delta > 0:
                        cpu_busy_ratio = max(0.0, min(1.0, 1.0 - (cpu_idle_delta / cpu_total_delta)))
                    cpu_count = max(1.0, snap.get("cpu_count", prev_snap.get("cpu_count", 1.0)))
                    cpu_cores = cpu_busy_ratio * cpu_count
                    cpu_source = "vm_runtime_ssh"

                prev_io_pg_pid_count = float(prev_snap.get("pg_io_pid_count", 0.0) or 0.0)
                use_pg_proc_io = (
                    io_mode == "pg_proc"
                    and snap.get("pg_pid_count", 0.0) > 0
                    and io_pg_pid_count > 0
                    and prev_io_pg_pid_count > 0
                )
                if use_pg_proc_io:
                    io_read_rate = max(
                        0.0,
                        (snap.get("pg_io_read_bytes", 0.0) - prev_snap.get("pg_io_read_bytes", 0.0)) / dt,
                    )
                    io_write_rate = max(
                        0.0,
                        (snap.get("pg_io_write_bytes", 0.0) - prev_snap.get("pg_io_write_bytes", 0.0)) / dt,
                    )
                    io_source = "vm_pg_proc_io"
                else:
                    io_read_rate = max(
                        0.0,
                        (snap.get("io_read_sectors", 0.0) - prev_snap.get("io_read_sectors", 0.0)) * 512.0 / dt,
                    )
                    io_write_rate = max(
                        0.0,
                        (snap.get("io_write_sectors", 0.0) - prev_snap.get("io_write_sectors", 0.0)) * 512.0 / dt,
                    )
                    if io_mode == "pg_proc" and snap.get("pg_pid_count", 0.0) > 0:
                        io_source = "vm_disk_device_fallback_pg_proc_io_unreadable"
                        io_metric_warning = "pg_proc_io_unreadable_fell_back_to_disk_device"
                    else:
                        io_source = "vm_disk_device"
                if stable_phase:
                    samples_cpu.append(cpu_cores)
                    samples_io_read.append(io_read_rate)
                    samples_io_write.append(io_write_rate)
                    samples_io.append(io_read_rate + io_write_rate)

            prev_snap = snap
            prev_ts = snap_ts

            if proc.poll() is not None:
                remaining = proc.stdout.read()
                if remaining:
                    for line in remaining.decode("utf-8", errors="replace").splitlines():
                        workload_output_lines.append(line)
                        print(line, flush=True)
                completed_normally = True
                break
            if bool(interval_correct_sleep):
                # Opt-in: sleep only the remainder of the target interval so slow
                # SSH does not double-penalize sample cadence.
                elapsed = time.perf_counter() - loop_start
                remaining_sleep = float(interval_s) - elapsed
                if remaining_sleep > 0.0:
                    time.sleep(remaining_sleep)
            else:
                # Legacy cadence: always sleep full interval after snapshot.
                time.sleep(max(0.1, interval_s))
    except BaseException as exc:
        monitor_error = exc
        raise
    finally:
        if not completed_normally and proc.poll() is None:
            _terminate_process_group(proc)
        workload_returncode = proc.wait()
        workload_sigterm_treated_as_success = _is_hybench_sigterm_with_samples(
            returncode=workload_returncode,
            workload_cmd=workload_cmd,
            workload_output_lines=workload_output_lines,
            stable_sample_counts={
                "cpu": len(samples_cpu),
                "memory": len(samples_mem),
                "io": len(samples_io),
            },
        )
        if fixed_horizon_reached and all(
            count > 0 for count in (len(samples_cpu), len(samples_mem), len(samples_io))
        ):
            workload_sigterm_treated_as_success = True
        if (
            monitor_error is None
            and workload_returncode != 0
            and not workload_sigterm_treated_as_success
        ):
            raise subprocess.CalledProcessError(workload_returncode, workload_cmd)

    cpu_stats = _stats(samples_cpu)
    mem_stats = _stats(samples_mem)
    io_read_stats = _stats(samples_io_read)
    io_write_stats = _stats(samples_io_write)
    io_stats = _stats(samples_io)

    resource_avg = {
        "cpu": cpu_stats["avg"],
        "memory": mem_stats["avg"],
        "io": io_stats["avg"],
    }
    detailed_stats = {
        "cpu": cpu_stats,
        "memory": mem_stats,
        "io": io_stats,
        "io_read": io_read_stats,
        "io_write": io_write_stats,
        "warmup_s": {"value": float(max(0.0, warmup_s))},
        "cpu_source": {"value": cpu_source},
        "memory_source": {"value": memory_source},
        "memory_pg_pid_count": {"value": float(memory_pg_pid_count)},
        "io_source": {"value": io_source},
        "io_pg_pid_count": {"value": float(io_pg_pid_count)},
        "io_metric_warning": {"value": io_metric_warning},
        "io_disk_devices": {"value": ",".join(disk_devices or [])},
        "vm_host": {"value": str(vm_host)},
        "stable_sample_count": {
            "cpu": float(len(samples_cpu)),
            "memory": float(len(samples_mem)),
            "io": float(len(samples_io)),
        },
        "workload_exit": {
            "returncode": float(workload_returncode if workload_returncode is not None else 0),
            "sigterm_treated_as_success": bool(workload_sigterm_treated_as_success),
            "fixed_horizon_reached": bool(fixed_horizon_reached),
            "fixed_horizon_s": float(max_runtime_s if fixed_horizon_success else 0.0),
        },
        "qps": None,
        "concurrency": None,
        "query_count": None,
        "query_time": None,
    }

    benchmark_parsed = parse_hybench_benchmark_output(workload_output_lines)
    detailed_stats["benchmark"] = benchmark_parsed.get("benchmark") or {}
    detailed_stats["benchmark_latency"] = benchmark_parsed.get("benchmark_latency") or {}
    detailed_stats["workload_output"] = str(benchmark_parsed.get("workload_output") or "")

    return resource_avg, detailed_stats
