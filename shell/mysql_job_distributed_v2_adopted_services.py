"""JOB v2 services that adopt one qualified live planner container.

The planner is never recreated between fresh R0, the debug round, and the
formal-ten launch.  Replay points still use fresh full physical template
copies, while every JOB client runs as a descendant of the frozen node02
client container.  A parallel host-local monitor makes rolling inventory and
foreign-device attribution part of every measured window.
"""

from __future__ import annotations

import base64
import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
from typing import Any, Callable, Mapping, Sequence

from formal_job_continuous_session import FormalJobContinuousSession
from formal_mysql_sgd_orchestration import FormalSGDNotReady
from mysql_algorithm_b_production_services import (
    MYSQL_COST_KEYS,
    MySQLCostAdapter,
    ProductionServices,
)
from shell.manual_mysql_sgd_r0_campaign import (
    Campaign,
    IMAGE,
    remote_ssh_command,
)


HOST = "anonymous@192.0.2.4"
HOSTNAME = "node02"
PROJECT = "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job"
TEMPLATE = PROJECT + "/template"
DEVICE = "/dev/nvme1n1"
MAJOR_MINOR = "259:3"
PLANNER_PORT = 65102
REPLAY_PORT = 65202
CPUS = "18-20,54-56"
MEMS = "1"
CPU_CORES = 1.15
MEMORY_BYTES = 4294967296
PIDS_MAX = 512
READ_BPS = 7340032
WRITE_BPS = 7340032
BUFFER_POOL_BYTES = 3221225472
CLIENT_CPUS = "0-2,36-38"
CLIENT_MEMS = "0"
RECONCILIATION_SHA256 = (
    "96beed0c59d0f86ac8c4b517ce94a5af460be2b14089a3d40583a3ef6813df82"
)
ATTEMPT_RE = re.compile(r"job-v2-[a-z0-9][a-z0-9_.-]{24,95}$")
HEX64_RE = re.compile(r"[0-9a-f]{64}$")
ROLLING_MARKER = "MYSQL_JOB_V2_ROLLING_GATE_SAMPLE_JSON="


