#!/usr/bin/env python3
"""Launch one real MySQL-only SGD campaign lane and execute its R0 point.

This is a manual, non-agent launcher.  It owns one fresh template copy, one
resource-isolated MySQL container, one SSH tunnel, one continuous workload,
and the one-second cgroup/DBQPS sampler required by the formal rolling-window
protocol.  R0 is the first phase of the experience-table/all-subset SGD
algorithm, not a readiness probe.
"""

from __future__ import annotations

import argparse
import base64
import configparser
import fcntl
from dataclasses import replace
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Mapping, Sequence
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from formal_benchbase_continuous_session import FormalBenchBaseContinuousSession
from formal_hybench_continuous_session import FormalHyBenchContinuousSession
from formal_job_continuous_session import FormalJobContinuousSession
from formal_mysql_sgd_orchestration import _run_continuous_session
from mysql_cost_adapter import MYSQL_COST_KEYS, MySQLCLIClient, MySQLCostAdapter


HISTORY = ROOT / "history/mysql_sgd_env_agents_20260723"
CONTROL_PLANE_SSH_LOCK = HISTORY / "control_plane/compute00_remote_ssh.lock"
CONTROL_PLANE_SSH_LOCK_TIMEOUT_SECONDS = 300
DATABASE_COUNTER_READ_TIMEOUT_SECONDS = 3.0
PROTOCOL = ROOT / "preflight/frozen_formal_config_20260727_v10/measurement_protocol.json"
IMAGE = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
JAVA = Path("/usr/bin/java")
MYSQL = Path("/usr/bin/mysql")
FORMAL_CLASSES = ROOT / "formal_clients_20260721/classes"
ATTRIBUTION_CLASSES = ROOT / "formal_random_walk_clients_20260721/classes"
CONNECTOR = Path("/home/anonymous/HyBench-2024-master/lib/mysql-connector-java-8.0.30.jar")
HYBENCH_JAR = Path(
    "/disk2/resource_adaptive_mysql/gd_20260718/hybench_client/target/"
    "HyBench-1.0-SNAPSHOT-jar-with-dependencies.jar"
)
HYBENCH_ROOT = Path("/home/anonymous/HyBench-2024-master")
XP_V7_BUNDLE = Path(os.environ.get(
    "MYSQL_XP_RUNTIME_BUNDLE",
    str(
        ROOT
        / "artifacts/mysql_xp_v7/"
        "174d05a7dff2c75af7eb9e8ec9d4c2204d21c38d4a7c512985500213ceea8b8b"
    ),
)).resolve()
XP_V7_CLASSES = XP_V7_BUNDLE / "formal-wrapper-classes"
XP_V7_JAR = (
    XP_V7_BUNDLE
    / "HyBench-1.0-SNAPSHOT-formal-xp-binding-trace-candidate-jar-with-dependencies.jar"
)
STATEMENTS = (
    ROOT / "history/brute_formal_mixed_20260722_v6_remaining_xp_retry2/mysql/"
    "brute_force/A8_r1/round_000_baseline/attempt_01/hybench_runtime/conf/"
    "stmt_mysql_formal_case_normalized.toml"
)
if os.environ.get("MYSQL_XP_RUNTIME_BUNDLE"):
    CONNECTOR = XP_V7_BUNDLE / "runtime/mysql-connector-java-8.0.30.jar"
    STATEMENTS = XP_V7_BUNDLE / "config/stmt_mysql_formal_case_normalized.toml"
_BUNDLED_BENCHBASE_ROOT = ROOT / "formal_benchbase_runtime"
BENCHBASE_ROOT = (
    _BUNDLED_BENCHBASE_ROOT
    if _BUNDLED_BENCHBASE_ROOT.is_dir()
    else Path("/home/anonymous/benchbase-mysql")
)
BENCHBASE_JAR = (
    BENCHBASE_ROOT
    / "target/benchbase-2023-SNAPSHOT-formal-binding-trace-candidate.jar"
    if BENCHBASE_ROOT == _BUNDLED_BENCHBASE_ROOT
    else (
        ROOT
        / "formal_benchbase_src_20260723/target/"
        "benchbase-2023-SNAPSHOT-formal-binding-trace-candidate.jar"
    )
)
TPCH_AGENT = ROOT / "formal_clients_20260721/tpch-seed-agent.jar"
TPCC_SENSITIVE_SQL = Path(
    "/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated/"
    "config/workloads/mysql_tpcc_sensitive_item_semijoin.sql"
)
JOB_QUERY_DIR = (
    HISTORY / "job/job_strict_plan_capture_20260723_0642/artifacts/sql"
)
def _run_database_counter_sample(
    command: Sequence[str], query: str, expected_rows: int,
    runner: Callable[..., Any] = subprocess.run,
) -> tuple[int | None, list[dict[str, Any]]]:
    """Take one bounded counter sample without blocking the workload.

    A timeout is a sample error, not a campaign-fatal sampler exception. One
    short reconnect retry is allowed; a later formal boundary check still
    fails closed if enough samples were lost.
    """
    errors: list[dict[str, Any]] = []
    for attempt, timeout in enumerate((5, 1), 1):
        try:
            completed = runner(
                [*command, "--execute", f"{query};"],
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"counter query rc={completed.returncode}: "
                    f"{(completed.stderr or '')[-1000:]}"
                )
            counter = 0
            received_rows = 0
            for line in completed.stdout.splitlines():
                pieces = line.rstrip("\n").split("\t", 1)
                if len(pieces) == 2 and pieces[1].isdigit():
                    counter += int(pieces[1])
                    received_rows += 1
            if received_rows != expected_rows:
                raise TimeoutError(
                    "independent database counter response had unexpected row count"
                )
            return counter, errors
        except (OSError, TimeoutError, subprocess.TimeoutExpired, RuntimeError) as error:
            errors.append({
                "utc": datetime.now(timezone.utc).isoformat(),
                "epoch_ms": int(time.time() * 1000),
                "attempt": attempt,
                "sample_error": f"{type(error).__name__}: {error}",
            })
    return None, errors


def _run_processlist_sample(
    command: Sequence[str], query: str,
    runner: Callable[..., Any] = subprocess.run,
) -> tuple[list[int], dict[str, Any] | None]:
    """Read workload query ids; one timeout is a missing drain sample."""
    try:
        completed = runner(
            [*command, "-e", query],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"processlist query rc={completed.returncode}: "
                f"{(completed.stderr or '')[-1000:]}"
            )
        return [
            int(value) for value in completed.stdout.splitlines()
            if value.strip().isdigit()
        ], None
    except subprocess.TimeoutExpired as error:
        return [], {
            "utc": datetime.now(timezone.utc).isoformat(),
            "epoch_ms": int(time.time() * 1000),
            "sample_error": f"{type(error).__name__}: {error}",
            "sample_status": "missing",
        }


@dataclass(frozen=True)
class Lane:
    key: str
    scenario: str
    benchmark: str
    host: str
    hostname: str
    project: str
    template: str
    device: str
    cpuset: str
    mems: str
    cpu_cores: float
    memory_bytes: int
    pids: int
    read_bps: int
    write_bps: int
    remote_port: int
    database: str
    target_resource: str
    counter_name: str
    experience: tuple[str, ...]
    buffer_pool_bytes: int = 4294967296


LANES = {
    "job": Lane(
        "job", "job_memory", "job", "anonymous@192.0.2.4", "node02",
        "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job",
        "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/template",
        "/dev/nvme1n1", "0-2,36-38", "0", 3, 6442450944, 512,
        1205862400, 1205862400, 65102, "imdb_job", "memory", "Com_select",
        (str(HISTORY / "job/job_plan_effect_t_20260723T003507Z/T.mysql_job_physical_plan_effect_v1.json"),),
    ),
    "xp": Lane(
        "xp", "hybench_xp_io", "hybench_xp", "anonymous@192.0.2.4", "node02",
        "/mnt/mysql_nvme0n1p1/anonymous/resource_adaptive_mysql/hybench_xp",
        "/mnt/mysql_nvme0n1p1/anonymous/resource_adaptive_mysql/hybench_xp/template_v2/datadir",
        "/dev/nvme0n1", "18-33,54-69", "1", 16, 8589934592, 1024,
        104857600, 104857600, 65104, "hybench_sf10x", "io",
        "Com_select+Com_insert+Com_update+Com_delete",
        (
            str(ROOT / "config/formal/frozen_mysql_experience_table_source_policy_20260723_v1/source_policy.json"),
            str(ROOT / "history/brute_formal_mixed_20260722_v6_remaining_xp_retry2/mysql/brute_force/A8_r1"),
        ),
    ),
    "tpch": Lane(
        "tpch", "tpch_memory", "tpch", "anonymous@192.0.2.3", "node01",
        "/mnt/nvme1n1/anonymous_resource_adaptive/tpch",
        "/mnt/nvme1n1/anonymous/resource_adaptive_mysql/tpch/template",
        "/dev/nvme1n1", "18-20,54-56", "1", 3, 6442450944, 512,
        1205862400, 1205862400, 65103, "tpch_sf10", "memory", "Com_select",
        (
            str(ROOT / "config/formal/frozen_mysql_experience_table_source_policy_20260723_v1/source_policy.json"),
            str(ROOT / "history/brute_formal_mixed_20260722_v6_remaining_v1/mysql/brute_force/A7_r1"),
        ),
    ),
    "tpcc": Lane(
        "tpcc", "tpcc_cpu", "tpcc", "anonymous@192.0.2.3", "node01",
        "/mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/tpcc",
        "/mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/tpcc/template/datadir",
        "/dev/nvme0n1", "21-24,57-60", "1", 4, 8589934592, 512,
        1205862400, 1205862400, 65106, "tpcc", "cpu",
        "Com_insert+Com_update+Com_delete",
        (
            str(ROOT / "config/formal/frozen_mysql_operator_physics_experience_tables_20260724_v1/T.tpcc_cpu.operator_physics_v1.json"),
            str(ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/plan_workload_registries/tpcc.production_binding_capture_contract_v1.json"),
        ),
    ),
}

R0_TARGET_BAND = (0.85, 0.95)
R0_NON_TARGET_BAND = (0.30, 0.50)


def evaluate_r0_profile(
    lane: Lane, measurement: Mapping[str, Any],
) -> dict[str, Any]:
    selected = (
        measurement.get("rolling_measurement_control", {})
        .get("selected_observation", {})
    )
    resources = selected.get("resource_means", {})
    utilization: dict[str, float] = {}
    for resource in ("cpu", "memory", "io"):
        try:
            value = float(resources[resource])
        except (KeyError, TypeError, ValueError) as error:
            return {
                "passed": False,
                "target_resource": lane.target_resource,
                "reason": f"missing_or_invalid_{resource}",
                "utilization": utilization,
            }
        if not math.isfinite(value):
            return {
                "passed": False,
                "target_resource": lane.target_resource,
                "reason": f"non_finite_{resource}",
                "utilization": utilization,
            }
        utilization[resource] = value
    checks = {}
    for resource, value in utilization.items():
        band = (
            R0_TARGET_BAND
            if resource == lane.target_resource
            else R0_NON_TARGET_BAND
        )
        checks[resource] = {
            "value": value,
            "band": list(band),
            "passed": band[0] <= value <= band[1],
        }
    return {
        "passed": all(check["passed"] for check in checks.values()),
        "target_resource": lane.target_resource,
        "utilization": utilization,
        "checks": checks,
        "reason": None,
    }


def ssh_transport_options(lane: Lane) -> list[str]:
    """Return the frozen control-plane route for a remote lane."""
    return [
        "-o", "ServerAliveInterval=10",
        "-o", "ServerAliveCountMax=6",
    ]


def remote_ssh_command(lane: Lane, *remote_args: str) -> list[str]:
    return [
        "ssh", *ssh_transport_options(lane),
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        lane.host, *remote_args,
    ]


def tunnel_ssh_command(lane: Lane) -> list[str]:
    return [
        "ssh", *ssh_transport_options(lane),
        "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
        "-L", f"127.0.0.1:{lane.remote_port}:127.0.0.1:{lane.remote_port}",
        lane.host,
    ]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def mysql_password() -> str:
    parser = configparser.ConfigParser(interpolation=None)
    credential = Path("/home/anonymous/.my.cnf")
    parser.read(credential, encoding="utf-8")
    value = parser.get("client", "password", fallback="").strip("\"'")
    if not value:
        raise RuntimeError("local MySQL credential is absent")
    return value


def defaults_text(password: str) -> str:
    suffix = f"password={password}\n" if password else ""
    return f"[client]\nuser=root\n{suffix}"


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run(command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(value) for value in command], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True, **kwargs,
    )


