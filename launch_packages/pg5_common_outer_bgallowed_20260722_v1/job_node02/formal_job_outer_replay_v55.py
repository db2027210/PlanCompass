#!/usr/bin/env python3
"""JOB v55 bridge using only pre-execution sealed EXPLAIN identity."""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import statistics
from typing import Any, Dict, Iterator, Mapping, Optional, Sequence

import psycopg2


CAMPAIGN_ID = "pg5_common_outer_bgallowed_20260722_v1"
LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
CONTROL_PORT = 64309
POINT_CONTAINER_NAME = "ra_pg5_job_node02_v1_outerpoint"
POINT_PORT = 64310
COST_PARAMETERS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
PARAMETER_RANGES = {
    "seq_page_cost": (0.1, 10.0),
    "random_page_cost": (0.1, 20.0),
    "cpu_tuple_cost": (0.001, 0.1),
    "cpu_index_tuple_cost": (0.0005, 0.05),
    "cpu_operator_cost": (0.00025, 0.025),
    "parallel_setup_cost": (0.0, 10000.0),
    "parallel_tuple_cost": (0.001, 1.0),
}
PROHIBITED_DOCKER_NETWORKS = frozenset({"bridge", "host", "none"})


def job_docker_network() -> str:
    """Return the explicit dedicated JOB bridge network or fail closed."""
    raw = os.environ.get("RA_JOB_DOCKER_NETWORK", "")
    value = raw.strip()
    if (
        not value
        or value != raw
        or value.lower() in PROHIBITED_DOCKER_NETWORKS
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", value) is None
    ):
        raise RuntimeError(
            "RA_JOB_DOCKER_NETWORK must name a dedicated network and may not "
            "be bridge, host, or none"
        )
    return value


def dedicated_network_resource_arguments(arguments: Sequence[str]) -> list[str]:
    """Remove exactly one legacy ``--network bridge`` pair and nothing else."""
    raw = list(arguments)
    filtered: list[str] = []
    removed = 0
    index = 0
    while index < len(raw):
        token = raw[index]
        if token == "--network":
            if index + 1 >= len(raw):
                raise RuntimeError("resource arguments end with bare --network")
            mode = raw[index + 1]
            if mode != "bridge":
                raise RuntimeError(
                    f"unexpected resource network mode: --network {mode}"
                )
            removed += 1
            index += 2
            continue
        if (
            token == "--net"
            or token.startswith("--net=")
            or token.startswith("--network=")
        ):
            raise RuntimeError(f"unexpected resource network token: {token}")
        filtered.append(token)
        index += 1
    if removed != 1:
        raise RuntimeError(
            "resource arguments must contain exactly one legacy --network bridge pair"
        )
    return filtered


def validate_final_network_command(command: Sequence[str], expected: str) -> None:
    """Require exactly one explicit, non-default network in the final argv."""
    pairs = []
    for index, token in enumerate(command):
        if token == "--network":
            if index + 1 >= len(command):
                raise RuntimeError("final Docker command ends with bare --network")
            pairs.append(command[index + 1])
        elif (
            token == "--net"
            or token.startswith("--net=")
            or token.startswith("--network=")
        ):
            raise RuntimeError(f"unexpected final Docker network token: {token}")
    if (
        pairs != [expected]
        or expected.lower() in PROHIBITED_DOCKER_NETWORKS
    ):
        raise RuntimeError(
            f"final Docker command network contract mismatch: {pairs!r}"
        )


def outer_root() -> Path:
    """Resolve the attempt root at call time for isolated tests and resumes."""
    return Path(
        os.environ.get(
            "RA_PG_OUTPUT_ROOT",
            str(LANE_ROOT / "history/job/outer/attempt_1"),
        )
    )


def frozen_measurement_timing() -> Dict[str, int]:
    """Load the exact public-v5 decision timing used by the point runtime."""
    frozen_root = Path(
        os.environ.get(
            "RA_PG_FROZEN_ROOT",
            str(LANE_ROOT / "bundle/attempt8_frozen"),
        )
    )
    protocol_path = frozen_root / "measurement_protocol.json"
    try:
        payload = json.loads(protocol_path.read_text(encoding="utf-8"))
        timing = payload["timing"]
        result = {
            "minimum_measurement_seconds": int(timing["minimum_measurement_seconds"]),
            "initial_probe_seconds": int(timing["initial_probe_seconds"]),
            "decision_step_seconds": int(timing["decision_step_seconds"]),
            "maximum_measurement_seconds": int(timing["maximum_measurement_seconds"]),
        }
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"cannot load frozen measurement timing: {exc}") from exc
    expected = {
        "minimum_measurement_seconds": 180,
        "initial_probe_seconds": 180,
        "decision_step_seconds": 30,
        "maximum_measurement_seconds": 600,
    }
    if result != expected:
        raise RuntimeError(f"unexpected frozen JOB v5 measurement timing: {result}")
    return result


def lane_run(
    command: Sequence[str],
    *,
    check: bool = True,
    capture: bool = True,
    env: Optional[Mapping[str, str]] = None,
    timeout: Optional[float] = None,
    cwd: Optional[Path] = None,
    input_text: Optional[str] = None,
) -> subprocess.CompletedProcess[str]:
    argv = list(command)
    if argv and argv[0] == "sudo":
        argv = argv[1:]
    result = subprocess.run(
        argv,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        env=dict(env) if env is not None else None,
        timeout=timeout,
        cwd=str(cwd) if cwd is not None else None,
        input=input_text,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed exit={result.returncode}: {argv!r}\n"
            f"stdout={(result.stdout or '')[-4000:]}\n"
            f"stderr={(result.stderr or '')[-4000:]}"
        )
    return result