class AdoptionError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise AdoptionError(f"{label} is absent or symlinked: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AdoptionError(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AdoptionError(f"{label} must be an object")
    return value


def _config_identity(row: Mapping[str, Any]) -> str:
    return sha_bytes(
        canonical(
            {
                "Config": row.get("Config"),
                "HostConfig": row.get("HostConfig"),
                "Mounts": row.get("Mounts"),
                "Image": row.get("Image"),
                "Created": row.get("Created"),
            }
        )
    )


def _expand_cpuset(value: Any) -> set[int]:
    result: set[int] = set()
    for token in str(value).split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            low, high = [int(item) for item in token.split("-", 1)]
            result.update(range(low, high + 1))
        else:
            result.add(int(token))
    return result


def _rate(rows: Any, device: str) -> int | None:
    for row in rows or []:
        if isinstance(row, Mapping) and row.get("Path") == device:
            return int(row.get("Rate") or 0)
    return None


def validate_handoff_authority(
    config: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    binding = config.get("same_live_planner_authority")
    if not isinstance(binding, Mapping):
        raise AdoptionError("same-live-planner authority binding is absent")
    path = Path(str(binding.get("path") or "")).resolve()
    expected_sha = binding.get("sha256")
    if (
        not path.is_absolute()
        or not isinstance(expected_sha, str)
        or HEX64_RE.fullmatch(expected_sha) is None
        or sha256(path) != expected_sha
    ):
        raise AdoptionError("same-live-planner authority file differs")
    authority = load_object(path, "same-live-planner authority")
    stage = authority.get("stage")
    status = authority.get("status")
    allowed = {
        "fresh_r0": "READY_FRESH_R0_SAME_LIVE_PLANNER",
        "debug": "READY_DEBUG_SAME_LIVE_PLANNER",
        "formal10": "READY_FORMAL10_SAME_LIVE_PLANNER",
    }
    planner = authority.get("planner_identity")
    client = authority.get("client_identity")
    if (
        authority.get("schema_version")
        != "mysql_job_distributed_v2_same_live_planner_handoff_v1"
        or authority.get("formal_scope") != "mysql_only"
        or authority.get("cross_engine_comparison_prohibited") is not True
        or authority.get("postgresql_mutation_prohibited") is not True
        or authority.get("scenario") != "job_memory"
        or authority.get("host_id") != HOSTNAME
        or not isinstance(stage, str)
        or status != allowed.get(stage)
        or authority.get("attempt_token") != binding.get("attempt_token")
        or authority.get("bundle_id") != binding.get("bundle_id")
        or ATTEMPT_RE.fullmatch(str(authority.get("attempt_token") or ""))
        is None
        or authority.get("new_campaign_or_planner_container_prohibited")
        is not True
        or authority.get("qualification_container_adoption_required")
        is not True
        or authority.get("ongoing_rolling_gate_required") is not True
        or authority.get("old_reconciliation_sha256")
        != RECONCILIATION_SHA256
        or authority.get("old_top5_rerun_authorized") is not False
        or not isinstance(
            authority.get("old_top5_attempt_tokens_denylist"), list
        )
        or len(authority["old_top5_attempt_tokens_denylist"]) != 5
        or authority.get("historical_accepted_round_credit") != 0
        or authority.get("resume_old_outer_checkpoint") is not False
        or authority.get("capture_or_r0_or_debug_countable_as_accepted")
        is not False
        or authority.get("accepted_round_authorized") is not False
        or not isinstance(planner, Mapping)
        or set(planner)
        != {
            "container_name",
            "container_id",
            "pid",
            "datadir",
            "database_inspect_config_sha256",
        }
        or not isinstance(client, Mapping)
        or set(client)
        != {
            "container_name",
            "container_id",
            "pid",
            "inspect_config_sha256",
        }
    ):
        raise AdoptionError("same-live-planner authority semantics differ")
    if (
        not isinstance(planner["container_name"], str)
        or not planner["container_name"].endswith("_db")
        or HEX64_RE.fullmatch(str(planner["container_id"])) is None
        or int(planner["pid"]) <= 0
        or not str(planner["datadir"]).startswith(PROJECT + "/")
        or HEX64_RE.fullmatch(
            str(planner["database_inspect_config_sha256"])
        )
        is None
        or not isinstance(client["container_name"], str)
        or not client["container_name"].endswith("_client")
        or HEX64_RE.fullmatch(str(client["container_id"])) is None
        or int(client["pid"]) <= 0
        or HEX64_RE.fullmatch(str(client["inspect_config_sha256"])) is None
    ):
        raise AdoptionError("planner/client immutable identity differs")
    clock = now or datetime.now(timezone.utc)
    try:
        created = datetime.fromisoformat(
            str(authority["created_at_utc"]).replace("Z", "+00:00")
        )
        expires = datetime.fromisoformat(
            str(authority["expires_at_utc"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AdoptionError(f"handoff validity window is invalid: {exc}") from exc
    if (
        created.tzinfo is None
        or expires.tzinfo is None
        or expires <= created
        or (expires - created).total_seconds() > 900
        or clock.astimezone(timezone.utc) >= expires.astimezone(timezone.utc)
    ):
        raise AdoptionError("handoff authority is stale or over-broad")
    if stage == "fresh_r0":
        if (
            authority.get("fresh_r0_authorized") is not True
            or authority.get("formal10_authorized") is not False
            or authority.get("target_accepted_rounds") != 0
            or authority.get("outer_acceptance_authorized") is not False
        ):
            raise AdoptionError("fresh-R0 handoff boundary differs")
    elif stage == "debug":
        if (
            authority.get("formal10_authorized") is not False
            or authority.get("target_accepted_rounds") != 0
            or authority.get("outer_acceptance_authorized") is not False
        ):
            raise AdoptionError("debug handoff boundary differs")
    elif (
        authority.get("formal10_authorized") is not True
        or authority.get("target_accepted_rounds") != 10
        or authority.get("next_accepted_round") != 1
        or authority.get("outer_acceptance_authorized") is not True
    ):
        raise AdoptionError("formal10 handoff boundary differs")
    return authority


def validate_distributed_v2_runtime_binding(
    config: Mapping[str, Any], authority: Mapping[str, Any]
) -> dict[str, Any]:
    binding = config.get("distributed_v2")
    if not isinstance(binding, Mapping):
        raise AdoptionError("distributed-v2 runtime binding is absent")
    required = {
        "local_bundle_path",
        "remote_bundle_path",
        "bundle_manifest_sha256",
        "remote_python",
    }
    if not required.issubset(binding):
        raise AdoptionError("distributed-v2 runtime binding is incomplete")
    local = Path(str(binding["local_bundle_path"])).resolve()
    manifest_path = local / "bundle_manifest.json"
    if (
        local.is_symlink()
        or not local.is_dir()
        or local.name != authority["bundle_id"]
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
        or sha256(manifest_path) != binding["bundle_manifest_sha256"]
    ):
        raise AdoptionError("local distributed-v2 bundle identity differs")
    manifest = load_object(manifest_path, "distributed-v2 bundle manifest")
    files = manifest.get("files")
    if (
        manifest.get("bundle_id") != authority["bundle_id"]
        or not isinstance(files, Mapping)
        or not files
    ):
        raise AdoptionError("distributed-v2 bundle manifest differs")
    expected: set[str] = set()
    for relative, expected_sha in files.items():
        path = local / str(relative)
        if (
            not isinstance(relative, str)
            or not relative
            or relative.startswith("/")
            or ".." in Path(relative).parts
            or HEX64_RE.fullmatch(str(expected_sha)) is None
            or path.is_symlink()
            or not path.is_file()
            or sha256(path) != expected_sha
        ):
            raise AdoptionError(
                f"distributed-v2 bundle closure differs at {relative}"
            )
        expected.add(relative)
    actual = {
        str(path.relative_to(local))
        for path in local.rglob("*")
        if path.is_file()
        and str(path.relative_to(local)) not in {
            "bundle_manifest.json",
            "bundle_manifest.json.sha256",
        }
    }
    if actual != expected:
        raise AdoptionError("distributed-v2 bundle exact entry set differs")
    runtime = load_object(
        local / "distributed_runtime_contract.json",
        "distributed-v2 runtime contract",
    )
    toolchain = runtime.get("toolchain") or {}
    python_manifest_path = local / str(
        toolchain.get("python_tree_manifest") or ""
    )
    python_manifest = load_object(
        python_manifest_path, "Python runtime manifest"
    )
    remote_bundle = str(binding["remote_bundle_path"])
    expected_remote_bundle = str(
        runtime.get("bundle_remote_path_rule") or ""
    ).replace("<bundle_id>", authority["bundle_id"])
    remote_python = str(binding["remote_python"])
    if (
        remote_bundle != expected_remote_bundle
        or Path(remote_bundle).name != authority["bundle_id"]
        or remote_python != toolchain.get("python_runtime")
        or toolchain.get("python_binary_relative") != "bin/python3.11"
        or not remote_python.endswith("/bin/python3.11")
        or (python_manifest.get("artifacts") or {}).get("bin/python3.11")
        != toolchain.get("python_binary_sha256")
        or runtime.get("database_counter_transport")
        != {
            "schema_version":
                "mysql_algorithm_c_database_counter_transport_v1",
            "kind": "exact_client_container_docker_exec_stdout_v1",
            "mysql_binary_in_client": "/usr/bin/mysql",
            "stdout_only": True,
            "host_mysql_cli_required": False,
            "docker_socket_mount_prohibited": True,
            "target_datadir_mount_prohibited": True,
        }
    ):
        raise AdoptionError("manifest-bound remote runtime differs")
    return {
        "status": "PASS_LOCAL_EXACT_CLOSURE_AND_REMOTE_PATH_BINDING",
        "bundle_id": authority["bundle_id"],
        "bundle_manifest_sha256": binding["bundle_manifest_sha256"],
        "remote_bundle_path": remote_bundle,
        "remote_python": remote_python,
        "python_binary_relative": "bin/python3.11",
        "database_counter_transport":
            "exact_client_container_docker_exec_stdout_v1",
        "accepted_round_authorized": False,
    }


def validate_live_database_inspect(
    row: Mapping[str, Any], authority: Mapping[str, Any]
) -> dict[str, Any]:
    planner = authority["planner_identity"]
    host = row.get("HostConfig") or {}
    state = row.get("State") or {}
    ports = ((host.get("PortBindings") or {}).get("3306/tcp") or [])
    if (
        row.get("Id") != planner["container_id"]
        or str(row.get("Name") or "").lstrip("/") != planner["container_name"]
        or state.get("Running") is not True
        or int(state.get("Pid") or 0) != int(planner["pid"])
        or int(row.get("RestartCount") or 0) != 0
        or _config_identity(row)
        != planner["database_inspect_config_sha256"]
        or int(host.get("CpuPeriod") or 0) != 100000
        or int(host.get("CpuQuota") or 0) != 115000
        or _expand_cpuset(host.get("CpusetCpus")) != _expand_cpuset(CPUS)
        or _expand_cpuset(host.get("CpusetMems")) != _expand_cpuset(MEMS)
        or int(host.get("Memory") or 0) != MEMORY_BYTES
        or int(host.get("MemorySwap") or 0) != MEMORY_BYTES
        or int(host.get("PidsLimit") or 0) != PIDS_MAX
        or _rate(host.get("BlkioDeviceReadBps"), DEVICE) != READ_BPS
        or _rate(host.get("BlkioDeviceWriteBps"), DEVICE) != WRITE_BPS
        or {
            (item.get("HostIp"), int(item.get("HostPort") or 0))
            for item in ports
        }
        != {("127.0.0.1", PLANNER_PORT)}
        or not any(
            item.get("Source") == planner["datadir"]
            and item.get("Destination") == "/var/lib/mysql"
            and item.get("RW") is True
            for item in row.get("Mounts") or []
        )
    ):
        raise AdoptionError("live adopted planner readback differs")
    return {
        "verified": True,
        "container_id": row["Id"],
        "pid": int(state["Pid"]),
        "datadir": planner["datadir"],
        "database_inspect_config_sha256": _config_identity(row),
    }


def parse_rolling_gate_window(
    path: Path,
    *,
    attempt_token: str,
    point_token: str,
    start_epoch_ms: int,
    measurement_seconds: int,
) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise AdoptionError("rolling monitor output is absent")
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        if not line.startswith(ROLLING_MARKER):
            continue
        value = json.loads(line[len(ROLLING_MARKER):])
        if (
            not isinstance(value, dict)
            or value.get("schema_version")
            != "mysql_job_distributed_v2_rolling_gate_sample_v1"
            or value.get("attempt_token") != attempt_token
            or value.get("point_token") != point_token
            or value.get("status") != "PASS_SAMPLE"
        ):
            raise AdoptionError("rolling monitor sample authority differs")
        rows.append(value)
    end = start_epoch_ms + measurement_seconds * 1000
    selected = [
        row
        for row in rows
        if start_epoch_ms <= int(row["epoch_ms"]) <= end
    ]
    if len(selected) < measurement_seconds:
        raise AdoptionError(
            "rolling monitor coverage incomplete: "
            f"{len(selected)}/{measurement_seconds}"
        )
    selected.sort(key=lambda row: int(row["epoch_ms"]))
    for prior, current in zip(selected, selected[1:]):
        gap = (int(current["epoch_ms"]) - int(prior["epoch_ms"])) / 1000.0
        if gap <= 0.0 or gap > 1.5:
            raise AdoptionError("rolling monitor sample gap exceeded")
    first_device = selected[0]["client_target_device"]
    if any(row["client_target_device"] != first_device for row in selected):
        raise AdoptionError("client accessed the target MySQL device")
    database_ids = {
        (row["database_container_id"], int(row["database_pid"]))
        for row in selected
    }
    client_ids = {
        (row["client_container_id"], int(row["client_pid"]))
        for row in selected
    }
    if len(database_ids) != 1 or len(client_ids) != 1:
        raise AdoptionError("DB/client identity changed in rolling window")
    return {
        "schema_version":
            "mysql_job_distributed_v2_rolling_gate_window_v1",
        "status": "PASS",
        "attempt_token": attempt_token,
        "point_token": point_token,
        "measurement_seconds": measurement_seconds,
        "sample_count": len(selected),
        "first_epoch_ms": int(selected[0]["epoch_ms"]),
        "last_epoch_ms": int(selected[-1]["epoch_ms"]),
        "database_identity": list(database_ids)[0],
        "client_identity": list(client_ids)[0],
        "client_target_device_delta_bytes": 0,
        "full_host_inventory_passed_every_sample": True,
        "accepted_round_authorized": False,
    }


class RemoteClientFormalJobSession(FormalJobContinuousSession):
    """Bridge remote client-tmpfs events/stop control into local evidence."""

    def __init__(
        self,
        *,
        remote_event_reader: Callable[[], str],
        remote_stop_writer: Callable[[int], None],
        rolling_gate: Callable[[int, int], Mapping[str, Any]],
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.remote_event_reader = remote_event_reader
        self.remote_stop_writer = remote_stop_writer
        self.rolling_gate = rolling_gate

    def _completed_event_rows(self) -> list[Mapping[str, Any]]:
        raw = self.remote_event_reader()
        temporary = self.events_path.with_name(
            f".{self.events_path.name}.remote.{os.getpid()}"
        )
        temporary.write_text(raw, encoding="utf-8")
        os.replace(temporary, self.events_path)
        return super()._completed_event_rows()

    def _publish_stop(self, measurement_seconds: int) -> None:
        self.remote_stop_writer(measurement_seconds)
        super()._publish_stop(measurement_seconds)

    def observation(self, measurement_seconds: int) -> Mapping[str, Any]:
        result = dict(super().observation(measurement_seconds))
        start = int(self._session_marker()["measurement_start_epoch_ms"])
        result["rolling_full_host_gate"] = dict(
            self.rolling_gate(start, measurement_seconds)
        )
        return result


class ExactClientContainerSQLClient:
    """SQL client which never opens a controller tunnel or host mysql CLI."""

    def __init__(self, campaign: "_V2ClientCampaignMixin") -> None:
        self.campaign = campaign

    def execute(self, sql: str) -> str:
        if not self.campaign.client_defaults_provisioned:
            raise AdoptionError("exact client defaults have not been provisioned")
        client = str(self.campaign.authority["client_identity"]["container_name"])
        return self.campaign._client_remote(
            "docker",
            "exec",
            client,
            "/usr/bin/mysql",
            "--defaults-file=%s" % self.campaign.remote_client_defaults,
            "--protocol=TCP",
            "--host=127.0.0.1",
            "--port=%d" % int(self.campaign.lane.remote_port),
            "--user=root",
            "--database=%s" % self.campaign.lane.database,
            "--batch",
            "--raw",
            "--skip-column-names",
            "--execute",
            sql,
            timeout=30,
        )


class _V2ClientCampaignMixin:
    authority: Mapping[str, Any]
    v2_config: Mapping[str, Any]
    rolling_process: subprocess.Popen[str] | None
    rolling_stdout: Any
    rolling_stderr: Any
    rolling_path: Path
    point_token: str

    def _init_v2(
        self, authority: Mapping[str, Any], config: Mapping[str, Any]
    ) -> None:
        self.authority = copy.deepcopy(dict(authority))
        self.v2_config = copy.deepcopy(dict(config))
        self.rolling_process = None
        self.rolling_stdout = None
        self.rolling_stderr = None
        self.rolling_path = self.output / "rolling_full_host_gate.jsonl"
        self.point_token = self.run_id.lower()
        self.remote_client_root = f"/measurement/{self.point_token}"
        self.remote_client_defaults = (
            self.remote_client_root + "/mysql.cnf"
        )
        self.remote_event_path = self.remote_client_root + "/events.jsonl"
        self.remote_stop_path = self.remote_client_root + "/stop"
        self.client_defaults_provisioned = False
        self._v2_last_rolling_epoch_ms = -1
        self._v2_last_resource_stream: Mapping[str, Any] | None = None

    def _client_remote(self, *args: str, timeout: int = 120) -> str:
        result = subprocess.run(
            remote_ssh_command(self.lane, *args),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            raise AdoptionError(
                "remote client command failed: "
                f"rc={result.returncode} stderr={result.stderr[-2000:]}"
            )
        return result.stdout

    def provision_remote_client_defaults(self) -> None:
        if self.client_defaults_provisioned:
            return
        local = self.output / "private/mysql.cnf"
        if local.is_symlink() or not local.is_file():
            raise AdoptionError("point MySQL defaults file is absent")
        encoded = base64.b64encode(local.read_bytes()).decode("ascii")
        client = self.authority["client_identity"]["container_name"]
        script = (
            f"set -eu; umask 077; mkdir -p {self.remote_client_root}; "
            f"test ! -e {self.remote_client_defaults}; "
            f"printf '%s' {encoded} | base64 -d >"
            f"{self.remote_client_defaults}; "
            f"test ! -e {self.remote_event_path}; "
            f"test ! -e {self.remote_stop_path}"
        )
        self._client_remote(
            "docker", "exec", client, "/bin/sh", "-ceu", script
        )
        self.client_defaults_provisioned = True

    def start_tunnel(self) -> None:
        """The v2 client is host-networked; an SSH port-forward is forbidden."""
        self.tunnel = None

    def start_sampler(self) -> None:
        self.provision_remote_client_defaults()
        bundle = str(self.v2_config["distributed_v2"]["remote_bundle_path"])
        python = str(self.v2_config["distributed_v2"]["remote_python"])
        monitor = (
            bundle
            + "/qualification/mysql_job_distributed_v2_rolling_monitor.py"
        )
        planner = self.authority["planner_identity"]
        client = self.authority["client_identity"]
        self.rolling_stdout = self.rolling_path.open("w", encoding="utf-8")
        self.rolling_stderr = (
            self.output / "rolling_full_host_gate.stderr.log"
        ).open("w", encoding="utf-8")
        self.rolling_process = subprocess.Popen(
            remote_ssh_command(
                self.lane,
                python,
                monitor,
                "--bundle",
                bundle,
                "--database-container",
                self.container,
                "--database-container-id",
                str(self.live_container_id),
                "--database-pid",
                str(self.live_container_pid),
                "--database-inspect-sha256",
                str(self.live_inspect_sha256),
                "--client-container",
                str(client["container_name"]),
                "--client-container-id",
                str(client["container_id"]),
                "--client-pid",
                str(client["pid"]),
                "--client-inspect-sha256",
                str(client["inspect_config_sha256"]),
                "--attempt-token",
                str(self.authority["attempt_token"]),
                "--point-token",
                self.point_token,
                "--client-defaults-file",
                self.remote_client_defaults,
                "--duration-seconds",
                "660",
            ),
            text=True,
            stdout=self.rolling_stdout,
            stderr=self.rolling_stderr,
            start_new_session=True,
        )

    def _refresh_rolling_samples(self) -> None:
        if self.rolling_stdout is not None:
            self.rolling_stdout.flush()
        if self.rolling_process is None:
            raise AdoptionError("rolling full-host monitor was not started")
        if self.rolling_process.poll() is not None and self.rolling_process.returncode != 0:
            raise AdoptionError("rolling full-host monitor exited before sampling")
        if not self.rolling_path.is_file():
            return
        rows = []
        for line in self.rolling_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            if not line.startswith(ROLLING_MARKER):
                continue
            try:
                row = json.loads(line[len(ROLLING_MARKER):])
            except json.JSONDecodeError as exc:
                raise AdoptionError("rolling monitor emitted invalid JSON") from exc
            if not isinstance(row, Mapping) or row.get("status") != "PASS_SAMPLE":
                raise AdoptionError("rolling monitor sample differs")
            rows.append(row)
        for row in rows:
            epoch = int(row.get("epoch_ms") or 0)
            if epoch <= self._v2_last_rolling_epoch_ms:
                continue
            stream = row.get("resource_stream")
            counters = row.get("database_counters")
            if not isinstance(stream, Mapping) or not isinstance(counters, Mapping):
                raise AdoptionError("rolling sample streams are absent")
            prior = self._v2_last_resource_stream
            self._v2_last_rolling_epoch_ms = epoch
            mutable_stream = dict(stream)
            mutable_stream["_epoch_ms"] = epoch
            self._v2_last_resource_stream = mutable_stream
            completed = counters.get("Com_select")
            if not isinstance(completed, int) or completed < 0:
                raise AdoptionError("exact-client Com_select counter is absent")
            database = {
                "epoch_ms": epoch,
                "sampler_index": 0,
                "completed_total": completed,
                "source": "exact_client_docker_exec_stdout_database_status_counter",
                "attribution_scope": "dedicated_mysql_instance",
                "digest_set_sha256": None,
                "counter_name": "Com_select",
                "sampler_mode": "exact_client_docker_exec_per_second",
            }
            if prior is None:
                continue
            old_epoch = int(prior.get("_epoch_ms") or 0)
            if old_epoch <= 0 or epoch <= old_epoch:
                continue
            old_cpu = prior["cpu_usage_and_throttle_ratio"]["usage_usec"]
            new_cpu = stream["cpu_usage_and_throttle_ratio"]["usage_usec"]
            old_memory = prior["memory_current_swap_and_pressure"]
            new_memory = stream["memory_current_swap_and_pressure"]
            old_devices = prior[
                "per_declared_device_bytes_iops_latency_queue_and_utilization"
            ]["cgroup_devices"].get(MAJOR_MINOR, {})
            new_devices = stream[
                "per_declared_device_bytes_iops_latency_queue_and_utilization"
            ]["cgroup_devices"].get(MAJOR_MINOR, {})
            wall = (epoch - old_epoch) / 1000.0
            if wall <= 0:
                continue
            read_rate = (int(new_devices.get("read_bytes", 0)) - int(old_devices.get("read_bytes", 0))) / wall
            write_rate = (int(new_devices.get("write_bytes", 0)) - int(old_devices.get("write_bytes", 0))) / wall
            if read_rate < 0 or write_rate < 0 or int(new_cpu) < int(old_cpu):
                raise AdoptionError("rolling resource counter regressed")
            resource = {
                "epoch_ms": epoch,
                "cpu_utilization_ratio": (int(new_cpu) - int(old_cpu)) / 1e6 / wall / CPU_CORES,
                "memory_utilization_ratio": max(0, int(new_memory["memory_current_bytes"])) / MEMORY_BYTES,
                "memory_current_diagnostic_ratio": int(new_memory["memory_current_bytes"]) / MEMORY_BYTES,
                "io_utilization_ratio": (read_rate + write_rate) / (READ_BPS + WRITE_BPS),
                "io_read_bytes_per_second": read_rate,
                "io_write_bytes_per_second": write_rate,
                "io_definition": "(read_throughput+write_throughput)/(read_cap+write_cap)",
            }
            with self.rows_lock:
                if self.database_rows and completed < self.database_rows[-1]["completed_total"]:
                    raise AdoptionError("database counter regressed")
                self.resource_rows.append(resource)
                self.database_rows.append(database)
                self.database_attempt_rows.append(
                    {"epoch_ms": epoch, "sampler_index": 0, "succeeded": True, "error_count": 0}
                )

    def resource_samples(self) -> Sequence[Mapping[str, Any]]:
        self._refresh_rolling_samples()
        with self.rows_lock:
            return [dict(row) for row in self.resource_rows]

    def database_samples(self) -> Sequence[Mapping[str, Any]]:
        self._refresh_rolling_samples()
        with self.rows_lock:
            return [dict(row) for row in self.database_rows]

    def _remote_events(self) -> str:
        client = self.authority["client_identity"]["container_name"]
        return self._client_remote(
            "docker",
            "exec",
            client,
            "/bin/sh",
            "-ceu",
            f"test ! -e {self.remote_event_path} || cat {self.remote_event_path}",
        )

    def _remote_stop(self, measurement_seconds: int) -> None:
        client = self.authority["client_identity"]["container_name"]
        self._client_remote(
            "docker",
            "exec",
            client,
            "/bin/sh",
            "-ceu",
            (
                f"test ! -e {self.remote_stop_path}; "
                f"printf '%s\\n' {int(measurement_seconds)} "
                f">{self.remote_stop_path}"
            ),
        )

    def _rolling_gate(
        self, start_epoch_ms: int, measurement_seconds: int
    ) -> Mapping[str, Any]:
        if self.rolling_stdout is not None:
            self.rolling_stdout.flush()
        if self.rolling_process is None:
            raise AdoptionError("rolling full-host monitor was not started")
        if (
            self.rolling_process.poll() is not None
            and self.rolling_process.returncode != 0
        ):
            raise AdoptionError(
                "rolling full-host monitor exited before decision"
            )
        return parse_rolling_gate_window(
            self.rolling_path,
            attempt_token=str(self.authority["attempt_token"]),
            point_token=self.point_token,
            start_epoch_ms=start_epoch_ms,
            measurement_seconds=measurement_seconds,
        )

    def stop_rolling_monitor(self) -> Mapping[str, Any]:
        process = self.rolling_process
        if process is not None and process.poll() is None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except OSError:
                    pass
                process.wait(timeout=10)
        for stream in (self.rolling_stdout, self.rolling_stderr):
            if stream is not None and not stream.closed:
                stream.close()
        rows = 0
        if self.rolling_path.is_file():
            rows = sum(
                line.startswith(ROLLING_MARKER)
                for line in self.rolling_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
            )
        return {
            "monitor_stopped": True,
            "sample_rows": rows,
            "output": str(self.rolling_path),
        }

    def job_session(
        self, protocol: Mapping[str, Any]
    ) -> RemoteClientFormalJobSession:
        bundle = str(self.v2_config["distributed_v2"]["remote_bundle_path"])
        python = str(self.v2_config["distributed_v2"]["remote_python"])
        workload = (
            bundle + "/qualification/formal_job_multiquery_workload.py"
        )
        query_dir = bundle + "/qualification/sql/job"
        client = self.authority["client_identity"]["container_name"]

        def command(_local_stop: Path) -> Sequence[str]:
            return remote_ssh_command(
                self.lane,
                "docker",
                "exec",
                "-i",
                "--workdir",
                self.remote_client_root,
                client,
                python,
                workload,
                "--host",
                "127.0.0.1",
                "--port",
                str(self.lane.remote_port),
                "--database",
                self.lane.database,
                "--query-dir",
                query_dir,
                "--defaults-extra-file",
                self.remote_client_defaults,
                "--event-path",
                self.remote_event_path,
                "--stop-file",
                self.remote_stop_path,
                "--concurrency",
                "1",
                "--weights",
                "10,10,10,10,10,10,30,10,7.5",
                "--seed",
                "4508333",
                "--maximum-measurement-seconds",
                str(self.measurement_seconds),
            )

        return RemoteClientFormalJobSession(
            output=self.output,
            command_builder=command,
            resource_samples=self.resource_samples,
            database_qps_samples=self.database_samples,
            measurement_protocol=protocol,
            cwd=None,
            required_query_names=(
                "01_job_1a.sql",
                "02_job_5a.sql",
                "03_job_9a.sql",
                "04_job_12a.sql",
                "05_job_16a.sql",
                "06_job_20a.sql",
                "07_job_26a.sql",
                "08_job_33a.sql",
                "09_job_sensitive_title_cast_role5_movie100k.sql",
            ),
            remote_event_reader=self._remote_events,
            remote_stop_writer=self._remote_stop,
            rolling_gate=self._rolling_gate,
        )


class AdoptedPlannerCampaign(_V2ClientCampaignMixin, Campaign):
    def __init__(
        self,
        *args: Any,
        authority: Mapping[str, Any],
        config: Mapping[str, Any],
        owner: "AdoptedProductionServices",
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._init_v2(authority, config)
        self.owner = owner
        planner = authority["planner_identity"]
        self.container = str(planner["container_name"])
        self.remote_data = str(planner["datadir"])
        self.remote_root = str(Path(self.remote_data).parent)
        self.live_container_id = str(planner["container_id"])
        self.live_container_pid = int(planner["pid"])
        self.live_inspect_sha256 = str(
            planner["database_inspect_config_sha256"]
        )

    def prepare_remote(self) -> None:
        result = self.remote(
            f"set -eu; docker inspect {self.container}", timeout=60
        )
        try:
            rows = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AdoptionError(f"live planner inspect is invalid: {exc}") from exc
        if not isinstance(rows, list) or len(rows) != 1:
            raise AdoptionError("live planner inspect cardinality differs")
        readback = validate_live_database_inspect(rows[0], self.authority)
        local_defaults = Path(
            str(
                self.v2_config["same_live_planner_authority"][
                    "controller_defaults_file"
                ]
            )
        ).resolve()
        expected_sha = self.v2_config["same_live_planner_authority"][
            "controller_defaults_sha256"
        ]
        if (
            local_defaults.is_symlink()
            or not local_defaults.is_file()
            or sha256(local_defaults) != expected_sha
        ):
            raise AdoptionError("controller private defaults identity differs")
        private = self.output / "private"
        private.mkdir(parents=True, exist_ok=True)
        target = private / "mysql.cnf"
        target.write_bytes(local_defaults.read_bytes())
        target.chmod(0o600)
        (self.output / "resource_readback.json").write_text(
            json.dumps(
                {
                    **readback,
                    "requested": {
                        "cpu_quota_cores": CPU_CORES,
                        "cpuset_cpus": CPUS,
                        "cpuset_mems": MEMS,
                        "memory_bytes": MEMORY_BYTES,
                        "memory_swap_bytes": MEMORY_BYTES,
                        "pids_max": PIDS_MAX,
                        "io_read_bps": READ_BPS,
                        "io_write_bps": WRITE_BPS,
                        "innodb_buffer_pool_bytes": BUFFER_POOL_BYTES,
                    },
                    "same_live_planner_adopted": True,
                    "new_container_created": False,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.provision_remote_client_defaults()

    def apply_candidate_costs(self) -> None:
        if not self.candidate_costs:
            return
        adapter = MySQLCostAdapter(self.owner.client_for(self))
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
                float(effective[name]), float(requested), rel_tol=5e-6, abs_tol=1e-8
            )
        }
        if mismatches:
            raise AdoptionError("candidate MySQL cost readback differs")
        (self.output / "candidate_cost_readback.json").write_text(
            json.dumps(
                {
                    "schema_version": "mysql_candidate_cost_readback_v1",
                    "requested_active_costs": self.candidate_costs,
                    "effective_all_costs": {
                        name: effective[name] for name in MYSQL_COST_KEYS
                    },
                    "flush_optimizer_costs": True,
                    "fresh_connection_readback": True,
                    "transport": "exact_client_docker_exec_stdout_v1",
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def apply_candidate_costs(self) -> None:
        super().apply_candidate_costs()
        if self.candidate_costs:
            return
        adapter = MySQLCostAdapter(
            self.owner.client_for(self)
        )
        unexpected = sorted(
            item.name
            for item in adapter.snapshot()
            if item.name in MYSQL_COST_KEYS and item.raw_value is not None
        )
        if unexpected:
            raise AdoptionError(
                f"adopted planner cost overrides are not NULL: {unexpected}"
            )


class V2ReplayCampaign(_V2ClientCampaignMixin, Campaign):
    def __init__(
        self,
        *args: Any,
        authority: Mapping[str, Any],
        config: Mapping[str, Any],
        owner: "AdoptedProductionServices",
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._init_v2(authority, config)
        self.owner = owner
        self.live_container_id = ""
        self.live_container_pid = 0
        self.live_inspect_sha256 = ""

    def template_copy_command(self) -> str:
        bundle = str(self.v2_config["distributed_v2"]["remote_bundle_path"])
        python = str(self.v2_config["distributed_v2"]["remote_python"])
        python_root = str(Path(python).parents[1])
        helper = bundle + "/qualification/mysql_job_physical_copy_v1.py"
        lane = self.lane
        return f"""docker run --rm --network none --cpus 0.5 \
  --cpuset-cpus {lane.cpuset.split(',')[0]} --cpuset-mems {lane.mems} \
  --memory 536870912 --memory-swap 536870912 --pids-limit 128 \
  --mount type=bind,src={lane.template},dst=/template,readonly \
  --mount type=bind,src={self.remote_root},dst={self.remote_root} \
  --mount type=bind,src={bundle},dst={bundle},readonly \
  --mount type=bind,src={python_root},dst={python_root},readonly \
  --entrypoint {python} {IMAGE} {helper} --source /template \
  --destination {self.remote_data}"""

    def prepare_remote(self) -> None:
        super().prepare_remote()
        prepare_log = (
            self.output / "remote_prepare.stdout.log"
        ).read_text(encoding="utf-8")
        physical_rows = []
        for line in prepare_log.splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(row, dict)
                and row.get("schema_version")
                == "mysql_job_physical_copy_evidence_v1"
            ):
                physical_rows.append(row)
        if len(physical_rows) != 1:
            raise AdoptionError(
                "replay physical-copy evidence cardinality differs"
            )
        physical = physical_rows[0]
        fiemap = physical.get("fiemap") or {}
        if (
            physical.get("status")
            != "PASS_BYTE_COPY_AND_FIEMAP_NONSHARING"
            or physical.get("copy_api") != "os.read/os.write"
            or physical.get("cp_used") is not False
            or physical.get("reflink_api_used") is not False
            or physical.get("copy_file_range_used") is not False
            or physical.get("sendfile_used") is not False
            or fiemap.get("fiemap_extent_shared_absent") is not True
            or fiemap.get(
                "source_destination_physical_extent_overlap_absent"
            )
            is not True
        ):
            raise AdoptionError("replay physical-copy audit failed")
        (self.output / "physical_copy_evidence.json").write_text(
            json.dumps(physical, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        readback = load_object(
            self.output / "resource_readback.json",
            "replay resource readback",
        )
        self.live_container_id = str(readback["container_id"])
        inspect = json.loads(
            self.remote(f"docker inspect {self.container}", timeout=60).stdout
        )[0]
        self.live_container_pid = int(
            (inspect.get("State") or {}).get("Pid") or 0
        )
        self.live_inspect_sha256 = _config_identity(inspect)
        self.provision_remote_client_defaults()


class AdoptedProductionServices(ProductionServices):
    def __init__(self, config: Mapping[str, Any]) -> None:
        mutable = copy.deepcopy(dict(config))
        authority = validate_handoff_authority(mutable)
        runtime_binding = validate_distributed_v2_runtime_binding(
            mutable, authority
        )
        super().__init__(mutable)
        self.authority = authority
        self.runtime_binding = runtime_binding
        self.lane = replace(
            self.lane,
            host=HOST,
            hostname=HOSTNAME,
            project=PROJECT,
            template=TEMPLATE,
            device=DEVICE,
            cpuset=CPUS,
            mems=MEMS,
            cpu_cores=CPU_CORES,
            memory_bytes=MEMORY_BYTES,
            pids=PIDS_MAX,
            read_bps=READ_BPS,
            write_bps=WRITE_BPS,
            remote_port=PLANNER_PORT,
            buffer_pool_bytes=BUFFER_POOL_BYTES,
        )
        self.replay_lane = replace(self.lane, remote_port=REPLAY_PORT)

    def client_for(
        self, campaign: Campaign
    ) -> ExactClientContainerSQLClient:
        if not isinstance(campaign, _V2ClientCampaignMixin):
            raise AdoptionError("legacy controller MySQL client is forbidden")
        return ExactClientContainerSQLClient(campaign)

    def _new_campaign(
        self,
        lane: Any,
        role: str,
        costs: Mapping[str, float] | None = None,
    ) -> Campaign:
        run_id = self._run_id(role)
        common = {
            "candidate_costs": costs,
            "measurement_seconds": int(
                self.config.get("measurement_seconds", 600)
            ),
            "minimum_measurement_seconds": (
                int(self.config.get("measurement_seconds", 600))
                if role == "r0"
                else int(self.config.get("minimum_measurement_seconds", 180))
            ),
            "minimum_client_completions": int(
                self.config.get("minimum_client_completions", 0)
            ),
            "require_r0_profile_gate": role == "r0",
            "authority": self.authority,
            "config": self.config,
            "owner": self,
        }
        campaign_type = (
            AdoptedPlannerCampaign
            if role in {"r0", "resume_planner"}
            else V2ReplayCampaign
        )
        return campaign_type(
            lane, self.points / run_id, run_id, **common
        )

    def finalizer(
        self, continuous: Mapping[str, Any], context: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        campaign = self.active
        result = super().finalizer(continuous, context)
        if isinstance(campaign, _V2ClientCampaignMixin):
            result = dict(result)
            result["rolling_monitor"] = campaign.stop_rolling_monitor()
        return result

    def recovery(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        campaign = self.active
        if isinstance(campaign, _V2ClientCampaignMixin):
            campaign.stop_rolling_monitor()
        return super().recovery(context)

    def cleanup(self) -> Mapping[str, Any]:
        errors: list[str] = []
        active = self.active
        if active is not None:
            try:
                if isinstance(active, _V2ClientCampaignMixin):
                    active.stop_rolling_monitor()
                active.cleanup()
            except BaseException as exc:
                errors.append(f"active: {type(exc).__name__}: {exc}")
            self.active = None
        planner = self.planner
        if planner is not None:
            try:
                if isinstance(planner, _V2ClientCampaignMixin):
                    planner.stop_rolling_monitor()
                self._stop_sampling(planner)
                process = getattr(planner.session, "process", None)
                if process is not None and process.poll() is None:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    process.wait(timeout=120)
                if planner.tunnel is not None and planner.tunnel.poll() is None:
                    planner.tunnel.terminate()
                    planner.tunnel.wait(timeout=10)
                live = json.loads(
                    planner.remote(
                        f"docker inspect {planner.container}", timeout=60
                    ).stdout
                )[0]
                validate_live_database_inspect(live, self.authority)
            except BaseException as exc:
                errors.append(f"planner: {type(exc).__name__}: {exc}")
            self.planner = None
        return {
            "zero_pressure_verified": not errors,
            "errors": errors,
            "same_live_planner_preserved": not errors,
            "new_planner_container_created": False,
            "accepted_round_credit": 0,
        }


def build(config: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        return AdoptedProductionServices(config).bundle()
    except AdoptionError as exc:
        raise FormalSGDNotReady(f"NOT_READY: {exc}") from exc