class Campaign:
    def __init__(
        self,
        lane: Lane,
        output: Path,
        run_id: str,
        candidate_costs: Mapping[str, float] | None = None,
        measurement_seconds: int = 600,
        minimum_measurement_seconds: int = 180,
        minimum_client_completions: int = 0,
        capture_general_log: bool = False,
        capture_tpch_bindings: bool = False,
        binding_start_query: int = 1,
        binding_prefix_log: Path | None = None,
        require_r0_profile_gate: bool = False,
    ) -> None:
        self.lane = lane
        self.output = output
        self.run_id = run_id
        self.container = f"ra_mysql_sgd_{lane.key}_{run_id.lower()}"
        self.remote_root = f"{lane.project}/formal_sgd_runs/{run_id}"
        self.remote_data = f"{self.remote_root}/datadir"
        self.state_path = output / "campaign_runtime.json"
        self.resource_rows: list[dict[str, Any]] = []
        self.database_rows: list[dict[str, Any]] = []
        self.database_attempt_rows: list[dict[str, Any]] = []
        self.rows_lock = threading.Lock()
        self.sampler: subprocess.Popen[str] | None = None
        self.sampler_thread: threading.Thread | None = None
        self.database_sampler_thread: threading.Thread | None = None
        self.database_sampler_threads: list[threading.Thread] = []
        self.database_sampler_stop = threading.Event()
        self.tunnel: subprocess.Popen[str] | None = None
        self.session: Any | None = None
        self.stop_requested = False
        self.candidate_costs = {
            str(name): float(value)
            for name, value in (candidate_costs or {}).items()
        }
        self.measurement_seconds = int(measurement_seconds)
        self.minimum_measurement_seconds = int(minimum_measurement_seconds)
        self.minimum_client_completions = int(minimum_client_completions)
        self.capture_tpch_bindings = bool(capture_tpch_bindings)
        self.binding_start_query = int(binding_start_query)
        self.binding_prefix_log = binding_prefix_log
        self.capture_general_log = bool(capture_general_log or capture_tpch_bindings)
        self.require_r0_profile_gate = bool(require_r0_profile_gate)
        self.capture_remote_log = f"/var/lib/mysql/{self.run_id}_general.log"
        self.capture_log_enabled = False
        self.capture_log_exported = False
        self.capture_session_succeeded = False
        self._sampler_error: str | None = None
        self._database_sampler_error: str | None = None
        self._sampler_stderr: Any | None = None
        self._sampler_script: str | None = None
        self._sampler_reconnect_count = 0
        self.r0_gate_valid: bool | None = None
        self.algorithm_c_authorized = False
        self.xap_authorized = False
        # Authentication is normalized only on the ephemeral fresh copy.  The
        # sealed template is never mounted writable or modified.
        self.password = secrets.token_hex(24)

    def credential_candidates(self) -> list[str]:
        return [self.password]

    def state(self, phase: str, **extra: Any) -> None:
        with self.rows_lock:
            resources = len(self.resource_rows)
            databases = len(self.database_rows)
        base = {
            "schema_version": "manual_mysql_sgd_campaign_runtime_v1",
            "updated_utc": utc_now(),
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "algorithm": "experience_table_all_nonempty_subset_sgd",
            "algorithm_contract": {
                "subsets": "all_2^k_minus_1_nonempty_subsets",
                "finite_difference_sgd": False,
                "optimistic_pruning": "three_resource_joint_box_true_max_fail_open",
                "qps_model_error": None,
                "strict_physical_plan_cluster": True,
                "real_replay_recall": "one_global_deduplicated_fixed_top_5",
                "accept": (
                    "DBQPS_strictly_up_and_target_utilization_strictly_down_"
                    "and_each_non_target_strictly_below_0.8"
                ),
                "accepted_interval_shrink_target": 10,
                "stability": (
                    "180_then_plus_30_to_"
                    f"{self.measurement_seconds}_latest_six_resource_windows"
                ),
                "io_utilization": "(read_throughput+write_throughput)/(read_cap+write_cap)",
                "checkpoint_noncontiguous_replayed_ranks": True,
            },
            "phase": phase,
            "capture_general_log": self.capture_general_log,
            "capture_tpch_bindings": self.capture_tpch_bindings,
            "scenario": self.lane.scenario,
            "benchmark": self.lane.benchmark,
            "target_resource": self.lane.target_resource,
            "run_id": self.run_id,
            "campaign_pid": os.getpid(),
            "container": self.container,
            "host": self.lane.host,
            "hostname": self.lane.hostname,
            "device": self.lane.device,
            "remote_work_copy": self.remote_data,
            "accepted_shrinkage": 0,
            "resource_sample_count": resources,
            "database_sample_count": databases,
            "experience_sources": list(self.lane.experience),
            "experience_status": (
                "CURRENT_REAL_T" if self.lane.key in {"ap", "job"}
                else "POST_20260721_REAL_PRIOR_PENDING_CURRENT_R0_VALIDATION"
            ),
            "pruning_status": "FAIL_OPEN_UNTIL_JOINT_COVERAGE_PROVEN",
            "resource_envelope": {
                "cpu_quota_cores": self.lane.cpu_cores,
                "cpuset_cpus": self.lane.cpuset,
                "cpuset_mems": self.lane.mems,
                "memory_bytes": self.lane.memory_bytes,
                "memory_swap_bytes": self.lane.memory_bytes,
                "pids_max": self.lane.pids,
                "io_read_bps": self.lane.read_bps,
                "io_write_bps": self.lane.write_bps,
                "innodb_buffer_pool_bytes": self.lane.buffer_pool_bytes,
            },
            "non_interference": {
                "fixed_cpu_affinity": True,
                "fixed_numa_memory_node": True,
                "job_xp_cpu_sets_disjoint": True,
                "job_xp_numa_nodes_disjoint": True,
                "job_xp_data_devices_disjoint": True,
                "co_resident_postgresql_allowed_if_stable": True,
                "rolling_dynamic_gate": "IN_PROGRESS" if phase == "R0_RUNNING" else phase,
            },
            "ephemeral_authentication": {
                "normalized_on_fresh_copy": True,
                "sealed_template_modified": False,
                "credential_recorded_in_manifest": False,
            },
        }
        base.update(extra)
        atomic_json(self.state_path, base)

    def remote(self, script: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
        CONTROL_PLANE_SSH_LOCK.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + CONTROL_PLANE_SSH_LOCK_TIMEOUT_SECONDS
        acquired = False
        handle = CONTROL_PLANE_SSH_LOCK.open("a+", encoding="utf-8")
        try:
            while not acquired:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            "timed out waiting for serialized compute00 control-plane SSH slot"
                        )
                    time.sleep(1.0)
            last_error: BaseException | None = None
            for attempt in range(1, 4):
                try:
                    return run(
                        remote_ssh_command(self.lane, "bash", "-s"),
                        input=script, timeout=timeout,
                    )
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                    last_error = error
                    retryable = (
                        isinstance(error, subprocess.TimeoutExpired)
                        or getattr(error, "returncode", None) == 255
                    )
                    if not retryable or attempt == 3:
                        break
                    time.sleep(float(attempt * 2))
            assert last_error is not None
            detail = ""
            if isinstance(last_error, subprocess.CalledProcessError):
                stdout = (last_error.stdout or "").strip()
                stderr = (last_error.stderr or "").strip()
                detail = f"; stdout_tail={stdout[-2000:]!r}; stderr_tail={stderr[-2000:]!r}"
            raise RuntimeError(
                f"remote command failed after 3 attempts: {last_error}{detail}"
            ) from last_error
        finally:
            if acquired:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def template_copy_command(self) -> str:
        """Return the isolated template-copy command for this campaign."""
        lane = self.lane
        return f"""docker run --rm --network none --cpus 0.5 --cpuset-cpus {lane.cpuset.split(',')[0]} \
  --cpuset-mems {lane.mems} --memory 268435456 --memory-swap 268435456 --pids-limit 64 \
  -v {lane.template}:/template:ro -v {self.remote_data}:/copy:rw \
  --entrypoint /bin/sh {IMAGE} \
  -c 'cp -a --reflink=auto /template/. /copy/; rm -f /copy/mysql.sock /copy/mysqld.sock /copy/mysqld.pid; chown -R mysql:mysql /copy; find /copy -type d -exec chmod 0750 {{}} +; find /copy -type f -exec chmod 0640 {{}} +'"""

    def prepare_remote(self) -> None:
        lane = self.lane
        candidates = self.credential_candidates()
        credential_commands = []
        for index, candidate in enumerate(candidates):
            encoded = base64.b64encode(
                defaults_text(candidate).encode()
            ).decode("ascii")
            credential_commands.append(
                f"printf '%s' {encoded} | base64 -d >{self.remote_root}/credentials/candidate_{index}.cnf"
            )
        credentials = "\n".join(credential_commands)
        candidate_indices = " ".join(str(index) for index in range(len(candidates)))
        script = f"""set -euo pipefail
umask 077
test "$(hostname)" = {lane.hostname}
test -d {lane.template}
test ! -e {self.remote_root}
test -z "$(docker ps -aq --filter name=^{self.container}$)"
test -z "$(docker ps -aq --filter name=^{self.container}_bootstrap$)"
printf 'RA_PREP_STAGE=preflight_passed\n'
mkdir -p {self.remote_root}/credentials
{credentials}
chmod 600 {self.remote_root}/credentials/candidate_*.cnf
{self.template_copy_command()}
printf 'RA_PREP_STAGE=template_copied\n'
docker run -d --name {self.container}_bootstrap --network none \
  --cpus 1 --cpuset-cpus {lane.cpuset.split(',')[0]} --cpuset-mems {lane.mems} \
  --memory 2147483648 --memory-swap 2147483648 --pids-limit 128 \
  -v {self.remote_data}:/var/lib/mysql:rw {IMAGE} mysqld \
  --skip-grant-tables=ON --skip-networking=ON --innodb-buffer-pool-size=536870912 \
  --innodb-flush-method=O_DIRECT --skip-log-bin --max-connections=32 >/dev/null
for ignored in $(seq 1 180); do
  docker exec {self.container}_bootstrap mysqladmin -uroot ping --silent >/dev/null 2>&1 && break
  test "$(docker inspect {self.container}_bootstrap --format '{{{{.State.Running}}}}')" = true || break
  sleep 1
done
docker exec {self.container}_bootstrap mysqladmin -uroot ping --silent >/dev/null 2>&1
printf 'RA_PREP_STAGE=bootstrap_ready\n'
docker exec {self.container}_bootstrap mysql -uroot -NBe \
  "FLUSH PRIVILEGES; CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED WITH caching_sha2_password BY '{self.password}'; ALTER USER 'root'@'%' IDENTIFIED WITH caching_sha2_password BY '{self.password}'; ALTER USER 'root'@'localhost' IDENTIFIED WITH caching_sha2_password BY '{self.password}';"
docker stop --time 120 {self.container}_bootstrap >/dev/null
docker rm {self.container}_bootstrap >/dev/null
printf 'RA_PREP_STAGE=credentials_normalized\n'
docker run -d --name {self.container} \
  --label ra.owner=manual_mysql_sgd_v1 --label ra.scenario={lane.scenario} \
  --cpus {lane.cpu_cores} --cpuset-cpus {lane.cpuset} --cpuset-mems {lane.mems} \
  --memory {lane.memory_bytes} --memory-swap {lane.memory_bytes} --pids-limit {lane.pids} \
  --device-read-bps {lane.device}:{lane.read_bps} --device-write-bps {lane.device}:{lane.write_bps} \
  -p 127.0.0.1:{lane.remote_port}:3306 -v {self.remote_data}:/var/lib/mysql:rw \
  -v {self.remote_root}/credentials:/run/ra-credentials:ro \
  {IMAGE} mysqld --bind-address=0.0.0.0 \
  --innodb-buffer-pool-size={lane.buffer_pool_bytes} --innodb-flush-method=O_DIRECT \
  --innodb-flush-log-at-trx-commit=2 --skip-log-bin --max-connections=96 \
  --performance-schema=ON >/dev/null
printf 'RA_PREP_STAGE=final_container_started\n'
cid=$(docker inspect -f '{{{{.Id}}}}' {self.container})
devno=$(lsblk -dn -o MAJ:MIN {lane.device} | tr -d '[:space:]')
test -n "$devno"
# Some daemon versions accept --device-*-bps but omit it from both inspect
# and the live cgroup.  Prefer Docker's already-applied value; only use the
# host's non-interactive privilege path when the authoritative cgroup really
# lacks the requested limit.  Hosts 01/02 deliberately do not grant sudo to
# the experiment account, while their Docker daemon applies these values.
cg=/sys/fs/cgroup/blkio/docker/$cid
read_bps=$(awk -v d="$devno" '$1==d{{print $2}}' "$cg/blkio.throttle.read_bps_device")
write_bps=$(awk -v d="$devno" '$1==d{{print $2}}' "$cg/blkio.throttle.write_bps_device")
if test "$read_bps" != {lane.read_bps}; then
  printf '%s %s\n' "$devno" {lane.read_bps} \
    | sudo -n tee "$cg/blkio.throttle.read_bps_device" >/dev/null
fi
if test "$write_bps" != {lane.write_bps}; then
  printf '%s %s\n' "$devno" {lane.write_bps} \
    | sudo -n tee "$cg/blkio.throttle.write_bps_device" >/dev/null
fi
printf 'RA_PREP_STAGE=io_enforced\n'
for ignored in $(seq 1 180); do
  docker exec {self.container} mysqladmin -uroot ping --silent >/dev/null 2>&1 && break
  sleep 1
done
docker exec {self.container} mysqladmin -uroot ping --silent >/dev/null 2>&1
printf 'RA_PREP_STAGE=final_mysql_ready\n'
selected=''
for index in {candidate_indices}; do
  if docker exec {self.container} mysql --defaults-file=/run/ra-credentials/candidate_${{index}}.cnf -NBe 'SELECT 1' >/dev/null 2>&1; then
    selected=$index
    break
  fi
done
test -n "$selected"
printf 'RA_PREP_STAGE=credential_selected\n'
cp {self.remote_root}/credentials/candidate_${{selected}}.cnf {self.remote_root}/credentials/selected.cnf
chmod 600 {self.remote_root}/credentials/selected.cnf
docker exec {self.container} mysql --defaults-file=/run/ra-credentials/selected.cnf -NBe \
  'UPDATE mysql.server_cost SET cost_value=NULL; UPDATE mysql.engine_cost SET cost_value=NULL; FLUSH OPTIMIZER_COSTS;'
printf 'RA_PREP_STAGE=costs_reset\n'
read_bps=$(awk -v d="$devno" '$1==d{{print $2}}' \
  "$cg/blkio.throttle.read_bps_device")
write_bps=$(awk -v d="$devno" '$1==d{{print $2}}' \
  "$cg/blkio.throttle.write_bps_device")
printf 'RA_VERIFY_IO=%s/%s\n' "$read_bps" "$write_bps"
test "$read_bps" = {lane.read_bps}
test "$write_bps" = {lane.write_bps}
buffer_pool=$(docker exec {self.container} mysql \
  --defaults-file=/run/ra-credentials/selected.cnf -NBe \
  "SELECT @@GLOBAL.innodb_buffer_pool_size")
printf 'RA_VERIFY_BUFFER_POOL=%s\n' "$buffer_pool"
test "$buffer_pool" = {lane.buffer_pool_bytes}
printf 'RA_SELECTED=%s\\n' "$selected"
printf 'RA_CGROUP_DEVICE=%s\\n' "$devno"
printf 'RA_CGROUP_CPU_QUOTA_US=%s\\n' \
  "$(cat /sys/fs/cgroup/cpu/docker/$cid/cpu.cfs_quota_us)"
printf 'RA_CGROUP_CPU_PERIOD_US=%s\\n' \
  "$(cat /sys/fs/cgroup/cpu/docker/$cid/cpu.cfs_period_us)"
printf 'RA_CGROUP_CPUSET_CPUS=%s\\n' \
  "$(cat /sys/fs/cgroup/cpuset/docker/$cid/cpuset.cpus)"
printf 'RA_CGROUP_CPUSET_MEMS=%s\\n' \
  "$(cat /sys/fs/cgroup/cpuset/docker/$cid/cpuset.mems)"
printf 'RA_CGROUP_MEMORY_BYTES=%s\\n' \
  "$(cat /sys/fs/cgroup/memory/docker/$cid/memory.limit_in_bytes)"
printf 'RA_CGROUP_MEMORY_SWAP_BYTES=%s\\n' \
  "$(cat /sys/fs/cgroup/memory/docker/$cid/memory.memsw.limit_in_bytes)"
printf 'RA_CGROUP_PIDS_MAX=%s\\n' \
  "$(cat /sys/fs/cgroup/pids/docker/$cid/pids.max)"
printf 'RA_CGROUP_READ_BPS=%s\\n' "$read_bps"
printf 'RA_CGROUP_WRITE_BPS=%s\\n' "$write_bps"
printf 'RA_MYSQL_BUFFER_POOL_BYTES=%s\\n' "$buffer_pool"
docker inspect {self.container} --format '{{{{json .}}}}'
"""
        result = self.remote(
            script,
            timeout=int(getattr(self, "prepare_timeout_seconds", 600)),
        )
        (self.output / "remote_prepare.stdout.log").write_text(result.stdout, encoding="utf-8")
        (self.output / "remote_prepare.stderr.log").write_text(result.stderr, encoding="utf-8")
        lines = result.stdout.splitlines()
        selected_lines = [line for line in lines if line.startswith("RA_SELECTED=")]
        if len(selected_lines) != 1:
            raise RuntimeError("remote credential selection cardinality differs")
        selected = int(selected_lines[0].split("=", 1)[1])
        if selected < 0 or selected >= len(candidates):
            raise RuntimeError("remote credential selection index is invalid")
        self.password = candidates[selected]
        private = self.output / "private"
        private.mkdir(parents=True, exist_ok=True)
        local_defaults = private / "mysql.cnf"
        local_defaults.write_text(
            defaults_text(self.password), encoding="utf-8"
        )
        local_defaults.chmod(0o600)
        inspect = json.loads(lines[-1])
        if not isinstance(inspect, dict):
            raise RuntimeError("remote docker inspect result is not a mapping")
        host = inspect["HostConfig"]
        expected = {
            "CpusetCpus": lane.cpuset, "CpusetMems": lane.mems,
            "Memory": lane.memory_bytes, "MemorySwap": lane.memory_bytes,
            "PidsLimit": lane.pids,
        }
        for key, value in expected.items():
            if host.get(key) != value:
                raise RuntimeError(f"effective container {key} differs: {host.get(key)!r}/{value!r}")
        marker_values: dict[str, str] = {}
        for line in lines:
            if line.startswith("RA_CGROUP_") or line.startswith("RA_MYSQL_"):
                name, raw_value = line.split("=", 1)
                if name in marker_values:
                    raise RuntimeError(f"duplicate remote readback marker: {name}")
                marker_values[name] = raw_value
        required_markers = {
            "RA_CGROUP_DEVICE",
            "RA_CGROUP_CPU_QUOTA_US",
            "RA_CGROUP_CPU_PERIOD_US",
            "RA_CGROUP_CPUSET_CPUS",
            "RA_CGROUP_CPUSET_MEMS",
            "RA_CGROUP_MEMORY_BYTES",
            "RA_CGROUP_MEMORY_SWAP_BYTES",
            "RA_CGROUP_PIDS_MAX",
            "RA_CGROUP_READ_BPS",
            "RA_CGROUP_WRITE_BPS",
            "RA_MYSQL_BUFFER_POOL_BYTES",
        }
        if set(marker_values) != required_markers:
            raise RuntimeError(
                "remote effective readback marker mismatch: "
                f"{sorted(set(marker_values) ^ required_markers)}"
            )
        numeric = {
            name: int(marker_values[name])
            for name in required_markers
            if name not in {
                "RA_CGROUP_DEVICE",
                "RA_CGROUP_CPUSET_CPUS",
                "RA_CGROUP_CPUSET_MEMS",
            }
        }
        if numeric["RA_CGROUP_CPU_QUOTA_US"] / numeric["RA_CGROUP_CPU_PERIOD_US"] != lane.cpu_cores:
            raise RuntimeError("effective cgroup CPU quota differs")
        if marker_values["RA_CGROUP_CPUSET_CPUS"] != lane.cpuset:
            raise RuntimeError("effective cgroup CPU set differs")
        if marker_values["RA_CGROUP_CPUSET_MEMS"] != lane.mems:
            raise RuntimeError("effective cgroup NUMA set differs")
        if not 0 <= lane.memory_bytes - numeric["RA_CGROUP_MEMORY_BYTES"] < 4096:
            raise RuntimeError("effective cgroup memory limit differs beyond page alignment")
        if numeric["RA_CGROUP_MEMORY_SWAP_BYTES"] != numeric["RA_CGROUP_MEMORY_BYTES"]:
            raise RuntimeError("effective cgroup memory+swap limit differs")
        if numeric["RA_CGROUP_PIDS_MAX"] != lane.pids:
            raise RuntimeError("effective cgroup pids limit differs")
        if numeric["RA_CGROUP_READ_BPS"] != lane.read_bps or numeric["RA_CGROUP_WRITE_BPS"] != lane.write_bps:
            raise RuntimeError("effective cgroup I/O bandwidth differs")
        if numeric["RA_MYSQL_BUFFER_POOL_BYTES"] != lane.buffer_pool_bytes:
            raise RuntimeError("effective InnoDB buffer-pool size differs")
        atomic_json(self.output / "resource_readback.json", {
            "verified": True, "requested": expected,
            "nano_cpus": host.get("NanoCpus"),
            "blkio_device_read_bps": host.get("BlkioDeviceReadBps"),
            "blkio_device_write_bps": host.get("BlkioDeviceWriteBps"),
            "container_id": inspect["Id"],
            "credential_candidate_index": selected,
            "effective_cgroup": {
                "device_major_minor": marker_values["RA_CGROUP_DEVICE"],
                "cpu_quota_us": numeric["RA_CGROUP_CPU_QUOTA_US"],
                "cpu_period_us": numeric["RA_CGROUP_CPU_PERIOD_US"],
                "cpuset_cpus": marker_values["RA_CGROUP_CPUSET_CPUS"],
                "cpuset_mems": marker_values["RA_CGROUP_CPUSET_MEMS"],
                "memory_bytes": numeric["RA_CGROUP_MEMORY_BYTES"],
                "memory_swap_bytes": numeric["RA_CGROUP_MEMORY_SWAP_BYTES"],
                "pids_max": numeric["RA_CGROUP_PIDS_MAX"],
                "io_read_bps": numeric["RA_CGROUP_READ_BPS"],
                "io_write_bps": numeric["RA_CGROUP_WRITE_BPS"],
            },
            "effective_mysql": {
                "innodb_buffer_pool_bytes": numeric["RA_MYSQL_BUFFER_POOL_BYTES"],
            },
        })
        atomic_json(self.output / "effective_resource_readback_v2.json", {
            "schema_version": "mysql_sgd_effective_resource_readback_v2",
            "captured_at_utc": utc_now(),
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "scenario": lane.scenario,
            "benchmark": lane.benchmark,
            "run_id": self.run_id,
            "host": lane.host,
            "container": self.container,
            "verified": True,
            "requested": {
                "cpu_quota_cores": lane.cpu_cores,
                "cpuset_cpus": lane.cpuset,
                "cpuset_mems": lane.mems,
                "memory_bytes": lane.memory_bytes,
                "memory_swap_bytes": lane.memory_bytes,
                "pids_max": lane.pids,
                "io_read_bps": lane.read_bps,
                "io_write_bps": lane.write_bps,
                "innodb_buffer_pool_bytes": lane.buffer_pool_bytes,
            },
            "effective": {
                "container_id": inspect["Id"],
                "cpu_quota_us": numeric["RA_CGROUP_CPU_QUOTA_US"],
                "cpu_period_us": numeric["RA_CGROUP_CPU_PERIOD_US"],
                "cpuset_cpus": marker_values["RA_CGROUP_CPUSET_CPUS"],
                "cpuset_mems": marker_values["RA_CGROUP_CPUSET_MEMS"],
                "memory_bytes": numeric["RA_CGROUP_MEMORY_BYTES"],
                "memory_swap_bytes": numeric["RA_CGROUP_MEMORY_SWAP_BYTES"],
                "pids_max": numeric["RA_CGROUP_PIDS_MAX"],
                "io_device_major_minor": marker_values["RA_CGROUP_DEVICE"],
                "io_read": (
                    f"{marker_values['RA_CGROUP_DEVICE']} "
                    f"{numeric['RA_CGROUP_READ_BPS']}"
                ),
                "io_read_bps": numeric["RA_CGROUP_READ_BPS"],
                "io_write": (
                    f"{marker_values['RA_CGROUP_DEVICE']} "
                    f"{numeric['RA_CGROUP_WRITE_BPS']}"
                ),
                "io_write_bps": numeric["RA_CGROUP_WRITE_BPS"],
                "buffer_pool_bytes": numeric["RA_MYSQL_BUFFER_POOL_BYTES"],
            },
        })
        inventory = self.remote(
            "ps -eo pid,psr,rss,comm,args; docker ps --no-trunc; "
            "docker inspect $(docker ps -q) 2>/dev/null || true", timeout=60,
        )
        (self.output / "co_resident_inventory.txt").write_text(
            inventory.stdout + inventory.stderr, encoding="utf-8"
        )

    def start_tunnel(self) -> None:
        self.tunnel = subprocess.Popen(
            tunnel_ssh_command(self.lane),
            stdout=subprocess.DEVNULL,
            stderr=(self.output / "ssh_tunnel.stderr.log").open("w", encoding="utf-8"),
            start_new_session=True,
        )
        for _ in range(120):
            if self.tunnel.poll() is not None:
                raise RuntimeError(f"SSH tunnel exited: {self.tunnel.returncode}")
            try:
                probe = subprocess.run(
                    [str(MYSQL), f"--defaults-file={self.output / 'private/mysql.cnf'}",
                     "--protocol=tcp", "--host=127.0.0.1",
                     f"--port={self.lane.remote_port}", "--user=root", "--connect-timeout=3",
                     "--ssl-mode=DISABLED", "--get-server-public-key", "-NBe", "SELECT 1"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                    timeout=5,
                )
            except subprocess.TimeoutExpired:
                time.sleep(1)
                continue
            if probe.returncode == 0:
                return
            time.sleep(1)
        raise RuntimeError("local MySQL tunnel did not become ready")

    def apply_candidate_costs(self) -> None:
        """Write one frozen candidate and verify it through fresh CLI sessions."""
        if not self.candidate_costs:
            return
        unknown = sorted(set(self.candidate_costs) - set(MYSQL_COST_KEYS))
        if unknown:
            raise ValueError(f"unsupported candidate MySQL costs: {unknown}")
        client = MySQLCLIClient({
            "mysql_binary": str(MYSQL),
            "defaults_extra_file": str(self.output / "private/mysql.cnf"),
            "defaults_file_only": True,
            "host": "127.0.0.1",
            "port": self.lane.remote_port,
            "user": "root",
            "database": self.lane.database,
            "query_timeout_sec": 30,
        })
        adapter = MySQLCostAdapter(client)
        before = adapter.snapshot()
        MySQLCostAdapter.save_snapshot(
            self.output / "cost_snapshot_before.json", before
        )
        adapter.apply(self.candidate_costs)
        effective = adapter.effective_values()
        mismatches = {
            name: {"requested": requested, "effective": effective.get(name)}
            for name, requested in self.candidate_costs.items()
            if name not in effective
            or not math.isclose(
                float(effective[name]),
                float(requested),
                rel_tol=5e-6,
                abs_tol=1e-8,
            )
        }
        if mismatches:
            raise RuntimeError(
                "candidate MySQL cost readback differs: "
                + json.dumps(mismatches, sort_keys=True)
            )
        atomic_json(self.output / "candidate_cost_readback.json", {
            "schema_version": "mysql_candidate_cost_readback_v1",
            "requested_active_costs": self.candidate_costs,
            "effective_all_costs": {
                name: effective[name] for name in MYSQL_COST_KEYS
            },
            "flush_optimizer_costs": True,
            "fresh_connection_readback": True,
        })

    def start_sampler(self) -> None:
        if self.lane.key == "xp":
            query = (
                "SHOW GLOBAL STATUS WHERE Variable_name IN "
                "('Com_select','Com_insert','Com_update','Com_delete')"
            )
        else:
            query = "SHOW GLOBAL STATUS LIKE 'Com_select'"
        script = f"""set -euo pipefail
cid=$(docker inspect {self.container} --format '{{{{.Id}}}}')
cpu=/sys/fs/cgroup/cpuacct/docker/$cid/cpuacct.usage
mem=/sys/fs/cgroup/memory/docker/$cid
bio=/sys/fs/cgroup/blkio/docker/$cid/blkio.throttle.io_service_bytes
while docker inspect {self.container} --format '{{{{.State.Running}}}}' 2>/dev/null | grep -qx true; do
  epoch=$(date +%s%3N)
  cpu_ns=$(cat "$cpu")
  usage=$(cat "$mem/memory.usage_in_bytes")
  inactive=$(awk '$1=="total_inactive_file"{{print $2}}' "$mem/memory.stat")
  read_bytes=$(awk '$2=="Read"{{value+=$3}}END{{print value+0}}' "$bio")
  write_bytes=$(awk '$2=="Write"{{value+=$3}}END{{print value+0}}' "$bio")
  printf 'R\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' \
    "$epoch" "$cpu_ns" "$usage" "$inactive" "$read_bytes" "$write_bytes"
  sleep 0.90
done
"""
        self._sampler_script = script
        self._sampler_stderr = (self.output / "sampler.stderr.log").open(
            "a", encoding="utf-8"
        )
        self.sampler = self._open_resource_sampler()
        self.sampler_thread = threading.Thread(target=self._consume_samples, daemon=True)
        self.sampler_thread.start()
        # Query the dedicated database through the already-established local
        # SSH tunnel.  This keeps the database-side counter on its own exact
        # one-second clock and avoids charging repeated `docker exec` client
        # startup to the constrained MySQL cgroup.
        self.database_sampler_stop.clear()
        self.database_sampler_threads = [
            threading.Thread(
                target=self._consume_database_samples,
                args=(query, sampler_index, sampler_index * 0.5),
                name=f"database-sampler-{self.lane.key}-{sampler_index + 1}",
                daemon=True,
            )
            for sampler_index in range(2)
        ]
        self.database_sampler_thread = self.database_sampler_threads[0]
        for thread in self.database_sampler_threads:
            thread.start()

    def _open_resource_sampler(self) -> subprocess.Popen[str]:
        if self._sampler_script is None or self._sampler_stderr is None:
            raise RuntimeError("resource sampler is not initialized")
        sampler = subprocess.Popen(
            remote_ssh_command(self.lane, "bash", "-s"),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._sampler_stderr,
            text=True, bufsize=1, start_new_session=True,
        )
        assert sampler.stdin is not None
        sampler.stdin.write(self._sampler_script)
        sampler.stdin.close()
        return sampler

    def _consume_database_samples(
        self, query: str, sampler_index: int = 0, initial_delay: float = 0.0,
    ) -> None:
        database_path = self.output / "database_samples.jsonl"
        diagnostic_path = self.output / (
            "database_sampler.stderr.log"
            if sampler_index == 0
            else f"database_sampler_{sampler_index + 1:02d}.stderr.log"
        )
        sample_error_path = self.output / (
            "database_sample_errors.jsonl"
            if sampler_index == 0
            else f"database_sample_errors_{sampler_index + 1:02d}.jsonl"
        )
        command = [
            str(MYSQL),
            f"--defaults-file={self.output / 'private/mysql.cnf'}",
            "--protocol=tcp",
            "--host=127.0.0.1",
            f"--port={self.lane.remote_port}",
            "--user=root",
            "--connect-timeout=3",
            "--ssl-mode=DISABLED",
            "--get-server-public-key",
            "--batch",
            "--raw",
            "--skip-column-names",
            "--unbuffered",
        ]
        expected_rows = 4 if self.lane.key == "xp" else 1
        counter_variables = {
            "job": ("Com_select",),
            "xp": ("Com_select", "Com_insert", "Com_update", "Com_delete"),
            "tpch": ("Com_select",),
            "tpcc": ("Com_insert", "Com_update", "Com_delete"),
        }[self.lane.key]
        quoted_variables = ",".join(
            "'" + variable + "'" for variable in counter_variables
        )
        persistent_query = (
            "SHOW GLOBAL STATUS "
            f"WHERE Variable_name IN ({quoted_variables}); "
            "SHOW SESSION STATUS LIKE 'Uptime';"
        )
        persistent: subprocess.Popen[str] | None = None
        if self.database_sampler_stop.wait(max(0.0, initial_delay)):
            return
        try:
            with database_path.open("a", encoding="utf-8") as database_file, \
                    diagnostic_path.open("a", encoding="utf-8") as diagnostic_file, \
                    sample_error_path.open("a", encoding="utf-8") as sample_error_file:
                next_tick = time.monotonic()
                while not self.database_sampler_stop.is_set():
                    if self.lane.key == "xp":
                        counter, errors = _run_database_counter_sample(
                            command, query, expected_rows
                        )
                    elif self.lane.key in {"job", "tpch", "tpcc"}:
                        errors: list[dict[str, Any]] = []
                        try:
                            if persistent is None or persistent.poll() is not None:
                                persistent = subprocess.Popen(
                                    command,
                                    stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL,
                                    text=True,
                                    bufsize=1,
                                    start_new_session=True,
                                )
                            assert persistent.stdin is not None
                            assert persistent.stdout is not None
                            persistent.stdin.write(persistent_query + "\n")
                            persistent.stdin.flush()
                            values: list[int] = []
                            sentinel_seen = False
                            read_deadline = (
                                time.monotonic()
                                + DATABASE_COUNTER_READ_TIMEOUT_SECONDS
                            )
                            while not sentinel_seen:
                                remaining = read_deadline - time.monotonic()
                                if remaining <= 0:
                                    raise TimeoutError(
                                        "persistent database counter query timed out"
                                    )
                                ready, _, _ = select.select(
                                    [persistent.stdout], [], [], remaining
                                )
                                if not ready:
                                    raise TimeoutError(
                                        "persistent database counter query timed out"
                                    )
                                raw_line = persistent.stdout.readline()
                                if raw_line == "":
                                    raise EOFError(
                                        "persistent database counter connection closed"
                                    )
                                line = raw_line.rstrip("\r\n")
                                pieces = line.split("\t")
                                if len(pieces) != 2:
                                    raise ValueError(
                                        f"unexpected counter row: {line!r}"
                                    )
                                if pieces[0] == "Uptime":
                                    sentinel_seen = True
                                else:
                                    values.append(int(pieces[1]))
                            if len(values) != len(counter_variables):
                                raise ValueError(
                                    "database counter row cardinality mismatch: "
                                    f"expected={len(counter_variables)} actual={len(values)}"
                                )
                            counter = sum(values)
                        except BaseException as sample_error:
                            errors.append({
                                "epoch_ms": int(time.time() * 1000),
                                "utc": datetime.now(timezone.utc).isoformat(),
                                "attempt": 1,
                                "sample_error": (
                                    f"{type(sample_error).__name__}: {sample_error}"
                                ),
                            })
                            counter = None
                            if persistent is not None:
                                persistent.kill()
                                persistent.wait(timeout=5)
                                persistent = None
                    else:
                        counter, errors = _run_database_counter_sample(
                            command, query, expected_rows
                        )
                    for error in errors:
                        error["sampler_index"] = sampler_index
                        sample_error_file.write(
                            json.dumps(error, sort_keys=True) + "\n"
                        )
                        sample_error_file.flush()
                        diagnostic_file.write(
                            f"{error['utc']} database counter sample_error: "
                            f"{error['sample_error']}\n"
                        )
                        diagnostic_file.flush()
                    attempt_epoch_ms = int(time.time() * 1000)
                    with self.rows_lock:
                        self.database_attempt_rows.append({
                            "epoch_ms": attempt_epoch_ms,
                            "sampler_index": sampler_index,
                            "succeeded": counter is not None,
                            "error_count": len(errors),
                        })
                    if counter is not None:
                        database = {
                            "epoch_ms": attempt_epoch_ms,
                            "sampler_index": sampler_index,
                            "completed_total": counter,
                            "source": "database_status_counter",
                            "attribution_scope": "dedicated_mysql_instance",
                            "digest_set_sha256": None,
                            "counter_name": self.lane.counter_name,
                            "sampler_mode": (
                                "persistent_mysql_session"
                                if self.lane.key in {"job", "tpch", "tpcc"}
                                else "independent_process_per_second"
                            ),
                        }
                        if not self._append_database_sample(database_file, database):
                            continue
                    next_tick += 1.0
                    if next_tick < time.monotonic() - 1.0:
                        next_tick = time.monotonic()
                    self.database_sampler_stop.wait(
                        max(0.0, next_tick - time.monotonic())
                    )
        except BaseException as error:
            self._database_sampler_error = f"{type(error).__name__}: {error}"
        finally:
            if persistent is not None and persistent.poll() is None:
                persistent.terminate()
                try:
                    persistent.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    persistent.kill()

    def _append_database_sample(
        self, database_file: Any, database: Mapping[str, Any],
    ) -> bool:
        """Merge staggered sampler results into one monotonic evidence stream."""
        with self.rows_lock:
            if self.database_rows:
                previous = self.database_rows[-1]
                if (
                    int(database["epoch_ms"]) <= int(previous["epoch_ms"])
                    or float(database["completed_total"])
                    < float(previous["completed_total"])
                ):
                    return False
            row = dict(database)
            database_file.write(json.dumps(row, sort_keys=True) + "\n")
            database_file.flush()
            self.database_rows.append(row)
            return True

    def _consume_samples(self) -> None:
        previous: tuple[int, int, int, int] | None = None
        raw_path = self.output / "sampler_raw.tsv"
        resource_path = self.output / "resource_samples.jsonl"
        try:
            with raw_path.open("a", encoding="utf-8") as raw, \
                    resource_path.open("a", encoding="utf-8") as resource_file:
                while not self.database_sampler_stop.is_set():
                    sampler = self.sampler
                    if sampler is None or sampler.stdout is None:
                        raise RuntimeError("resource sampler process is absent")
                    for line in sampler.stdout:
                        raw.write(line)
                        raw.flush()
                        pieces = line.rstrip("\n").split("\t")
                        if (
                            len(pieces) != 7
                            or pieces[0] != "R"
                            or not all(piece.isdigit() for piece in pieces[1:])
                        ):
                            continue
                        epoch, cpu_ns, usage, inactive, read_bytes, write_bytes = map(
                            int, pieces[1:]
                        )
                        if previous is not None:
                            old_epoch, old_cpu, old_read, old_write = previous
                            wall = (epoch - old_epoch) / 1000.0
                            if wall > 0 and cpu_ns >= old_cpu and read_bytes >= old_read and write_bytes >= old_write:
                                read_rate = (read_bytes - old_read) / wall
                                write_rate = (write_bytes - old_write) / wall
                                resource = {
                                    "epoch_ms": epoch,
                                    "cpu_utilization_ratio": (cpu_ns - old_cpu) / 1e9 / wall / self.lane.cpu_cores,
                                    "memory_utilization_ratio": max(0, usage - inactive) / self.lane.memory_bytes,
                                    "memory_current_diagnostic_ratio": usage / self.lane.memory_bytes,
                                    "io_utilization_ratio": (read_rate + write_rate) / (self.lane.read_bps + self.lane.write_bps),
                                    "io_read_bytes_per_second": read_rate,
                                    "io_write_bytes_per_second": write_rate,
                                    "io_definition": "(read_throughput+write_throughput)/(read_cap+write_cap)",
                                }
                                resource_file.write(json.dumps(resource, sort_keys=True) + "\n")
                                resource_file.flush()
                                with self.rows_lock:
                                    self.resource_rows.append(resource)
                        previous = (epoch, cpu_ns, read_bytes, write_bytes)
                    if self.database_sampler_stop.is_set():
                        break
                    self._sampler_reconnect_count += 1
                    self._sampler_stderr.write(
                        f"{utc_now()} resource sampler stream ended; "
                        f"reconnect={self._sampler_reconnect_count}\n"
                    )
                    self._sampler_stderr.flush()
                    if self.database_sampler_stop.wait(2.0):
                        break
                    try:
                        self.sampler = self._open_resource_sampler()
                    except BaseException as reconnect_error:
                        self._sampler_stderr.write(
                            f"{utc_now()} resource sampler reconnect failed: "
                            f"{type(reconnect_error).__name__}: {reconnect_error}\n"
                        )
                        self._sampler_stderr.flush()
                        if self.database_sampler_stop.wait(3.0):
                            break
        except BaseException as error:
            self._sampler_error = f"{type(error).__name__}: {error}"

    def resource_samples(self) -> Sequence[Mapping[str, Any]]:
        if self._sampler_error is not None:
            raise RuntimeError(f"resource sampler failed: {self._sampler_error}")
        if (
            self.sampler_thread is not None
            and not self.sampler_thread.is_alive()
            and not self.database_sampler_stop.is_set()
        ):
            raise RuntimeError(
                "resource sampler supervisor exited before the measurement "
                "session stopped; see sampler.stderr.log"
            )
        with self.rows_lock:
            return [dict(row) for row in self.resource_rows]

    def database_samples(self) -> Sequence[Mapping[str, Any]]:
        if self._database_sampler_error is not None:
            raise RuntimeError(
                f"database sampler failed: {self._database_sampler_error}"
            )
        if (
            self.database_sampler_threads
            and not any(thread.is_alive() for thread in self.database_sampler_threads)
            and not self.database_sampler_stop.is_set()
        ):
            raise RuntimeError("all database sampler threads exited unexpectedly")
        with self.rows_lock:
            return [dict(row) for row in self.database_rows]

    def database_counter_coverage_gate(
        self, measurement: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Fail closed when database-counter sampling loses too much coverage."""
        selected = (
            measurement.get("rolling_measurement_control", {})
            .get("selected_observation", {})
        )
        throughput = selected.get("database_throughput", {})
        try:
            start_ms = int(throughput["requested_start_epoch_ms"])
            end_ms = int(throughput["requested_end_epoch_ms"])
        except (KeyError, TypeError, ValueError):
            return {
                "passed": False,
                "reason": "selected_database_counter_window_is_absent",
            }
        if end_ms <= start_ms:
            return {
                "passed": False,
                "reason": "selected_database_counter_window_is_not_positive",
            }
        expected_per_sampler = max(1, math.ceil((end_ms - start_ms) / 1000.0))
        minimum_coverage = 0.90
        maximum_gap_ms = 10000
        with self.rows_lock:
            attempts = [
                dict(row) for row in self.database_attempt_rows
                if start_ms <= int(row.get("epoch_ms", -1)) <= end_ms
            ]
        sampler_results: dict[str, Any] = {}
        passed = True
        for sampler_index in range(2):
            rows = [
                row for row in attempts
                if row.get("sampler_index") == sampler_index
            ]
            successes = sorted(
                int(row["epoch_ms"]) for row in rows if row.get("succeeded") is True
            )
            success_count = len(successes)
            failure_count = sum(
                1 for row in rows if row.get("succeeded") is not True
            )
            coverage = min(1.0, success_count / expected_per_sampler)
            boundaries = [start_ms, *successes, end_ms]
            maximum_observed_gap_ms = max(
                right - left for left, right in zip(boundaries, boundaries[1:])
            )
            sampler_passed = (
                coverage >= minimum_coverage
                and maximum_observed_gap_ms <= maximum_gap_ms
            )
            passed = passed and sampler_passed
            sampler_results[str(sampler_index)] = {
                "success_count": success_count,
                "failure_count": failure_count,
                "expected_sample_count": expected_per_sampler,
                "coverage": coverage,
                "maximum_observed_gap_ms": maximum_observed_gap_ms,
                "passed": sampler_passed,
            }
        return {
            "passed": passed,
            "reason": None if passed else "database_counter_sampling_coverage_failed",
            "minimum_coverage": minimum_coverage,
            "maximum_gap_ms": maximum_gap_ms,
            "requested_start_epoch_ms": start_ms,
            "requested_end_epoch_ms": end_ms,
            "samplers": sampler_results,
        }

    def hybench_session(self, workload: str, protocol: Mapping[str, Any]) -> FormalHyBenchContinuousSession:
        runtime = self.output / "hybench_runtime"
        (runtime / "conf").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HYBENCH_ROOT / "conf/log4j2.properties", runtime / "conf/log4j2.properties")
        statements = runtime / "conf/stmt_mysql_formal_case_normalized.toml"
        shutil.copyfile(STATEMENTS, statements)
        data_link = runtime / "Data_10x"
        data_link.symlink_to(HYBENCH_ROOT / "Data_10x", target_is_directory=True)
        config = self.output / "private/formal_hybench.properties"
        config.parent.mkdir(parents=True, exist_ok=True)
        is_xp = workload == "xp"
        jdbc = (
            f"jdbc:mysql://127.0.0.1:{self.lane.remote_port}/{self.lane.database}"
            "?useUnicode=true&characterEncoding=utf-8&useSSL=false&allowPublicKeyRetrieval=true"
            "&useServerPrepStmts=true&cachePrepStmts=true&useCursorFetch=true&defaultFetchSize=1000"
        )
        driver = "com.mysql.cj.jdbc.Driver"
        if is_xp:
            jdbc = jdbc.replace("jdbc:mysql:", "jdbc:formal-mysql:", 1)
            driver = "formal.mysql.AttributingMysqlDriver"
        values = {
            "db": "mysql", "classname": driver, "username": "root", "password": self.password,
            "url": jdbc, "url_ap": jdbc, "classname_ap": driver,
            "username_ap": "root", "password_ap": self.password, "sf": "10x",
            "at1_percent": 40, "at2_percent": 30, "at3_percent": 15,
            "at4_percent": 5, "at5_percent": 7, "at6_percent": 3,
            "apclient": 0 if is_xp else 4, "tpclient": 0,
            "xapclient": 8 if is_xp else 0, "xtpclient": 8 if is_xp else 0,
            "risk_rate": 0.1, "fresh_interval": 20, "contention_num": 10000,
            "apRunSecs": self.measurement_seconds + 60,
            "tpRunSecs": self.measurement_seconds + 60,
            "xpRunSecs": self.measurement_seconds + 60,
            "progress_windows": self.measurement_seconds + 60,
            "random_seed": 4508333,
            "formal_warmup_seconds": 60,
            "formal_maximum_measurement_seconds": self.measurement_seconds,
            "formal_graceful_quiescence_seconds": 120,
            "formal_stop_file": str((self.output / "formal_hybench.stop").resolve()),
            # AP and XP must both use the frozen AP13/Q10-weighted mix.  A
            # Q10-only calibration is not representative of the formal
            # workload and, in particular, can incorrectly report zero I/O
            # after the first buffer-pool warm-up.
            "ap_query_ids": "1,2,3,4,5,6,7,8,9,10,11,12,13",
            "ap_query_weights": "6,6,6,6,6,6,6,6,6,28,6,6,6",
            "dist": "zipfian", "power_alpha": 0.1, "apround": 1,
        }
        config.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
        config.chmod(0o600)
        classpath = [FORMAL_CLASSES, CONNECTOR, HYBENCH_JAR]
        if is_xp:
            classpath = [
                XP_V7_CLASSES,
                ATTRIBUTION_CLASSES,
                CONNECTOR,
                XP_V7_JAR,
            ]

        def command(_stop: Path) -> Sequence[str]:
            return [str(JAVA), "-cp", os.pathsep.join(map(str, classpath)),
                    "com.hybench.HyBenchFormalMain", workload, str(config), str(statements)]

        def drain_workload_queries() -> None:
            audit = self.output / "hybench_drain_kill_queries.jsonl"
            selector = (
                "SELECT ID FROM information_schema.PROCESSLIST "
                "WHERE ID <> CONNECTION_ID() AND USER='root' "
                "AND COMMAND='Query' AND INFO IS NOT NULL "
                "AND INFO NOT LIKE 'SHOW GLOBAL STATUS%' "
                "AND INFO NOT LIKE 'SELECT ID FROM information_schema.PROCESSLIST%'"
            )
            base = [
                str(MYSQL),
                f"--defaults-file={self.output / 'private/mysql.cnf'}",
                "--protocol=tcp", "--host=127.0.0.1",
                f"--port={self.lane.remote_port}", "--user=root",
                "--batch", "--raw", "--skip-column-names",
            ]
            quiet = 0
            with audit.open("a", encoding="utf-8") as handle:
                for attempt in range(1, 21):
                    ids, missing = _run_processlist_sample(base, selector)
                    record = {
                        "epoch_ms": int(time.time() * 1000),
                        "attempt": attempt,
                        "active_workload_query_ids": ids,
                    }
                    if missing is not None:
                        record.update(missing)
                        handle.write(json.dumps(record, sort_keys=True) + "\n")
                        handle.flush()
                        quiet = 0
                        time.sleep(0.5)
                        continue
                    handle.write(json.dumps(record, sort_keys=True) + "\n")
                    handle.flush()
                    for connection_id in ids:
                        subprocess.run(
                            [*base, "-e", f"KILL QUERY {connection_id}"],
                            text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False, timeout=10,
                        )
                    quiet = quiet + 1 if not ids else 0
                    if quiet >= 2:
                        return
                    time.sleep(0.5)
            raise RuntimeError("HyBench workload queries did not drain after KILL QUERY")

        return FormalHyBenchContinuousSession(
            output=self.output, workload=workload, command_builder=command,
            resource_samples=self.resource_samples, database_qps_samples=self.database_samples,
            measurement_protocol=protocol, cwd=runtime,
            drain_workload_queries=drain_workload_queries,
        )

    def job_session(self, protocol: Mapping[str, Any]) -> FormalJobContinuousSession:
        def command(stop: Path) -> Sequence[str]:
            return [
                "/home/anonymous/miniconda3/envs/py313/bin/python",
                str(ROOT / "shell/formal_job_multiquery_workload.py"),
                "--host", "127.0.0.1", "--port", str(self.lane.remote_port),
                "--database", self.lane.database, "--query-dir", str(JOB_QUERY_DIR),
                "--defaults-extra-file", str(self.output / "private/mysql.cnf"),
                "--event-path", str(self.output / "completion_events.jsonl"),
                "--stop-file", str(stop), "--concurrency", "1",
                "--weights", "10,10,10,10,10,10,30,10,7.5",
                "--seed", "4508333",
                "--maximum-measurement-seconds", str(self.measurement_seconds),
            ]
        return FormalJobContinuousSession(
            output=self.output, command_builder=command,
            resource_samples=self.resource_samples, database_qps_samples=self.database_samples,
            measurement_protocol=protocol, cwd=ROOT,
            required_query_names=tuple(
                path.name for path in sorted(JOB_QUERY_DIR.glob("*.sql"))
            ),
        )

    def tpch_session(
        self,
        protocol: Mapping[str, Any],
        *,
        query_weights: str | None = None,
        terminals: int = 2,
    ) -> FormalBenchBaseContinuousSession:
        source = BENCHBASE_ROOT / "config/mysql/sample_tpch_config.xml"
        target = self.output / "private/tpch_660.xml"
        target.parent.mkdir(parents=True, exist_ok=True)
        tree = ET.parse(source)
        root = tree.getroot()
        values = {
            "url": (
                f"jdbc:mysql://127.0.0.1:{self.lane.remote_port}/{self.lane.database}"
                "?useUnicode=true&characterEncoding=utf-8&useSSL=false&allowPublicKeyRetrieval=true"
                "&useServerPrepStmts=true&cachePrepStmts=true&useCursorFetch=true&defaultFetchSize=1000"
            ),
            "username": "root", "password": self.password, "isolation": "TRANSACTION_READ_COMMITTED",
            "scalefactor": "10", "terminals": str(terminals), "randomSeed": "4508333",
        }
        for tag, value in values.items():
            node = root.find(tag)
            if node is None:
                node = ET.SubElement(root, tag)
            node.text = value
        works = root.find("works")
        if works is None or not works.findall("work"):
            raise RuntimeError("TPC-H configuration lacks works/work")
        work = works.findall("work")[0]
        for extra in works.findall("work")[1:]:
            works.remove(extra)
        for tag, value in {
            "serial": "false", "rate": "unlimited",
            "time": str(self.measurement_seconds + 60), "warmup": "60",
            "weights": query_weights or "4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,16,4,4,4,4",
        }.items():
            node = work.find(tag)
            if node is None:
                node = ET.SubElement(work, tag)
            node.text = value
        tree.write(target, encoding="utf-8", xml_declaration=True)
        target.chmod(0o600)
        result_root = self.output / "benchbase_results"
        classpath = os.pathsep.join((str(BENCHBASE_JAR), str(BENCHBASE_ROOT / "lib/*")))

        def command(_stop: Path) -> Sequence[str]:
            return [
                str(JAVA), f"-javaagent:{TPCH_AGENT}", "-cp", classpath,
                "com.oltpbenchmark.DBWorkload", "-b", "tpch", "-c", str(target),
                "--create=false", "--load=false", "--execute=true", "-d", str(result_root),
            ]

        def correctness() -> Mapping[str, Any]:
            sql = """SELECT
  c_name,c_custkey,o_orderkey,o_orderdate,o_totalprice,SUM(l_quantity)
FROM customer,orders,lineitem
WHERE o_orderkey IN (
  SELECT l_orderkey FROM lineitem
  GROUP BY l_orderkey HAVING SUM(l_quantity) > 314
)
AND c_custkey=o_custkey AND o_orderkey=l_orderkey
GROUP BY c_name,c_custkey,o_orderkey,o_orderdate,o_totalprice
ORDER BY o_totalprice DESC,o_orderdate LIMIT 100
"""
            completed = run([
                MYSQL,
                f"--defaults-file={self.output / 'private/mysql.cnf'}",
                "--protocol=tcp", "--host=127.0.0.1",
                f"--port={self.lane.remote_port}", "--user=root",
                "--batch", "--raw", "--skip-column-names",
                self.lane.database, "-e", sql,
            ], timeout=600)
            normalized = completed.stdout.replace("\r\n", "\n")
            if normalized and not normalized.endswith("\n"):
                normalized += "\n"
            evidence_path = self.output / "tpch_q18_correctness.tsv"
            evidence_path.write_text(normalized, encoding="utf-8")
            return {
                "query_id": "Q18",
                "parameter_int": 314,
                "row_count": len(normalized.splitlines()),
                "sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                "seed_agent": 4508333,
                "result_path": str(evidence_path),
            }

        return FormalBenchBaseContinuousSession(
            output=self.output, benchmark="tpch", command_builder=command,
            resource_samples=self.resource_samples, database_qps_samples=self.database_samples,
            measurement_protocol=protocol, tpch_correctness=correctness, cwd=BENCHBASE_ROOT,
        )

    def tpcc_session(self, protocol: Mapping[str, Any]) -> FormalBenchBaseContinuousSession:
        source = BENCHBASE_ROOT / "config/mysql/sample_tpcc_config.xml"
        target = self.output / "private/tpcc_660.xml"
        target.parent.mkdir(parents=True, exist_ok=True)
        tree = ET.parse(source)
        root = tree.getroot()
        values = {
            "url": (
                f"jdbc:mysql://127.0.0.1:{self.lane.remote_port}/{self.lane.database}"
                "?useUnicode=true&characterEncoding=utf-8&useSSL=false"
                "&allowPublicKeyRetrieval=true&rewriteBatchedStatements=true"
            ),
            "username": "root",
            "password": self.password,
            # Keep the production run aligned with the frozen TPC-C template.
            # SERIALIZABLE creates lock-wait bursts at 32 terminals / 143
            # warehouses and invalidates the intended CPU-bottleneck profile.
            "isolation": "TRANSACTION_READ_COMMITTED",
            "scalefactor": "143",
            "terminals": "32",
            "randomSeed": "4508333",
        }
        for tag, value in values.items():
            node = root.find(tag)
            if node is None:
                node = ET.SubElement(root, tag)
            node.text = value
        works = root.find("works")
        if works is None or not works.findall("work"):
            raise RuntimeError("TPC-C configuration lacks works/work")
        work = works.findall("work")[0]
        for extra in works.findall("work")[1:]:
            works.remove(extra)
        for tag, value in {
            "serial": "false",
            "rate": "unlimited",
            "time": str(self.measurement_seconds + 60),
            "warmup": "60",
            "weights": "45,43,4,4,4",
        }.items():
            node = work.find(tag)
            if node is None:
                node = ET.SubElement(work, tag)
            node.text = value
        tree.write(target, encoding="utf-8", xml_declaration=True)
        target.chmod(0o600)
        result_root = self.output / "benchbase_results"
        classpath = os.pathsep.join((str(BENCHBASE_JAR), str(BENCHBASE_ROOT / "lib/*")))

        def command(_stop: Path) -> Sequence[str]:
            java_command = [
                str(JAVA), "-cp", classpath, "com.oltpbenchmark.DBWorkload",
                "-b", "tpcc", "-c", str(target), "--create=false", "--load=false",
                "--execute=true", "-d", str(result_root),
            ]
            return [
                sys.executable,
                str(ROOT / "shell/formal_tpcc_composite_workload.py"),
                "--defaults-extra-file", str(self.output / "private/mysql.cnf"),
                "--host", "127.0.0.1",
                "--port", str(self.lane.remote_port),
                "--database", self.lane.database,
                "--sensitive-sql", str(TPCC_SENSITIVE_SQL),
                "--event-path", str(self.output / "tpcc_sensitive_events.jsonl"),
                "--interval-seconds", "15",
                "--",
                *java_command,
            ]

        scale_factor = 143
        fixed_counts = {
            "warehouse": scale_factor,
            "district": scale_factor * 10,
            "customer": scale_factor * 30000,
            "item": 100000,
            "stock": scale_factor * 100000,
        }
        invariant_sql = {
            **{
                name: f"SELECT COUNT(*) FROM {name}"
                for name in fixed_counts
            },
            "orphan_new_order": (
                "SELECT COUNT(*) FROM new_order n LEFT JOIN oorder o "
                "ON o.o_w_id=n.no_w_id AND o.o_d_id=n.no_d_id "
                "AND o.o_id=n.no_o_id WHERE o.o_id IS NULL"
            ),
            "orphan_order_line": (
                "SELECT COUNT(*) FROM order_line l LEFT JOIN oorder o "
                "ON o.o_w_id=l.ol_w_id AND o.o_d_id=l.ol_d_id "
                "AND o.o_id=l.ol_o_id WHERE o.o_id IS NULL"
            ),
            "invalid_next_order": (
                "SELECT COUNT(*) FROM district d JOIN "
                "(SELECT o_w_id,o_d_id,MAX(o_id) max_o_id FROM oorder "
                "GROUP BY o_w_id,o_d_id) o "
                "ON o.o_w_id=d.d_w_id AND o.o_d_id=d.d_id "
                "WHERE d.d_next_o_id<=o.max_o_id"
            ),
        }
        invariant_contract = {
            "schema_version": "mysql_tpcc_frozen_invariant_v2",
            "scale_factor": scale_factor,
            "fixed_counts": fixed_counts,
            "invariant_sql": invariant_sql,
        }
        invariant_id = hashlib.sha256(
            json.dumps(
                invariant_contract, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()

        def invariant(phase: str) -> Mapping[str, Any]:
            observed: dict[str, int] = {}
            for name, sql in invariant_sql.items():
                command = [
                    MYSQL,
                    f"--defaults-file={self.output / 'private/mysql.cnf'}",
                    "--protocol=tcp", "--host=127.0.0.1",
                    f"--port={self.lane.remote_port}", "--user=root",
                    "--connect-timeout=10", "--ssl-mode=DISABLED",
                    "--get-server-public-key",
                    "--batch", "--raw", "--skip-column-names",
                    self.lane.database, "-e", sql,
                ]
                last_error: BaseException | None = None
                completed = None
                for attempt in range(1, 4):
                    try:
                        completed = run(command, timeout=600)
                        break
                    except BaseException as exc:
                        last_error = exc
                        if attempt < 3:
                            time.sleep(2.0)
                if completed is None:
                    raise RuntimeError(
                        f"TPC-C invariant query {name!r} failed after 3 attempts: "
                        f"{type(last_error).__name__}: {last_error}"
                    ) from last_error
                observed[name] = int(completed.stdout.strip())
            fixed_ok = all(
                observed.get(name) == expected
                for name, expected in fixed_counts.items()
            )
            relational_ok = all(
                observed.get(name) == 0
                for name in (
                    "orphan_new_order",
                    "orphan_order_line",
                    "invalid_next_order",
                )
            )
            return {
                "schema_version": invariant_contract["schema_version"],
                "scale_factor": scale_factor,
                "frozen_invariant_id": invariant_id,
                "phase": phase,
                "fixed_counts": fixed_counts,
                "observed": observed,
                "passed": fixed_ok and relational_ok,
            }

        return FormalBenchBaseContinuousSession(
            output=self.output,
            benchmark="tpcc",
            command_builder=command,
            resource_samples=self.resource_samples,
            database_qps_samples=self.database_samples,
            measurement_protocol=protocol,
            tpcc_invariant=invariant,
            expected_tpcc_invariant_id=invariant_id,
            cwd=BENCHBASE_ROOT,
        )

    def make_session(self) -> Any:
        protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
        protocol["timing"]["maximum_measurement_seconds"] = self.measurement_seconds
        protocol["stability_decision_resources"] = [self.lane.target_resource]
        protocol["config_id"] = (
            f"{protocol['config_id']}_max_{self.measurement_seconds}s"
        )
        if self.lane.key == "ap":
            return self.hybench_session("ap", protocol)
        if self.lane.key == "xp":
            return self.hybench_session("xp", protocol)
        if self.lane.key == "job":
            return self.job_session(protocol)
        if self.lane.key == "tpch":
            return self.tpch_session(protocol)
        return self.tpcc_session(protocol)

    def marker(self) -> str:
        return {
            "ap": "HYBENCH_FORMAL_SESSION_JSON=",
            "xp": "HYBENCH_FORMAL_SESSION_JSON=",
            "job": "FORMAL_SQL_SESSION_JSON=",
            "tpch": "BENCHBASE_FORMAL_SESSION_JSON=",
            "tpcc": "BENCHBASE_FORMAL_SESSION_JSON=",
        }[self.lane.key]

    def wait_initial_marker(self) -> None:
        stdout = self.output / "workload.stdout.log"
        # TPC-H JVM initialization plus the mandatory 60-second warm-up can
        # exceed two minutes on the memory-constrained lane.  This is only a
        # start barrier; the measured interval remains unchanged.
        marker_timeout = 300 if self.lane.key in {"tpch", "tpcc"} else 120
        deadline = time.monotonic() + marker_timeout
        while time.monotonic() < deadline:
            process = getattr(self.session, "process", None)
            if process is not None and process.poll() is not None:
                stderr = (self.output / "workload.stderr.log").read_text(encoding="utf-8")
                raise RuntimeError(
                    f"workload exited before initial marker: rc={process.returncode}; {stderr[-2000:]}"
                )
            text = stdout.read_text(encoding="utf-8") if stdout.is_file() else ""
            marker_ready = text.count(self.marker()) == 1
            if self.lane.key in {"tpch", "tpcc"}:
                # BenchBase emits the formal session marker immediately, but
                # the measured R0 interval may only start after its warm-up
                # counters have been reset.  Keep the start barrier closed
                # until both singleton markers are durable in the audit log.
                marker_ready = (
                    marker_ready
                    and text.count("BENCHBASE_FORMAL_WARMUP_RESET=") == 1
                )
            if marker_ready:
                return
            time.sleep(0.05)
        raise RuntimeError("initial workload session marker timed out")

    def wait_live(self, result: dict[str, Any]) -> None:
        stdout = self.output / "workload.stdout.log"
        # TPC-C performs the frozen pre-run relational invariant before
        # starting BenchBase.  Under the deliberately tight CPU envelope that
        # invariant can consume several minutes, followed by the mandatory
        # 60-second warm-up.  The live gate must not time out while the
        # independently bounded (600s/query) correctness check is still
        # making progress.
        deadline = time.monotonic() + (900 if self.lane.key == "tpcc" else 180)
        while time.monotonic() < deadline:
            if result.get("error"):
                raise RuntimeError(str(result["error"]))
            process = getattr(self.session, "process", None)
            if process is not None and process.poll() is not None:
                stderr = (self.output / "workload.stderr.log").read_text(encoding="utf-8")
                raise RuntimeError(f"workload exited before R0 live gate: rc={process.returncode}; {stderr[-2000:]}")
            text = stdout.read_text(encoding="utf-8") if stdout.is_file() else ""
            with self.rows_lock:
                sample_count = len(self.resource_rows)
                database_count = len(self.database_rows)
            marker_ready = text.count(self.marker()) == 1
            if self.lane.key in {"tpch", "tpcc"}:
                marker_ready = (
                    marker_ready
                    and text.count("BENCHBASE_FORMAL_WARMUP_RESET=") == 1
                )
            if marker_ready and sample_count >= 3 and database_count >= 4:
                return
            time.sleep(0.25)
        raise RuntimeError("R0 workload marker/sample live gate timed out")


    def _capture_mysql(self, query: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
        return run([
            MYSQL,
            f"--defaults-file={self.output / 'private/mysql.cnf'}",
            "--protocol=tcp", "--host=127.0.0.1",
            f"--port={self.lane.remote_port}", "--user=root",
            "--batch", "--raw", "--skip-column-names", self.lane.database,
            "-e", query,
        ], timeout=timeout)

    def enable_capture_general_log(self) -> None:
        if not self.capture_general_log:
            return
        self._capture_mysql(
            "SET GLOBAL log_output='FILE'; "
            f"SET GLOBAL general_log_file='{self.capture_remote_log}'; "
            "SET GLOBAL general_log=ON"
        )
        self.capture_log_enabled = True
        atomic_json(self.output / "capture_general_log_gate.json", {
            "schema_version": "tpch_general_log_capture_gate_v1",
            "status": "GENERAL_LOG_ENABLED",
            "benchmark": "tpch",
            "host": self.lane.host,
            "hostname": self.lane.hostname,
            "remote_log": self.capture_remote_log,
            "capture_seconds": self.measurement_seconds,
            "client_normal_exit_required": True,
            "q1_q22_complete_required": True,
            "stability_required": False,
        })

    def export_capture_general_log(self, *, require_complete: bool) -> None:
        if not self.capture_general_log or self.capture_log_exported:
            return
        remote_copy = f"{self.remote_root}/production_general.log"
        # Keep the container alive while both disabling the log and copying it.
        # The local tunnel may already be unhealthy on an exception path.
        remote_export = f"""set +e
running=$(docker inspect --format '{{{{.State.Running}}}}' {self.container} 2>/dev/null)
test "$running" = true || exit 91
docker exec {self.container} mysql --defaults-file=/run/ra-credentials/selected.cnf -NBe \
  'SET GLOBAL general_log=OFF'
off_rc=$?
docker cp {self.container}:{self.capture_remote_log} {remote_copy}
copy_rc=$?
test $copy_rc -eq 0
test -s {remote_copy}
printf 'REMOTE_GENERAL_LOG_OFF_RC=%s\n' "$off_rc"
"""
        remote_result = self.remote(remote_export, timeout=180)
        self.capture_log_enabled = False
        local_copy = self.output / "production_general.log"
        run([
            "scp", *ssh_transport_options(self.lane), "-o", "BatchMode=yes",
            f"{self.lane.host}:{remote_copy}", str(local_copy),
        ], timeout=180)
        if self.binding_prefix_log is not None:
            combined = local_copy.with_name(
                f".{local_copy.name}.combined.{os.getpid()}"
            )
            with combined.open("wb") as destination:
                with self.binding_prefix_log.open("rb") as prefix:
                    shutil.copyfileobj(prefix, destination)
                with local_copy.open("rb") as suffix:
                    shutil.copyfileobj(suffix, destination)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(combined, local_copy)
        metadata = {
            "schema_version": "tpch_general_log_capture_v1",
            "status": "RAW_GENERAL_LOG_CAPTURED",
            "remote_log": self.capture_remote_log,
            "local_log": str(local_copy),
            "sha256": sha256(local_copy),
            "client_normal_exit_required": True,
            "q1_q22_complete_required": True,
            "stability_required": False,
            "remote_general_log_off": remote_result.stdout.strip(),
            "binding_resume": {
                "start_query": self.binding_start_query,
                "prefix_log": (
                    str(self.binding_prefix_log)
                    if self.binding_prefix_log is not None
                    else None
                ),
            },
        }
        if require_complete:
            binding_dir = self.output / "production_bindings"
            extraction = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "shell/extract_tpch_production_bindings.py"),
                    str(local_copy), str(binding_dir),
                ],
                text=True, capture_output=True, timeout=600, check=False,
            )
            (self.output / "binding_extraction.stdout.log").write_text(
                extraction.stdout, encoding="utf-8"
            )
            (self.output / "binding_extraction.stderr.log").write_text(
                extraction.stderr, encoding="utf-8"
            )
            if extraction.returncode != 0:
                raise RuntimeError(
                    "TPC-H Q1-Q22 binding coverage incomplete: "
                    + extraction.stdout.strip()
                )
            manifest = json.loads(
                (binding_dir / "binding_manifest.json").read_text(encoding="utf-8")
            )
            if manifest.get("coverage_complete") is not True:
                raise RuntimeError("TPC-H binding manifest is not complete")
            metadata["binding_manifest"] = str(binding_dir / "binding_manifest.json")
            metadata["covered_queries"] = manifest.get("covered_queries", [])
        atomic_json(self.output / "production_general_log.json", metadata)
        self.capture_log_exported = True

    def execute_capture_general_log(self) -> None:
        if not self.capture_general_log:
            raise RuntimeError("capture mode was not requested")
        self.output.mkdir(parents=True, exist_ok=False)
        self.state("PREPARING_TPCH_GENERAL_LOG_CAPTURE")
        self.prepare_remote()
        self.state("STARTING_TUNNEL_AND_SAMPLER")
        self.start_tunnel()
        self.apply_candidate_costs()
        self.start_sampler()
        self.session = self.make_session()
        self.enable_capture_general_log()
        self.state(
            "TPCH_CAPTURE_RUNNING",
            capture_general_log=True,
            capture_seconds=self.measurement_seconds,
            stability_required=False,
        )
        self.session.start(
            warmup_seconds=60,
            maximum_measurement_seconds=self.measurement_seconds,
        )
        self.wait_initial_marker()
        self.state(
            "TPCH_CAPTURE_DRAINING",
            capture_general_log=True,
            capture_seconds=self.measurement_seconds,
            graceful_drain_seconds=600,
            stop_request_at_measurement_end=True,
            stability_required=False,
        )
        result = self.session.stop_after_measurement(
            self.measurement_seconds, graceful_drain_seconds=600
        )
        self.capture_session_succeeded = True
        atomic_json(self.output / "capture_session_result.json", result)
        self.export_capture_general_log(require_complete=True)
        self.state(
            "TPCH_CAPTURE_COMPLETE",
            capture_general_log=True,
            capture_seconds=self.measurement_seconds,
            client_normal_exit=True,
            q1_q22_complete=True,
            stability_required=False,
        )

    def _capture_one_tpch_binding(self, query: int) -> Mapping[str, Any]:
        """Submit one production TPCH transaction and preserve its exact SQL.

        This is a binding-only capture, not a performance measurement.  A
        MySQL general-log Query/Execute record is created when SQL is
        submitted, so requiring an expensive query such as Q9 to complete
        wastes tens of minutes without strengthening binding provenance.
        The final extractor remains fail-closed on complete Q1--Q22 coverage.
        """
        weights = ["0"] * 22
        weights[query - 1] = "1"
        protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
        query_timeout_seconds = 600
        protocol["timing"]["maximum_measurement_seconds"] = query_timeout_seconds
        protocol["config_id"] = f"{protocol['config_id']}_binding_q{query}"
        session = self.tpch_session(
            protocol,
            query_weights=",".join(weights),
            terminals=1,
        )
        self.session = session
        session.start(
            warmup_seconds=60,
            maximum_measurement_seconds=query_timeout_seconds,
        )
        self.wait_initial_marker()
        # Q15 is a three-statement transaction (CREATE/SELECT/DROP), so allow
        # it to complete.  For every other query, 90 seconds after the formal
        # measurement marker is ample time for the statement to be submitted
        # and recorded in the server general log.
        submission_wait_seconds = 600 if query == 15 else 90
        deadline = time.monotonic() + submission_wait_seconds
        completed_before_stop = False
        while True:
            text = (self.output / "workload.stdout.log").read_text(encoding="utf-8")
            samples = [
                json.loads(raw)
                for raw in re.findall(r"BENCHBASE_FORMAL_SAMPLE_JSON=(\{[^\n]+\})", text)
            ]
            progress = [
                row for row in samples
                if row.get("measurement_phase") is True
                and int(row.get("completed_total", 0)) >= 1
            ]
            if progress:
                completed_before_stop = True
                break
            if session.process.poll() is not None:
                if session.process.returncode != 0:
                    raise RuntimeError(
                        f"Q{query} BenchBase exited before submission window "
                        f"rc={session.process.returncode}"
                    )
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        process = session.process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)
        session._close()
        self.session = None
        session.stop_file.unlink(missing_ok=True)
        return {
            "query": f"Q{query}",
            "completed_before_stop": completed_before_stop,
            "submission_wait_seconds": submission_wait_seconds,
            "binding_capture_semantics": (
                "general_log_submission_evidence_final_extraction_fail_closed"
            ),
        }

    def execute_tpch_binding_capture(self) -> None:
        if not self.capture_tpch_bindings:
            raise RuntimeError("TPC-H binding capture was not requested")
        self.output.mkdir(parents=True, exist_ok=False)
        self.state("PREPARING_TPCH_BINDING_CAPTURE")
        self.prepare_remote()
        self.state("STARTING_TUNNEL_AND_SAMPLER")
        self.start_tunnel()
        self.apply_candidate_costs()
        self.start_sampler()
        self.enable_capture_general_log()
        completed_queries = [
            f"Q{query}" for query in range(1, self.binding_start_query)
        ]
        for query in range(self.binding_start_query, 23):
            self.state(
                "TPCH_BINDING_RUNNING",
                binding_query=f"Q{query}",
                completed_queries=completed_queries,
                capture_seconds=self.measurement_seconds,
                stability_required=False,
            )
            result = self._capture_one_tpch_binding(query)
            completed_queries.append(str(result["query"]))
            atomic_json(
                self.output / "tpch_binding_progress.json",
                {"completed_queries": completed_queries, "last_result": result},
            )
        self.capture_session_succeeded = True
        self.export_capture_general_log(require_complete=True)
        self.state(
            "TPCH_BINDING_CAPTURE_COMPLETE",
            capture_general_log=True,
            capture_tpch_bindings=True,
            capture_seconds=self.measurement_seconds,
            completed_queries=completed_queries,
            q1_q22_complete=True,
            client_normal_exit=True,
            stability_required=False,
        )

    def execute(self) -> None:
        self.output.mkdir(parents=True, exist_ok=False)
        self.state("PREPARING_FRESH_R0")
        self.prepare_remote()
        self.state("STARTING_TUNNEL_AND_SAMPLER")
        self.start_tunnel()
        self.apply_candidate_costs()
        self.start_sampler()
        self.session = self.make_session()
        result: dict[str, Any] = {}

        def drive() -> None:
            try:
                campaign = self

                class StartBarrier:
                    def start(self, *, warmup_seconds: int, maximum_measurement_seconds: int) -> None:
                        campaign.session.start(
                            warmup_seconds=warmup_seconds,
                            maximum_measurement_seconds=maximum_measurement_seconds,
                        )
                        campaign.wait_initial_marker()

                    def __getattr__(self, name: str) -> Any:
                        return getattr(campaign.session, name)

                result["measurement"] = _run_continuous_session(
                    StartBarrier(),
                    maximum_measurement_seconds=self.measurement_seconds,
                    minimum_selection_seconds=self.minimum_measurement_seconds,
                    minimum_client_completions=self.minimum_client_completions,
                )
            except BaseException as error:
                result["error"] = f"{type(error).__name__}: {error}"

        driver = threading.Thread(target=drive, name=f"r0-{self.lane.key}")
        driver.start()
        self.wait_live(result)
        process = getattr(self.session, "process", None)
        self.state(
            "R0_RUNNING", r0_started=True, workload_pid=getattr(process, "pid", None),
            workload_marker=self.marker().rstrip("="), algorithm_process_live=True,
        )
        driver.join()
        if result.get("error"):
            raise RuntimeError(str(result["error"]))
        measurement = dict(result["measurement"])
        profile_gate = evaluate_r0_profile(self.lane, measurement)
        database_counter_gate = self.database_counter_coverage_gate(measurement)
        measurement["r0_profile_gate"] = profile_gate
        measurement["database_counter_coverage_gate"] = database_counter_gate
        atomic_json(self.output / "r0_result.json", measurement)
        stability = (
            measurement.get("rolling_measurement_control", {})
            .get("selected_observation", {})
            .get("stability", {})
        )
        self.r0_gate_valid = (
            stability.get("stable") is True
            and database_counter_gate.get("passed") is True
            and (
                profile_gate.get("passed") is True
                or not self.require_r0_profile_gate
            )
        )
        if not self.r0_gate_valid:
            profile_failure = (
                self.require_r0_profile_gate
                and profile_gate.get("passed") is not True
            )
            database_counter_failure = (
                database_counter_gate.get("passed") is not True
            )
            failure_detail = (
                f"R0 profile gate failed: {profile_gate}; "
                if profile_failure
                else (
                    f"database counter coverage gate failed: "
                    f"{database_counter_gate}; "
                    if database_counter_failure
                    else (
                        "R0 stability gate failed: "
                        "selected_observation.stability.stable must be true, "
                        f"got {stability.get('stable')!r}; "
                    )
                )
            )
            message = failure_detail + "Algorithm C and XAP are unauthorized"
            self.state(
                "INVALID_R0_PROFILE" if profile_failure else "INVALID_R0_UNSTABLE",
                r0_started=True,
                r0_complete=False,
                r0_gate_valid=False,
                r0_profile_gate=profile_gate,
                database_counter_coverage_gate=database_counter_gate,
                rolling_dynamic_gate="FAIL",
                algorithm_c_authorized=False,
                xap_authorized=False,
                algorithm_process_live=False,
                error=message,
            )
            raise RuntimeError(message)
        self.algorithm_c_authorized = True
        self.xap_authorized = True
        self.state("R0_COMPLETE", r0_started=True, r0_complete=True,
                   r0_gate_valid=True, rolling_dynamic_gate="PASS",
                   r0_profile_gate=profile_gate,
                   database_counter_coverage_gate=database_counter_gate,
                   algorithm_c_authorized=True, xap_authorized=True,
                   algorithm_process_live=True)

    def cleanup(self) -> None:
        if self.capture_general_log:
            try:
                # This must run before stopping the workload, tunnel, or
                # remote container so an in-flight query cannot lose the log.
                self.export_capture_general_log(
                    require_complete=self.capture_session_succeeded
                )
            except BaseException as error:
                (self.output / "production_general_log_error.txt").write_text(
                    f"{type(error).__name__}: {error}\\n", encoding="utf-8"
                )
        self.database_sampler_stop.set()
        for thread in self.database_sampler_threads:
            thread.join(timeout=10)
        process = getattr(self.session, "process", None) if self.session is not None else None
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=10)
        for process in (self.sampler, self.tunnel):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
        if self.sampler_thread is not None:
            self.sampler_thread.join(timeout=10)
        if self._sampler_stderr is not None and not self._sampler_stderr.closed:
            self._sampler_stderr.close()
        script = f"""set +e
docker stop --time 120 {self.container} >/dev/null 2>&1
docker rm -f {self.container} >/dev/null 2>&1
docker rm -f {self.container}_bootstrap >/dev/null 2>&1
test "$(dirname {self.remote_root})" = {self.lane.project}/formal_sgd_runs || exit 91
if test -d {self.remote_root}; then
  docker run --rm --network none --cpus 0.5 --cpuset-cpus {self.lane.cpuset.split(',')[0]} \
    --cpuset-mems {self.lane.mems} --memory 268435456 --memory-swap 268435456 --pids-limit 64 \
    -v {self.remote_root}:/cleanup:rw --entrypoint /bin/sh {IMAGE} \
    -c 'find /cleanup -mindepth 1 -delete' >/dev/null 2>&1
  rmdir {self.remote_root} 2>/dev/null
fi
"""
        errors: list[str] = []
        for cleanup_attempt in range(1, 4):
            try:
                self.remote(script, timeout=300)
                verify = self.remote(
                    f"""set -e
test -z "$(docker ps -aq --filter name=^{self.container}$)"
test -z "$(docker ps -aq --filter name=^{self.container}_bootstrap$)"
""",
                    timeout=30,
                )
                if verify.returncode == 0:
                    return
            except Exception as error:
                errors.append(f"attempt {cleanup_attempt}: {error}")
                if cleanup_attempt < 3:
                    time.sleep(3)
        (self.output / "cleanup_error.txt").write_text(
            "\n".join(errors) + "\n",
            encoding="utf-8",
        )

    def capture_failure_diagnostics(self) -> None:
        script = f"""set +e
printf '%s\n' '=== docker inspect ==='
docker inspect {self.container}
printf '%s\n' '=== docker logs tail ==='
docker logs --tail 400 {self.container}
"""
        try:
            result = self.remote(script, timeout=60)
            (self.output / "remote_failure_diagnostics.stdout.log").write_text(
                result.stdout, encoding="utf-8"
            )
            (self.output / "remote_failure_diagnostics.stderr.log").write_text(
                result.stderr, encoding="utf-8"
            )
        except Exception as error:
            (self.output / "remote_failure_diagnostics_error.txt").write_text(
                str(error), encoding="utf-8"
            )


def validate_inputs(lane: Lane) -> None:
    required = [
        PROTOCOL, JAVA, MYSQL, CONNECTOR, HYBENCH_JAR, STATEMENTS,
        BENCHBASE_JAR, TPCH_AGENT, TPCC_SENSITIVE_SQL, JOB_QUERY_DIR,
        *(Path(path) for path in lane.experience),
    ]
    if lane.key == "xp":
        required.extend([
            XP_V7_JAR,
            XP_V7_CLASSES / "com/hybench/HyBenchFormalMain.class",
            XP_V7_BUNDLE / "manifest.json",
        ])
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"required launch inputs absent: {missing}")
    if lane.key == "xp":
        manifest = json.loads(
            (XP_V7_BUNDLE / "manifest.json").read_text(encoding="utf-8")
        )
        if manifest.get("formal_scope") != "mysql_only":
            raise RuntimeError("XP runtime bundle does not declare mysql_only scope")
        if manifest.get("cross_engine_comparison_prohibited") is not True:
            raise RuntimeError(
                "XP runtime bundle does not prohibit cross-engine comparison"
            )
        expected_envelope = {
            "cpu_cores": lane.cpu_cores,
            "cpuset": lane.cpuset,
            "mems": lane.mems,
            "read_bps": lane.read_bps,
            "write_bps": lane.write_bps,
        }
        actual_envelope = manifest.get("resource_envelope")
        if actual_envelope != expected_envelope:
            raise RuntimeError(
                "XP runtime bundle resource envelope differs from launch lane: "
                f"expected={expected_envelope!r} actual={actual_envelope!r}"
            )
        if manifest.get("formal_graceful_quiescence_seconds") != 120:
            raise RuntimeError("XP runtime bundle graceful quiescence must be 120 seconds")
        wrapper = XP_V7_CLASSES / "com/hybench/HyBenchFormalMain.class"
        checks = {
            XP_V7_JAR: manifest.get("runtime", {}).get("jar_sha256"),
            wrapper: manifest.get("runtime", {}).get("wrapper_sha256"),
            CONNECTOR: manifest.get("runtime", {}).get("connector_sha256"),
            STATEMENTS: manifest.get("runtime", {}).get("statements_sha256"),
        }
        for path, expected in checks.items():
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if not expected or actual != expected:
                raise RuntimeError(
                    f"XP content-addressed runtime mismatch: {path}"
                )


ACTIVE: Campaign | None = None


def request_stop(_signum: int, _frame: object) -> None:
    if ACTIVE is not None:
        ACTIVE.stop_requested = True
        ACTIVE.cleanup()
    raise SystemExit(128 + _signum)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=tuple(LANES), required=True)
    parser.add_argument(
        "--capture-general-log",
        action="store_true",
        help="TPC-H only: run a 900-second graceful capture and require Q1-Q22 log coverage",
    )
    parser.add_argument(
        "--capture-tpch-bindings",
        action="store_true",
        help="TPC-H only: authorize Q1-Q22 bindings with one transaction per run",
    )
    parser.add_argument(
        "--binding-start-query",
        type=int,
        default=1,
        help="TPC-H binding capture resume point in [1,22]",
    )
    parser.add_argument(
        "--binding-prefix-log",
        type=Path,
        help="completed Q1..Q(start-1) general log to prepend on binding resume",
    )
    parser.add_argument("--run-id")
    parser.add_argument(
        "--cost-json",
        type=Path,
        help="non-empty JSON object containing one frozen MySQL cost candidate",
    )
    parser.add_argument(
        "--profile-json",
        type=Path,
        help=(
            "calibration-only resource override; accepted fields are cpuset, "
            "mems, cpu_cores, memory_bytes, pids, read_bps, write_bps and "
            "buffer_pool_bytes"
        ),
    )
    parser.add_argument(
        "--measurement-seconds",
        type=int,
        default=600,
        help="maximum measured interval after the fixed 60-second warm-up",
    )
    parser.add_argument(
        "--minimum-measurement-seconds",
        type=int,
        default=180,
        help="earliest rolling-stability point eligible for selection",
    )
    parser.add_argument(
        "--minimum-client-completions",
        type=int,
        default=0,
        help="minimum workload-native completed operations required for early stop",
    )
    parser.add_argument(
        "--require-r0-profile-gate",
        action="store_true",
        help="require target utilization 85-95 percent and both non-targets 30-50 percent",
    )
    args = parser.parse_args()
    if args.capture_general_log and args.scenario != "tpch":
        parser.error("--capture-general-log is only supported for --scenario tpch")
    if args.capture_general_log and args.measurement_seconds != 900:
        parser.error("--capture-general-log requires --measurement-seconds 900")
    if args.capture_tpch_bindings and args.scenario != "tpch":
        parser.error("--capture-tpch-bindings is only supported for --scenario tpch")
    if args.capture_tpch_bindings and args.measurement_seconds != 900:
        parser.error("--capture-tpch-bindings requires --measurement-seconds 900")
    if args.capture_general_log and args.capture_tpch_bindings:
        parser.error("--capture-general-log and --capture-tpch-bindings are mutually exclusive")
    if not 1 <= args.binding_start_query <= 22:
        parser.error("--binding-start-query must be within [1,22]")
    if args.binding_start_query != 1 and not args.capture_tpch_bindings:
        parser.error("--binding-start-query requires --capture-tpch-bindings")
    if args.binding_start_query > 1 and args.binding_prefix_log is None:
        parser.error("--binding-prefix-log is required when resuming bindings")
    if args.binding_prefix_log is not None:
        if not args.capture_tpch_bindings:
            parser.error("--binding-prefix-log requires --capture-tpch-bindings")
        if not args.binding_prefix_log.is_file():
            parser.error("--binding-prefix-log must be an existing file")
        if args.binding_prefix_log.stat().st_size <= 0:
            parser.error("--binding-prefix-log must not be empty")
    if args.measurement_seconds < 180 or args.measurement_seconds % 30:
        parser.error("--measurement-seconds must be a multiple of 30 and at least 180")
    if (
        args.minimum_measurement_seconds < 180
        or args.minimum_measurement_seconds > args.measurement_seconds
        or args.minimum_measurement_seconds % 30
    ):
        parser.error(
            "--minimum-measurement-seconds must be a multiple of 30 "
            "within [180, measurement-seconds]"
        )
    if args.minimum_client_completions < 0:
        parser.error("--minimum-client-completions cannot be negative")
    lane = LANES[args.scenario]
    if args.profile_json is not None:
        value = json.loads(args.profile_json.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            parser.error("--profile-json root must be an object")
        allowed = {
            "cpuset",
            "mems",
            "cpu_cores",
            "memory_bytes",
            "pids",
            "read_bps",
            "write_bps",
            "buffer_pool_bytes",
        }
        audit_metadata = {
            "schema_version",
            "scenario",
            "benchmark",
            "host",
            "hostname",
            "device",
            "mount",
        }
        unknown = set(value) - allowed - audit_metadata
        if unknown:
            parser.error(
                "--profile-json has unsupported fields: "
                + ", ".join(sorted(unknown))
            )
        lane = replace(
            lane,
            **{key: value[key] for key in allowed if key in value},
        )
    candidate_costs: Mapping[str, float] | None = None
    if args.cost_json is not None:
        raw_costs = json.loads(args.cost_json.read_text(encoding="utf-8"))
        if not isinstance(raw_costs, Mapping) or not raw_costs:
            parser.error("--cost-json root must be a non-empty object")
        unknown = sorted(set(map(str, raw_costs)) - set(MYSQL_COST_KEYS))
        if unknown:
            parser.error(
                "--cost-json has unsupported MySQL costs: " + ", ".join(unknown)
            )
        try:
            candidate_costs = {
                str(name): float(value) for name, value in raw_costs.items()
            }
        except (TypeError, ValueError):
            parser.error("--cost-json values must be finite positive numbers")
        if any(
            not math.isfinite(value) or value <= 0
            for value in candidate_costs.values()
        ):
            parser.error("--cost-json values must be finite positive numbers")
    validate_inputs(lane)
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if re.fullmatch(r"[0-9A-Za-z_-]+", run_id) is None:
        parser.error("run-id must contain only letters, digits, underscore or hyphen")
    output = HISTORY / lane.key / "formal_sgd_campaigns" / run_id
    campaign = Campaign(
        lane,
        output,
        run_id,
        candidate_costs,
        measurement_seconds=args.measurement_seconds,
        minimum_measurement_seconds=args.minimum_measurement_seconds,
        minimum_client_completions=args.minimum_client_completions,
        capture_general_log=args.capture_general_log,
        capture_tpch_bindings=args.capture_tpch_bindings,
        binding_start_query=args.binding_start_query,
        binding_prefix_log=args.binding_prefix_log,
        require_r0_profile_gate=args.require_r0_profile_gate,
    )
    global ACTIVE
    ACTIVE = campaign
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        if args.capture_tpch_bindings:
            campaign.execute_tpch_binding_capture()
        elif args.capture_general_log:
            campaign.execute_capture_general_log()
        else:
            campaign.execute()
        return 0
    except BaseException as error:
        if output.exists():
            campaign.capture_failure_diagnostics()
            previous_phase = None
            state_path = campaign.state_path
            if state_path.is_file():
                try:
                    previous_phase = json.loads(
                        state_path.read_text(encoding="utf-8")
                    ).get("phase")
                except (OSError, ValueError):
                    previous_phase = None
            failure_phase = (
                previous_phase
                if previous_phase in {"INVALID_R0_PROFILE", "INVALID_R0_UNSTABLE"}
                else (
                    "INVALID_R0_UNSTABLE"
                    if campaign.r0_gate_valid is False
                    else "FAILED"
                )
            )
            previous_r0_profile_gate = None
            if state_path.exists():
                try:
                    previous_r0_profile_gate = json.loads(
                        state_path.read_text(encoding="utf-8")
                    ).get("r0_profile_gate")
                except (OSError, ValueError):
                    previous_r0_profile_gate = None
            campaign.state(
                failure_phase,
                error=f"{type(error).__name__}: {error}",
                algorithm_process_live=False,
                r0_gate_valid=campaign.r0_gate_valid,
                r0_profile_gate=previous_r0_profile_gate,
                algorithm_c_authorized=False,
                xap_authorized=False,
                rolling_dynamic_gate="FAIL" if campaign.r0_gate_valid is False else "FAILED",
            )
            (output / "failure.txt").write_text(f"{type(error).__name__}: {error}\n", encoding="utf-8")
        raise
    finally:
        campaign.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