@contextlib.contextmanager
def lane_lock() -> Iterator[Dict[str, Any]]:
    path = LANE_ROOT / "lease" / "job_lane.lock"
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        owner = {
            "campaign_id": CAMPAIGN_ID,
            "lane_id": "job_node02",
            "pid": os.getpid(),
            "acquired_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "implementation": "posix_flock_exclusive",
            "background_load_allowed": True,
            "scope": "one_fresh_template_outer_replay",
        }
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(owner, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        try:
            yield {"path": str(path), **owner}
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def cleanup_runtime(engine: Any, kind: str, restore: Mapping[str, Any]) -> Dict[str, Any]:
    name, _ = engine.container_spec(kind)
    engine.remove_stopped_container(name)
    root = Path(str(restore["runtime_root"])).resolve()
    allowed_runtime_root = Path(
        os.environ.get(
            "RA_PG_WORK_ROOT",
            str(LANE_ROOT / "work" / "runtime_outer"),
        )
    ).resolve()
    root.relative_to(allowed_runtime_root)
    if root.exists():
        engine.run(
            [
                "docker", "run", "--rm", "--network", "none",
                "--volume", f"{root}:/lane-runtime",
                "--entrypoint", "sh", engine.IMAGE,
                "-c", "find /lane-runtime -mindepth 1 -depth -delete",
            ],
            capture=False,
            timeout=1800,
        )
        root.rmdir()
    return {
        "cleanup_complete": not root.exists(),
        "template_preserved": True,
        "cleanup_transport": "validated runtime root via isolated Docker helper",
    }


def start_lane_container(
    engine: Any,
    kind: str,
    pgdata: Path,
    profile_name: str = "none",
    cost_parameters: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    docker_network = job_docker_network()
    name, port = engine.container_spec(kind)
    if name != POINT_CONTAINER_NAME or port != POINT_PORT:
        raise engine.FormalPgError(f"unexpected formal point endpoint: {name}:{port}")
    if engine.run(["docker", "inspect", name], check=False).returncode == 0:
        raise engine.FormalPgError(f"formal point container already exists: {name}")
    tmp_dir = LANE_ROOT / "tmp" / "postgres_outer_point"
    socket_dir = LANE_ROOT / "socket" / "postgresql_outer_point"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    socket_dir.mkdir(parents=True, exist_ok=True)
    lane_run(
        [
            "docker", "run", "--rm", "--network", "none",
            "--volume", f"{tmp_dir}:/tmp-target",
            "--volume", f"{socket_dir}:/socket-target",
            "--entrypoint", "sh", engine.IMAGE,
            "-c", "chmod 0777 /tmp-target /socket-target",
        ],
        capture=False,
        timeout=120,
    )
    command = [
        "docker", "run", "--detach", "--name", name,
        "--network", docker_network,
        "--label", f"resource_adaptive.campaign={CAMPAIGN_ID}",
        "--label", "resource_adaptive.lane=job_node02",
        "--label", "resource_adaptive.role=outer_formal_point",
        "--publish", f"127.0.0.1:{port}:5432",
        "--volume", f"{pgdata}:/var/lib/postgresql/data",
        "--volume", f"{tmp_dir}:/tmp",
        "--volume", f"{socket_dir}:/var/run/postgresql",
        "--log-driver", "local",
        "--shm-size", str(engine.RUNTIME_SHM_SIZE_BYTES),
        "--health-cmd", "pg_isready -U postgres -d imdb_job",
        "--health-interval", "5s", "--health-timeout", "3s", "--health-retries", "36",
    ]
    command.extend(
        dedicated_network_resource_arguments(
            engine.docker_resource_arguments(profile_name)
        )
    )
    command.append(engine.IMAGE)
    command.extend(engine.fixed_postgres_arguments(profile_name, cost_parameters))
    validate_final_network_command(command, docker_network)
    container_id = engine.run(command).stdout.strip()
    inspect = json.loads(engine.run(["docker", "inspect", container_id]).stdout)[0]
    expected_rate = 134_217_728
    for field in ("BlkioDeviceReadBps", "BlkioDeviceWriteBps"):
        values = inspect["HostConfig"].get(field) or []
        if not any(
            row.get("Path") == "/dev/nvme2n1"
            and int(row.get("Rate", 0)) == expected_rate
            for row in values
        ):
            raise engine.FormalPgError(f"Docker I/O readback mismatch for {field}: {values}")
    import time

    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        health = engine.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", name],
            check=False,
        )
        if health.stdout.strip() == "healthy":
            break
        state = json.loads(engine.run(["docker", "inspect", name]).stdout)[0]["State"]
        if not state["Running"]:
            logs = engine.run(["docker", "logs", name], check=False)
            raise engine.FormalPgError(
                "PostgreSQL stopped before healthy: "
                f"{(logs.stdout or '')[-8000:]} {(logs.stderr or '')[-8000:]}"
            )
        time.sleep(2)
    else:
        raise engine.FormalPgError("formal point PostgreSQL did not become healthy")
    engine.run(
        [
            "docker", "exec", "-i", "-u", "postgres", name,
            "psql", "-v", "ON_ERROR_STOP=1", "-d", "postgres",
        ],
        input_text="ALTER ROLE postgres PASSWORD 'CHANGE_ME';\n",
    )
    return {
        "name": name,
        "port": port,
        "container_id": container_id,
        "profile": profile_name,
        "runtime_shm_size_bytes": engine.RUNTIME_SHM_SIZE_BYTES,
        "docker_command": command,
        "healthcheck": "healthy",
        "io_limit_application": "Docker device BPS flags plus HostConfig readback",
        "data_path": str(pgdata),
        "tmp_path": str(tmp_dir),
        "socket_path": str(socket_dir),
        "docker_root_on_nvme": True,
        "docker_network": docker_network,
    }


def claim_replay_accounting() -> Dict[str, Any]:
    """Claim one immutable global ordinal and, only for search, its own budget."""
    root = outer_root()
    global_path = root / "replay_counter.txt"
    search_path = root / "formal_search_replay_counter.txt"
    startup_path = root / "formal_startup_baseline_counter.txt"
    lock_path = root / "replay_accounting.lock"
    scope = os.environ.get("RA_PG_REPLAY_ACCOUNTING_SCOPE", "formal_search").strip()
    if scope not in {"formal_search", "fresh_default_r0", "quality_calibration"}:
        raise RuntimeError(f"invalid replay accounting scope: {scope!r}")
    maximum_search_replays = max(
        1,
        int(os.environ.get("RA_PG_FORMAL_SEARCH_MAX_REPLAYS", "130")),
    )
    attempt_safety_cap = max(
        1,
        int(
            os.environ.get(
                "RA_PG_FORMAL_SEARCH_ATTEMPT_SAFETY_CAP",
                str(maximum_search_replays),
            )
        ),
    )
    if maximum_search_replays != attempt_safety_cap:
        raise RuntimeError(
            "formal-search global replay cap must equal the optimizer attempt "
            f"safety cap: {maximum_search_replays} != {attempt_safety_cap}"
        )
    root.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        global_before = int(global_path.read_text(encoding="utf-8").strip())
        if global_before < 13:
            raise RuntimeError(
                f"global replay ordinal audit value regressed below 13: {global_before}"
            )
        search_before = (
            int(search_path.read_text(encoding="utf-8").strip())
            if search_path.is_file()
            else 0
        )
        startup_before = (
            int(startup_path.read_text(encoding="utf-8").strip())
            if startup_path.is_file()
            else 0
        )
        exclude_startup = (
            scope == "formal_search"
            and os.environ.get(
                "RA_PG_EXCLUDE_FIRST_FORMAL_REPLAY_AS_STARTUP_BASELINE",
                "0",
            ) == "1"
        )
        effective_scope = (
            "formal_startup_baseline"
            if exclude_startup and startup_before == 0
            else scope
        )
        startup_global_ordinal = int(
            os.environ.get("RA_PG_STARTUP_BASELINE_GLOBAL_ORDINAL", "14")
        )
        if (
            effective_scope == "formal_startup_baseline"
            and global_before != startup_global_ordinal
        ):
            raise RuntimeError(
                "the one excluded startup baseline must immediately follow fresh R0 "
                f"global ordinal {startup_global_ordinal}, got {global_before}"
            )
        if effective_scope == "formal_search" and search_before >= maximum_search_replays:
            raise RuntimeError(
                "formal-search replay budget exhausted before global ordinal claim: "
                f"{search_before}/{maximum_search_replays}"
            )
        global_after = global_before + 1
        search_after = (
            search_before + 1
            if effective_scope == "formal_search"
            else search_before
        )
        startup_after = (
            startup_before + 1
            if effective_scope == "formal_startup_baseline"
            else startup_before
        )
        with global_path.open("w", encoding="utf-8") as handle:
            handle.write(f"{global_after}\n")
            handle.flush()
            os.fsync(handle.fileno())
        if effective_scope == "formal_search":
            with search_path.open("w", encoding="utf-8") as handle:
                handle.write(f"{search_after}\n")
                handle.flush()
                os.fsync(handle.fileno())
        if effective_scope == "formal_startup_baseline":
            with startup_path.open("w", encoding="utf-8") as handle:
                handle.write(f"{startup_after}\n")
                handle.flush()
                os.fsync(handle.fileno())
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    return {
        "requested_scope": scope,
        "scope": effective_scope,
        "global_replay_counter_path": str(global_path),
        "global_ordinal_before": global_before,
        "global_ordinal": global_after,
        "formal_search_replay_counter_path": str(search_path),
        "formal_search_replay_count_before": search_before,
        "formal_search_replay_count": search_after,
        "formal_search_maximum_replay_count": maximum_search_replays,
        "formal_search_attempt_safety_cap": attempt_safety_cap,
        "formal_startup_baseline_counter_path": str(startup_path),
        "formal_startup_baseline_count_before": startup_before,
        "formal_startup_baseline_count": startup_after,
        "startup_baseline_excluded_from_candidate_budget": (
            effective_scope == "formal_startup_baseline"
        ),
        "historical_counter_reset": False,
    }


def enforce_pause_before_next_claim() -> None:
    """Honor a sealed maintenance boundary without consuming an ordinal."""
    root = outer_root()
    marker_path = root / "pause_before_next_formal_claim.json"
    if not marker_path.is_file():
        return
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker.get("schema_version") != 1 or marker.get("pause") is not True:
        raise RuntimeError("formal replay pause marker is invalid")
    expected_ordinal = int(marker.get("global_ordinal_after_completed_point", -1))
    actual_ordinal = int(
        (root / "replay_counter.txt").read_text(encoding="utf-8").strip()
    )
    if actual_ordinal < expected_ordinal:
        return
    if actual_ordinal != expected_ordinal:
        raise RuntimeError(
            "formal replay pause boundary was crossed unexpectedly: "
            f"{actual_ordinal} != {expected_ordinal}"
        )
    raise RuntimeError(
        "formal replay paused before ordinal claim for a sealed protocol transition: "
        f"{marker.get('reason') or 'unspecified'}"
    )


def read_control_costs() -> Dict[str, float]:
    with psycopg2.connect(
        host="127.0.0.1",
        port=CONTROL_PORT,
        user="postgres",
        password="CHANGE_ME",
        dbname="imdb_job",
        application_name="formal-outer-cost-readback",
    ) as connection:
        with connection.cursor() as cursor:
            result: Dict[str, float] = {}
            for name in COST_PARAMETERS:
                cursor.execute(f"SHOW {name}")
                result[name] = float(cursor.fetchone()[0])
    return result


def resolve_replay_costs() -> tuple[Dict[str, float], Dict[str, Any]]:
    """Use an exact audited calibration vector when explicitly supplied."""
    raw = os.environ.get("RA_PG_EXPLICIT_COST_PARAMETERS_JSON", "").strip()
    if not raw:
        return read_control_costs(), {
            "source": "control_postgresql_show",
            "explicit_override": False,
        }
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("RA_PG_EXPLICIT_COST_PARAMETERS_JSON is invalid JSON") from exc
    if not isinstance(parsed, dict) or set(parsed) != set(COST_PARAMETERS):
        raise RuntimeError(
            "explicit cost vector must contain exactly the seven frozen cost parameters"
        )
    result: Dict[str, float] = {}
    for name in COST_PARAMETERS:
        value = float(parsed[name])
        lower, upper = PARAMETER_RANGES[name]
        if not math.isfinite(value) or value < lower or value > upper:
            raise RuntimeError(
                f"explicit cost parameter outside frozen range: {name}={value} "
                f"not in [{lower}, {upper}]"
            )
        result[name] = value
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"))
    return result, {
        "source": "RA_PG_EXPLICIT_COST_PARAMETERS_JSON",
        "explicit_override": True,
        "canonical_sha256": __import__("hashlib").sha256(
            canonical.encode("utf-8")
        ).hexdigest(),
        "selection_id": os.environ.get("RA_PG_CALIBRATION_SELECTION_ID"),
        "selection_subset_id": os.environ.get("RA_PG_CALIBRATION_SUBSET_ID"),
    }


def seed_control_plan_statements() -> None:
    """The v54 post-run full-query seed is forbidden by replay accounting."""
    raise RuntimeError(
        "JOB v55 forbids uncounted post-run pg_stat_statements query seeding"
    )


def classify_legacy_postrun_seed_failure(
    *,
    core_measurement_sealed: bool,
    operation: str,
    error: BaseException,
) -> Dict[str, Any]:
    """Classify only the removed v54 auxiliary failure without hiding others."""
    recognized = (
        core_measurement_sealed
        and operation == "legacy_postrun_pg_stat_statements_seed"
    )
    if not recognized:
        raise RuntimeError(
            "non-auxiliary or pre-seal failure remains a technical failure"
        ) from error
    return {
        "contract": "job_v55_legacy_postrun_seed_failure_classification_v1",
        "core_measurement_status": "valid",
        "physical_invocation_technical_failure": False,
        "auxiliary_audit_status": "failed_nonblocking",
        "operation": operation,
        "error_type": type(error).__name__,
        "error": str(error),
        "repeat_query_count": 0,
        "formal_state_update": False,
    }


def validate_preexecution_sealed_explain_binding(
    costs: Mapping[str, float],
) -> Dict[str, Any]:
    """Validate the exact candidate's sealed EXPLAIN identity before claim."""
    raw = os.environ.get("RA_PG_SEALED_EXPLAIN_BINDING_JSON", "").strip()
    if not raw:
        raise RuntimeError("sealed pre-execution EXPLAIN binding is missing")
    try:
        binding = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("sealed pre-execution EXPLAIN binding is invalid JSON") from exc
    canonical = json.dumps(dict(costs), sort_keys=True, separators=(",", ":"))
    parameter_sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    fingerprint = binding.get("three_level_fingerprint") or {}
    selection_path = Path(str(binding.get("selection_path") or ""))
    if (
        binding.get("contract")
        != "job_v55_preexecution_sealed_explain_binding_v1"
        or binding.get("candidate_parameter_sha256") != parameter_sha
        or not selection_path.is_absolute()
        or not selection_path.is_file()
        or hashlib.sha256(selection_path.read_bytes()).hexdigest()
        != binding.get("selection_sha256")
        or not str(binding.get("fingerprint_id") or "").startswith(
            "three-level-sha256:"
        )
        or binding.get("fingerprint_id") != fingerprint.get("id")
        or binding.get("plan_level") != fingerprint.get("plan_level")
        or not binding.get("plan_level")
        or binding.get("post_run_pg_stat_statements_seed_used") is not False
    ):
        raise RuntimeError("sealed pre-execution EXPLAIN binding contract mismatch")
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    candidates = selection.get("top5") or selection.get("candidates") or []
    matches = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        params = candidate.get("candidate_params") or candidate.get(
            "optimized_params"
        )
        if not isinstance(params, Mapping) or set(params) != set(COST_PARAMETERS):
            continue
        candidate_costs = {name: float(params[name]) for name in COST_PARAMETERS}
        candidate_canonical = json.dumps(
            candidate_costs, sort_keys=True, separators=(",", ":")
        )
        if (
            candidate_canonical == canonical
            and candidate.get("three_level_fingerprint") == fingerprint
            and candidate.get("boundary_corner") is False
        ):
            matches.append(candidate)
    if len(matches) != 1:
        raise RuntimeError(
            "sealed EXPLAIN binding does not match exactly one selection row"
        )
    return copy.deepcopy(dict(binding))


def load_jsonl(path: Path) -> list[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def finite_mean(values: Sequence[Any], *, field: str) -> float:
    parsed = [float(value) for value in values]
    if not parsed or any(not math.isfinite(value) for value in parsed):
        raise RuntimeError(f"replicate composite field is invalid: {field}")
    return float(statistics.fmean(parsed))


def required_valid_replicates_after_first(first_stable: bool) -> int:
    """A stable first run is final; an unstable first run commits to all three."""
    return 1 if first_stable else 3


def validate_technical_replicate(result: Mapping[str, Any]) -> None:
    measurement = result.get("measurement") or {}
    if result.get("status") != "complete":
        raise RuntimeError(f"formal replicate did not complete: {result.get('status')}")
    if measurement.get("correctness_passed") is not True:
        raise RuntimeError("formal replicate correctness gate failed")
    if result.get("oom_killed") or int(result.get("database_restart_count") or 0) != 0:
        raise RuntimeError("formal replicate OOM/restart gate failed")
    db_qps_raw = measurement.get("db_qps")
    if isinstance(db_qps_raw, bool) or not isinstance(db_qps_raw, (int, float)):
        raise RuntimeError(
            "formal replicate measurement.db_qps is missing; client completion QPS "
            "must not be used as a DB-QPS fallback"
        )
    db_qps = float(db_qps_raw)
    if not math.isfinite(db_qps) or db_qps <= 0.0:
        raise RuntimeError(
            f"formal replicate measurement.db_qps is non-positive/non-finite: {db_qps!r}"
        )


def composite_formal_resource(
    formal_resources: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    if len(formal_resources) != 3:
        raise RuntimeError("unstable composite requires exactly three valid replicates")
    caps = copy.deepcopy(formal_resources[0].get("caps") or {})
    if any((item.get("caps") or {}) != caps for item in formal_resources[1:]):
        raise RuntimeError("replicate resource caps changed within one point")
    aggregate_keys = (
        "cpu_utilization",
        "memory_utilization",
        "io_utilization",
        "read_bps",
        "write_bps",
        "total_bps",
        "memory_working_set_bytes",
    )
    aggregate = {
        key: finite_mean(
            [(item.get("aggregate") or {}).get(key) for item in formal_resources],
            field=f"formal_pg_resource.aggregate.{key}",
        )
        for key in aggregate_keys
    }
    return {
        "semantics": formal_resources[0].get("semantics"),
        "raw_range_validation": formal_resources[0].get("raw_range_validation"),
        "io_formula": formal_resources[0].get("io_formula"),
        "caps": caps,
        "sample_count": sum(int(item.get("sample_count") or 0) for item in formal_resources),
        "aggregate": aggregate,
        "composite": {
            "aggregation": "arithmetic_point_mean",
            "valid_replicate_count": 3,
            "first_replicate_unstable": True,
        },
    }


def resolve_restart_baseline(scope: str) -> tuple[Path, Dict[str, Any], str]:
    """Resolve the baseline that is valid for this accounting scope.

    The one fresh-default R0 is chained from the previously audited point 4.
    Every formal-search replay must instead bind the newly sealed R0 pointer.
    """
    attempt_root = outer_root()
    if scope == "fresh_default_r0":
        marker_path = (
            attempt_root
            / "formal_points/point_0004/formal_replay_validity.json"
        )
        attempt_id = "fresh_new_cap_default_point_0004"
    else:
        pointer_path = attempt_root / "fresh_restart_r0/latest.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        marker_path = Path(str(pointer.get("validity_marker_path") or ""))
        attempt_id = str(pointer.get("fresh_r0_id") or "")
        if (
            not marker_path.is_file()
            or hashlib.sha256(marker_path.read_bytes()).hexdigest()
            != pointer.get("validity_marker_sha256")
        ):
            raise RuntimeError("fresh restart R0 pointer/marker hash mismatch")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker.get("reuse_as_restart_baseline") is not True:
        raise RuntimeError("resolved fresh baseline does not authorize JOB restart")
    if ((marker.get("profile_gate") or {}).get("passed")) is not True:
        raise RuntimeError("resolved fresh baseline failed the requested resource profile")
    return marker_path, marker, attempt_id


def main() -> int:
    scope = os.environ.get("RA_PG_REPLAY_ACCOUNTING_SCOPE", "formal_search").strip()
    # Validate the dedicated network name before any immutable ordinal claim.
    _ = job_docker_network()
    fresh_baseline_marker_path, fresh_baseline_marker, r0_attempt = (
        resolve_restart_baseline(scope)
    )

    costs, cost_source = resolve_replay_costs()
    sealed_explain_binding = validate_preexecution_sealed_explain_binding(costs)
    enforce_pause_before_next_claim()
    replay_accounting = claim_replay_accounting()
    ordinal = int(replay_accounting["global_ordinal"])

    import p24_pg_recost_runner as p24

    p24.run = lane_run
    p24.CASES["job"]["name"] = POINT_CONTAINER_NAME
    p24.CASES["job"]["port"] = POINT_PORT
    _, points, validation = p24.configure_modules("job")
    import formal_pg_engine as engine

    engine.run = lane_run
    points.run = lane_run
    points.host_measurement_lock = lane_lock
    points.start_container = (
        lambda kind, pgdata, profile_name="none", cost_parameters=None: start_lane_container(
            engine, kind, pgdata, profile_name, cost_parameters
        )
    )
    points.cleanup_overlay = lambda kind, restore: cleanup_runtime(engine, kind, restore)

    run_attempt_id = os.environ.get(
        "RA_PG_RUN_ATTEMPT_ID",
        "job_experience_table_outer_attempt1",
    )
    point_id = f"{run_attempt_id}_{ordinal:04d}"
    output = outer_root() / "formal_points" / f"point_{ordinal:04d}"
    from formal_pg_resource_semantics import (
        normalize_derived_resource_samples,
        summarize_resource_samples,
    )

    replicate_total = int(os.environ.get("RA_PG_UNSTABLE_REPLICATE_TOTAL", "3"))
    if replicate_total != 3:
        raise RuntimeError("formal unstable replicate total must equal three")
    replicate_ledger = outer_root() / "formal_replicate_ledger.jsonl"
    base_context = {
        "purpose": "experience_table_all_subset_sgd_outer_real_replay",
        "outer_attempt": int(os.environ.get("RA_PG_OUTER_ATTEMPT", "1")),
        "replay_ordinal": ordinal,
        "replay_accounting": replay_accounting,
        "r0_attempt": r0_attempt,
        "r0_outer_allowed": True,
        "fresh_baseline_marker_path": str(fresh_baseline_marker_path),
        "fresh_baseline_marker_sha256": sha256_file(fresh_baseline_marker_path),
        "experience_table_artifact_sha256": os.environ.get(
            "RA_PG_EXPERIENCE_TABLE_ARTIFACT_SHA256", ""
        ),
        "experience_table_table_sha256": os.environ.get(
            "RA_PG_EXPERIENCE_TABLE_TABLE_SHA256", ""
        ),
        "early_stop_eligible_seconds": 180,
        "initial_observation_probe_seconds": 300,
        "maximum_measurement_seconds": 600,
        "throughput_role": "outer_acceptance_only",
        "cost_parameter_source": cost_source,
        "quality_calibration_role": os.environ.get("RA_PG_QUALITY_CALIBRATION_ROLE"),
        "quality_calibration_memory_cap_bytes": int(
            os.environ.get("RA_PG_MEMORY_BYTES", "3221225472")
        ),
        "source_validation": validation,
        "replicate_policy": {
            "first_stable_replicate_used_directly": True,
            "first_unstable_requires_total_valid_replicates": 3,
            "later_stability_does_not_short_circuit": True,
            "technical_failures_excluded_from_composite": True,
        },
    }
    replicates: list[Dict[str, Any]] = []
    required_valid_replicates = 1
    for replicate_index in range(1, replicate_total + 1):
        replicate_id = f"{point_id}_replicate_{replicate_index:02d}"
        replicate_output = (
            output / "replicates" / f"replicate_{replicate_index:02d}"
        )
        try:
            replicate_result = points.run_cost_point(
                "pg_job_memory",
                replicate_id,
                1,
                replicate_output,
                costs,
                evidence_context={
                    **base_context,
                    "replicate_index": replicate_index,
                    "replicate_id": replicate_id,
                    "replicate_output": str(replicate_output),
                },
                warmup_seconds=60,
                measurement_seconds=600,
            )
            validate_technical_replicate(replicate_result)
            replicate_measurement = replicate_result.get("measurement") or {}
            derived_path = Path(
                str(replicate_measurement["derived_resource_samples_path"])
            )
            samples = normalize_derived_resource_samples(load_jsonl(derived_path))
            replicate_formal_resource = summarize_resource_samples(
                samples,
                replicate_measurement["resource_denominators"],
            )
        except Exception as exc:
            append_jsonl(
                replicate_ledger,
                {
                    "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "replay_ordinal": ordinal,
                    "point_id": point_id,
                    "replicate_index": replicate_index,
                    "replicate_id": replicate_id,
                    "replicate_output": str(replicate_output),
                    "technical_failure": True,
                    "included_in_composite": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            raise RuntimeError(
                f"formal replicate {replicate_index} failed technically; "
                "it is excluded from every composite"
            ) from exc
        replicate_stability = replicate_measurement.get("resource_stability") or {}
        stable = bool(
            replicate_stability.get("passed") is True
            and replicate_stability.get("stable") is True
        )
        replicate_result_path = replicate_output / "point_result.json"
        record = {
            "result": replicate_result,
            "measurement": replicate_measurement,
            "formal_resource": replicate_formal_resource,
            "stable": stable,
            "result_path": replicate_result_path,
            "result_sha256": sha256_file(replicate_result_path),
        }
        replicates.append(record)
        append_jsonl(
            replicate_ledger,
            {
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                "replay_ordinal": ordinal,
                "point_id": point_id,
                "replicate_index": replicate_index,
                "replicate_id": replicate_id,
                "replicate_output": str(replicate_output),
                "point_result_path": str(replicate_result_path),
                "point_result_sha256": record["result_sha256"],
                "technical_failure": False,
                "valid_replicate": True,
                "stable": stable,
                "db_qps": float(replicate_measurement["db_qps"]),
                "resource_mean_utilization": replicate_measurement.get(
                    "resource_mean_utilization"
                ),
                "included_in_composite": replicate_index <= required_valid_replicates,
            },
        )
        if replicate_index == 1:
            required_valid_replicates = required_valid_replicates_after_first(stable)
        if len(replicates) >= required_valid_replicates:
            break

    selection_mode = "stable_first_replicate"
    if required_valid_replicates == 1:
        selected_result = copy.deepcopy(replicates[0]["result"])
        formal_resource = copy.deepcopy(replicates[0]["formal_resource"])
    else:
        if len(replicates) != replicate_total:
            raise RuntimeError("first unstable replicate did not reach three valid replicates")
        selection_mode = "three_valid_replicate_point_mean"
        selected_result = copy.deepcopy(replicates[0]["result"])
        formal_resource = composite_formal_resource(
            [item["formal_resource"] for item in replicates]
        )
        selected_measurement = selected_result.get("measurement") or {}
        selected_measurement["db_qps"] = finite_mean(
            [item["measurement"]["db_qps"] for item in replicates],
            field="measurement.db_qps",
        )
        selected_measurement["qps"] = finite_mean(
            [item["measurement"]["qps"] for item in replicates],
            field="measurement.qps",
        )
        selected_measurement["resource_mean_utilization"] = {
            resource: finite_mean(
                [
                    (item["measurement"].get("resource_mean_utilization") or {})[
                        resource
                    ]
                    for item in replicates
                ],
                field=f"measurement.resource_mean_utilization.{resource}",
            )
            for resource in ("cpu", "memory", "io")
        }
        latency_keys = ("mean", "p90", "p95", "p99", "max")
        selected_measurement["latency_ms"] = {
            key: finite_mean(
                [(item["measurement"].get("latency_ms") or {})[key] for item in replicates],
                field=f"measurement.latency_ms.{key}",
            )
            for key in latency_keys
            if all(key in (item["measurement"].get("latency_ms") or {}) for item in replicates)
        }
        selected_measurement["effective_measurement_seconds"] = finite_mean(
            [item["measurement"]["effective_measurement_seconds"] for item in replicates],
            field="measurement.effective_measurement_seconds",
        )
        selected_measurement["sample_coverage"] = finite_mean(
            [item["measurement"]["sample_coverage"] for item in replicates],
            field="measurement.sample_coverage",
        )
        selected_measurement["error_rate"] = finite_mean(
            [item["measurement"]["error_rate"] for item in replicates],
            field="measurement.error_rate",
        )
        selected_measurement["resource_stability"] = {
            "passed": True,
            "stable": True,
            "source": "three_valid_replicate_point_mean_after_first_unstable",
            "first_replicate_unstable": True,
            "valid_replicate_count": 3,
            "individual_stability": [item["stable"] for item in replicates],
            "technical_failures_included": False,
        }
        db_qps_evidence = {
            "schema_version": 1,
            "kind": "formal_db_qps_three_valid_replicate_point_mean",
            "replay_ordinal": ordinal,
            "aggregation": "arithmetic_mean",
            "db_qps": selected_measurement["db_qps"],
            "valid_replicate_count": 3,
            "technical_failures_included": False,
            "components": [
                {
                    "replicate_index": index,
                    "db_qps": float(item["measurement"]["db_qps"]),
                    "db_qps_evidence_path": item["measurement"].get(
                        "db_qps_evidence_path"
                    ),
                    "db_qps_evidence_sha256": item["measurement"].get(
                        "db_qps_evidence_sha256"
                    ),
                    "point_result_path": str(item["result_path"]),
                    "point_result_sha256": item["result_sha256"],
                }
                for index, item in enumerate(replicates, start=1)
            ],
        }
        composite_db_qps_path = output / "composite_db_qps_evidence.json"
        atomic_json(composite_db_qps_path, db_qps_evidence)
        selected_measurement["db_qps_evidence_path"] = str(composite_db_qps_path)
        selected_measurement["db_qps_evidence_sha256"] = sha256_file(
            composite_db_qps_path
        )
        selected_measurement["composite_point"] = {
            "selection_mode": selection_mode,
            "first_replicate_unstable": True,
            "valid_replicate_count": 3,
            "resource_aggregation": "arithmetic_point_mean",
            "db_qps_aggregation": "arithmetic_mean",
            "latency_aggregation": "arithmetic_point_mean",
        }
        selected_result["measurement"] = selected_measurement

    selected_result["point_id"] = point_id
    selected_result["replicate_selection"] = {
        "selection_mode": selection_mode,
        "first_replicate_stable": bool(replicates[0]["stable"]),
        "valid_replicate_count": len(replicates),
        "component_point_result_paths": [
            str(item["result_path"]) for item in replicates
        ],
        "component_point_result_sha256": [
            item["result_sha256"] for item in replicates
        ],
        "technical_failures_included": False,
    }
    atomic_json(output / "point_result.json", selected_result)
    result = selected_result
    measurement = result.get("measurement") or {}
    stability = measurement.get("resource_stability") or {}
    # v54 executed every JOB SQL file again here to seed pg_stat_statements.
    # That uncounted second workload could incorrectly turn an already sealed
    # valid measurement into a technical failure. v55 relies only on the
    # pre-claim sealed EXPLAIN binding validated above.

    client_qps = float(measurement["qps"])
    db_qps_raw = measurement.get("db_qps")
    if isinstance(db_qps_raw, bool) or not isinstance(db_qps_raw, (int, float)):
        raise RuntimeError(
            "formal point measurement.db_qps is missing; client completion QPS "
            "must not be used as a DB-QPS fallback"
        )
    db_qps = float(db_qps_raw)
    if not math.isfinite(db_qps) or db_qps <= 0.0:
        raise RuntimeError(
            f"formal point measurement.db_qps is non-positive/non-finite: {db_qps!r}"
        )
    db_qps_source = "formal_benchmark_db_qps"
    db_qps_evidence_path = measurement.get("db_qps_evidence_path")
    db_qps_evidence_sha256 = measurement.get("db_qps_evidence_sha256")
    latency = measurement.get("latency_ms") or {}
    frozen_timing = frozen_measurement_timing()
    marker = {
        "benchmark": {
            "ap_qps": client_qps,
            "client_completion_qps": client_qps,
            "db_qps": db_qps,
            "formal_qps": db_qps,
            "formal_qps_source": db_qps_source,
            "db_qps_source": db_qps_source,
            "db_qps_evidence_path": db_qps_evidence_path,
            "db_qps_evidence_sha256": db_qps_evidence_sha256,
            "query_count": int(measurement["query_count"]),
            "latency_p90": latency.get("p90"),
            "latency_p95": latency.get("p95"),
            "latency_p99": latency.get("p99"),
        },
        "benchmark_latency": {
            "all_p90_avg": latency.get("p90"),
            "all_p95_avg": latency.get("p95"),
            "all_p99_avg": latency.get("p99"),
        },
        "formal_pg_resource": formal_resource,
        "candidate_measurement_protocol": {
            "point_id": point_id,
            "replay_ordinal": ordinal,
            "point_result_path": str(output / "point_result.json"),
            "correctness_passed": True,
            "stable": True,
            "resource_stability": stability,
            "effective_measurement_seconds": measurement.get(
                "effective_measurement_seconds"
            ),
            "minimum_measurement_seconds": frozen_timing[
                "minimum_measurement_seconds"
            ],
            "initial_probe_seconds": frozen_timing["initial_probe_seconds"],
            "initial_probe_role": "first_target_stability_decision",
            "decision_step_seconds": frozen_timing["decision_step_seconds"],
            "maximum_measurement_seconds": frozen_timing[
                "maximum_measurement_seconds"
            ],
            "measurement_termination_reason": measurement.get(
                "measurement_termination_reason"
            ),
            "sample_coverage": measurement.get("sample_coverage"),
            "error_rate": measurement.get("error_rate"),
            "db_qps": db_qps,
            "db_qps_source": db_qps_source,
            "db_qps_evidence_path": db_qps_evidence_path,
            "db_qps_evidence_sha256": db_qps_evidence_sha256,
            "oom_killed": False,
            "database_restart_count": 0,
            "plan_fingerprint_provenance": sealed_explain_binding,
            "post_run_pg_stat_statements_seed_used": False,
        },
    }
    ledger = outer_root() / "formal_replay_ledger.jsonl"
    append_jsonl(
        ledger,
        {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "replay_ordinal": ordinal,
            "replay_accounting": replay_accounting,
            "point_id": point_id,
            "cost_parameters": costs,
            "point_result_path": str(output / "point_result.json"),
            "point_result_sha256": sha256_file(output / "point_result.json"),
            "selection_mode": selection_mode,
            "valid_replicate_count": len(replicates),
            "replicate_point_result_paths": [
                str(item["result_path"]) for item in replicates
            ],
            "replicate_point_result_sha256": [
                item["result_sha256"] for item in replicates
            ],
            "technical_failures_included": False,
            "plan_fingerprint_provenance": sealed_explain_binding,
            "post_run_pg_stat_statements_seed_used": False,
            "client_completion_qps": client_qps,
            "db_qps": db_qps,
            "db_qps_source": db_qps_source,
            "db_qps_evidence_path": db_qps_evidence_path,
            "db_qps_evidence_sha256": db_qps_evidence_sha256,
            "resource_mean_utilization": measurement.get(
                "resource_mean_utilization"
            ),
            "resource_stability": stability,
            "status": "complete",
        },
    )
    print(
        "RESOURCE_ADAPTIVE_BENCHMARK_JSON="
        + json.dumps(marker, ensure_ascii=False, sort_keys=True),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
