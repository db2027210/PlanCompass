#!/usr/bin/env python3
"""Materialize or execute one fail-closed fresh-b235 Retry9 qualification."""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import stat
import subprocess
import time
from typing import Any, Mapping


TARGET_ID = "b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47"
TARGET_SHA = "386f59b23f20eb7be59665c42c44fe2453512bf2112d850e12df6b398a16f439"
RENDERER_ID = "a496ad36e202ed7ded0f4910fa16a9690bcc590d7d33de4f574fe7aba63e4226"
RENDERER_SHA = "f82f0d04d8f59b67d687f245874db6eeaf9c937d65e4add12464dbf0a3cf600a"
SUPPORT_ID = "a070e44ed4ca1acb0f5b4e0fe03386c6fc0dd43736d0f477cdd17a006bd465b6"
SUPPORT_SHA = "a529c3aa35884314893afdd50b093a251dbf31c6a83d9ff797df052c960a7dce"
MATERIALIZER_ID = "819640d73e8b85a4547b2f4a09b5477eac4bcd19f425d46cac9764b01e3a57af"
MATERIALIZER_SHA = "fef5a7cbd3a93a5bd190036240492ffa5dabe495a8a701f77bc3e05bca978480"
WRAPPER_ID = "4849dd8600122990f77130206630a78b789ba3da6770c6754f940226c2d8eb67"
WRAPPER_SHA = "9fee0bfe0f0258c395464da121732842b1f8e7c929f270d829f8e0e6cd6b801e"
RUNTIME_ROOT = Path("/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729")
RUNTIME_TREE = "python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
IMAGE_REF = "mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b"
IMAGE_ID = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
ATTEMPT_RE = re.compile(r"tpch-v2-[a-z0-9][a-z0-9_.-]{24,95}$")
MUTEX = Path("/run/lock/ra-mysql-node04-tpch-tpcc-fourflow.lock")
FOURFLOW_UNIT = "ra-mysql-da455-fourflow-*.service"
FOURFLOW_PROCESS_MARKERS = (
    "mysql_tpcc_v16_fresh_four_flow_controller_v1.py",
    "mysql_tpch_tpcc_da455_systemd219_four_flow_v1.py",
    "launch_and_wait_joint.sh",
)
DOCKER_CLI = ["/usr/bin/docker", "-H", "unix:///run/ra-docker-mysql-tpch-v1.sock"]
STALE_ATTEMPT = "tpch-v2-20260803t022500z-afbe6badff313374deadbeef"
STALE_BUNDLE = "ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4"
STALE_CONTAINERS = {
    "a28bcc5e6fd284cd1138d91ca73d12e7009c999148bb099b30f3f79029053257":
        "ra_mysql_tpch_v2_tpch_v2_20260803t022500z_afbe6badff313374deadbee_db",
    "41a51161cd0718faf66380b36eeacea28cd9af21c400a9d384febc1212e411d4":
        "ra_mysql_tpch_v2_tpch_v2_20260803t022500z_afbe6badff313374deadbee_client",
}
RETRY8_UNIT = "ra-mysql-tpch-retry5-971eda5d6b76480d.service"
FAILED_RETRY9_UNIT = (
    "ra-mysql-tpch-bootstrap-"
    "tpch-v2-20260803t074729z-b235retry9aa511ee4b5b5.service"
)
TPCH_ACTIVE_MARKERS = (
    "mysql_tpch_tpcc_shared_collector_entry",
    "mysql_tpch_retry5_systemd219_completion_v1.py",
    "mysql_tpch_distributed_v2_authority.py",
)


class OneClickError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode("utf-8")


def bytecode_env() -> dict[str, str]:
    return dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sealed(path: Path, label: str) -> dict[str, Any]:
    path = Path(path).resolve(strict=True)
    sidecar = Path(str(path) + ".sha256")
    if (path.is_symlink() or not path.is_file() or sidecar.is_symlink()
            or not sidecar.is_file()
            or sidecar.read_text(encoding="ascii")
            != f"{sha256(path)}  {path.name}\n"):
        raise OneClickError(label + " seal differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise OneClickError(label + " must be an object")
    return value


def seal_new(path: Path, value: Mapping[str, Any], mode: int = 0o600) -> None:
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if any(item.exists() or item.is_symlink() for item in (path, sidecar)):
        raise OneClickError("refusing overwrite: " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = canonical(value)
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(8))
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sidecar.write_text(hashlib.sha256(payload).hexdigest() + "  " + path.name + "\n",
                       encoding="ascii")
    os.chmod(path, mode)
    os.chmod(sidecar, mode)


def write_new(path: Path, payload: bytes, mode: int) -> None:
    if path.exists() or path.is_symlink():
        raise OneClickError("refusing overwrite: " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, mode)


def verify_bundle(path: Path, bundle_id: str, manifest_sha: str,
                  label: str) -> dict[str, Any]:
    path = Path(path).resolve(strict=True)
    if stat.S_IMODE(path.stat().st_mode) & 0o222:
        raise OneClickError(label + " is mutable: .")
    manifest = sealed(path / "bundle_manifest.json", label + " manifest")
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    actual: dict[str, str] = {}
    for candidate in sorted(path.rglob("*")):
        relative = candidate.relative_to(path).as_posix()
        if candidate.name == "__pycache__" or candidate.suffix in (".pyc", ".pyo"):
            raise OneClickError(label + " contains prohibited bytecode: " + relative)
        if candidate.is_symlink():
            raise OneClickError(label + " contains a symlink: " + relative)
        if stat.S_IMODE(candidate.stat().st_mode) & 0o222:
            raise OneClickError(label + " is mutable: " + relative)
        if candidate.is_file() and relative not in (
                "bundle_manifest.json", "bundle_manifest.json.sha256"):
            actual[relative] = sha256(candidate)
    if (path.name != bundle_id or manifest.get("bundle_id") != bundle_id
            or sha256(path / "bundle_manifest.json") != manifest_sha
            or hashlib.sha256(canonical(unsigned)).hexdigest() != bundle_id
            or actual != manifest.get("files")
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False):
        raise OneClickError(label + " identity/scope differs")
    return manifest


def package_paths() -> dict[str, Path]:
    root = Path(__file__).resolve().parents[1]
    return {
        "root": root,
        "renderer": root / "dependencies/bootstrap_renderer" / RENDERER_ID,
        "support": root / "dependencies/support" / SUPPORT_ID,
        "materializer": root / "dependencies/materializer" / MATERIALIZER_ID,
        "wrapper": root / "dependencies/wrapper" / WRAPPER_ID,
        "failure": root / "evidence/mysql_tpch_retry8_failure_exact_v1.json",
    }


def verify_package() -> dict[str, Path]:
    paths = package_paths()
    verify_bundle(paths["root"], paths["root"].name,
                  sha256(paths["root"] / "bundle_manifest.json"), "one-click")
    verify_bundle(paths["renderer"], RENDERER_ID, RENDERER_SHA, "renderer")
    verify_bundle(paths["support"], SUPPORT_ID, SUPPORT_SHA, "support")
    verify_bundle(paths["materializer"], MATERIALIZER_ID, MATERIALIZER_SHA,
                  "materializer")
    verify_bundle(paths["wrapper"], WRAPPER_ID, WRAPPER_SHA, "wrapper")
    failure = sealed(paths["failure"], "retry8 failure")
    expected = {
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "error": "retry5 launch failed rc=2 stderr=NOT_READY: debug qualification workload did not exit gracefully within 120s\n",
        "qualification_attempt_id": "tpch-qualification-retry5-20260803T062320Z-9b149cc799b77818cb102a7016525096",
        "schema_version": "mysql_tpch_retry5_systemd219_failure_v1",
        "status": "FAILED_ZERO_CREDIT",
        "unit": "ra-mysql-tpch-retry5-971eda5d6b76480d.service",
    }
    if failure != expected:
        raise OneClickError("retry8 exact zero-credit evidence differs")
    return paths


def run_checked(argv: list[str], label: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, timeout=timeout, env=bytecode_env())
    if completed.returncode:
        raise OneClickError(f"{label} failed rc={completed.returncode} stderr={completed.stderr[-1600:]}")
    return completed


def attempt_token() -> str:
    return ("tpch-v2-" + datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz").lower()
            + "-b235retry9" + secrets.token_hex(6))


def expected_bootstrap_argv(spec: Mapping[str, Any], target: Path,
                            spec_path: Path, token: Path,
                            evidence: Path) -> list[str]:
    attempt = str(spec["attempt_token"])
    return [
        "systemd-run", "--quiet", "--unit",
        "ra-mysql-tpch-bootstrap-" + attempt + ".service",
        "--service-type=oneshot", "--remain-after-exit",
        spec["runtime"]["target_root"] + "/bin/python3.11", "-B",
        str(target / "mysql_tpch_distributed_v2_authority.py"),
        "--spec", str(spec_path), "--execute-bootstrap", "--output",
        str(evidence), "--main-agent-token-file", str(token),
    ]


def materialize(args: argparse.Namespace) -> int:
    paths = verify_package()
    runtime_root = args.runtime_root.resolve(strict=True)
    if runtime_root != RUNTIME_ROOT:
        raise OneClickError("node04 runtime root differs")
    attempt = args.attempt_token or attempt_token()
    if ATTEMPT_RE.fullmatch(attempt) is None:
        raise OneClickError("attempt token differs")
    target = runtime_root / "bundles" / TARGET_ID
    verify_bundle(target, TARGET_ID, TARGET_SHA, "runtime target")
    output = args.output_dir
    attempt_root = runtime_root / "attempts" / attempt
    if (not output.is_absolute() or output.exists() or output.is_symlink()
            or attempt_root.exists() or attempt_root.is_symlink()):
        raise OneClickError("fresh output/attempt root required")
    output.mkdir(parents=True, mode=0o700)
    token = output / "bootstrap/main_agent.token"
    write_new(token, (secrets.token_hex(48) + "\n").encode("ascii"), 0o600)
    spec_path = output / "bootstrap/bootstrap_spec.json"
    renderer = paths["renderer"] / "controller/render_mysql_tpch_sql_native_percentile_bootstrap_spec_v1.py"
    render_argv = [sys.executable, "-B", str(renderer), "--bundle-manifest",
                   str(target / "bundle_manifest.json"), "--mysql-image-reference",
                   IMAGE_REF, "--mysql-image-id", IMAGE_ID,
                   "--client-image-reference", IMAGE_REF, "--client-image-id",
                   IMAGE_ID, "--main-agent-token-file", str(token),
                   "--attempt-token", attempt, "--valid-minutes", "60",
                   "--output", str(spec_path)]
    run_checked(render_argv, "bootstrap spec renderer")
    spec = sealed(spec_path, "bootstrap spec")
    evidence = attempt_root / "bootstrap_evidence.json"
    boot_plan_path = output / "bootstrap/bootstrap_plan.json"
    boot_materializer = paths["root"] / "controller/mysql_tpch_bootstrap_plan_materializer_no_bytecode_v1.py"
    boot_materialize_argv = [sys.executable, "-B", str(boot_materializer), "--spec",
                             str(spec_path), "--bundle-path", str(target),
                             "--remote-spec", str(spec_path), "--remote-token-file",
                             str(token), "--remote-output", str(evidence),
                             "--help-fixture", str(paths["renderer"] / "fixtures/node04_systemd_run_help_fixture_v1.json"),
                             "--output", str(boot_plan_path)]
    run_checked(boot_materialize_argv, "bootstrap plan materializer")
    boot_plan = sealed(boot_plan_path, "bootstrap plan")
    expected = expected_bootstrap_argv(spec, target, spec_path, token, evidence)
    if boot_plan.get("argv") != expected:
        raise OneClickError("BOOT_PLAN.argv differs from the sole executable argv")
    controller_token = output / "controller/controller.token"
    controller_secret = secrets.token_hex(48)
    write_new(controller_token, (controller_secret + "\n").encode("ascii"), 0o600)
    retry9_materials = runtime_root / "retry9_materializations" / attempt
    retry9_systemd = runtime_root / "retry9_systemd" / attempt
    plan = {
        "schema_version": "mysql_tpch_retry9_one_click_plan_v1",
        "status": "SEALED_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "attempt_token": attempt,
        "target": str(target),
        "support": str(paths["support"]),
        "materializer": str(paths["materializer"]),
        "wrapper": str(paths["wrapper"]),
        "retry8_failure": str(paths["failure"]),
        "bootstrap_spec": str(spec_path),
        "bootstrap_spec_sha256": sha256(spec_path),
        "bootstrap_token": str(token),
        "bootstrap_token_sha256": sha256(token),
        "bootstrap_plan": str(boot_plan_path),
        "bootstrap_plan_sha256": sha256(boot_plan_path),
        "bootstrap_argv": boot_plan["argv"],
        "bootstrap_argv_source": "sealed_BOOT_PLAN.argv_only",
        "bootstrap_evidence": str(evidence),
        "attempt_root": str(attempt_root),
        "qualification_command": str(attempt_root / "qualification_solo.command.json"),
        "blkio_authority": str(attempt_root / "blkio_throttle_authority.json"),
        "blkio_supplement": str(attempt_root / "blkio_throttle_authority.supplement.json"),
        "mysql_defaults": str(attempt_root / "credentials/mysql.cnf"),
        "retry9_materials": str(retry9_materials),
        "retry9_systemd": str(retry9_systemd),
        "stale_reconcile_receipt": str(output / "completion/stale_retry8_reconcile.json"),
        "post_failure_reconcile_receipt": str(output / "completion/current_attempt_reconcile.json"),
        "stale_attempt": STALE_ATTEMPT,
        "stale_bundle_id": STALE_BUNDLE,
        "stale_container_ids": sorted(STALE_CONTAINERS),
        "retry8_failed_unit": RETRY8_UNIT,
        "failed_retry9_bootstrap_unit": FAILED_RETRY9_UNIT,
        "mutex": str(MUTEX),
        "tpcc_fourflow_unit_pattern": FOURFLOW_UNIT,
        "pid_outputs_required": ["bootstrap", "controller", "workload"],
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }
    plan_path = output / "controller/retry9.plan.json"
    seal_new(plan_path, plan, 0o400)
    now = time.time()
    authority = {
        "schema_version": "mysql_tpch_retry9_one_click_controller_authority_v1",
        "authorized_by": "main_agent",
        "plan_sha256": sha256(plan_path),
        "token_sha256": hashlib.sha256(controller_secret.encode("ascii")).hexdigest(),
        "not_before_epoch": now - 1.0,
        "expires_epoch": now + float(args.valid_seconds),
        "formal_scope": "mysql_only",
        "accepted_round_authorized": False,
    }
    authority_path = output / "controller/controller.authority.json"
    seal_new(authority_path, authority, 0o400)
    launch = output / "launch_and_wait_retry9.sh"
    argv = [sys.executable, "-B", str(Path(__file__).resolve()), "execute", "--plan",
            str(plan_path), "--authority", str(authority_path), "--token",
            str(controller_token)]
    write_new(launch, ("#!/bin/sh\nset -eu\nexport PYTHONDONTWRITEBYTECODE=1\nexec " + shlex.join(argv) + "\n").encode(), 0o500)
    verify_package()
    verify_bundle(target, TARGET_ID, TARGET_SHA, "runtime target post-materialize")
    print(json.dumps({"status": "SEALED_RETRY9_ONE_CLICK_NOT_EXECUTED",
                      "attempt_token": attempt, "plan": str(plan_path),
                      "launch_and_wait": str(launch),
                      "bootstrap_argv_exact_match": True,
                      "accepted_rounds_credit": 0}, sort_keys=True))
    return 0


def systemd_state(unit: str) -> dict[str, str]:
    result = subprocess.run(["/usr/bin/systemctl", "show", unit,
                             "--property=LoadState", "--property=ActiveState",
                             "--property=SubState", "--property=ExecMainStatus",
                             "--property=Result", "--property=MainPID",
                             "--property=ControlGroup", "--property=Transient"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=20, env=bytecode_env())
    if result.returncode:
        raise OneClickError("systemctl show failed: " + result.stderr[-800:])
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def docker_inventory() -> list[dict[str, Any]]:
    listed = run_checked([*DOCKER_CLI, "ps", "-a", "--no-trunc", "--quiet"],
                         "dedicated daemon inventory", 30)
    identifiers = listed.stdout.split()
    if not identifiers:
        return []
    inspected = run_checked([*DOCKER_CLI, "inspect", *identifiers],
                            "dedicated daemon inspect", 30)
    value = json.loads(inspected.stdout)
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise OneClickError("dedicated daemon inspect JSON differs")
    return value


def validate_owned_row(row: Mapping[str, Any], attempt: str,
                       bundle_id: str) -> tuple[str, str, int]:
    identifier = str(row.get("Id") or "")
    name = str(row.get("Name") or "").lstrip("/")
    config = row.get("Config") or {}
    labels = config.get("Labels") or {}
    state = row.get("State") or {}
    if (labels.get("ra.attempt") != attempt
            or labels.get("ra.bundle_id") != bundle_id
            or labels.get("ra.owner") != "mysql_algorithm_c_tpch_v2"
            or labels.get("ra.scenario") != "tpch_memory"
            or config.get("Image") != IMAGE_ID
            or state.get("Running") is not True
            or int(state.get("Pid") or 0) <= 1):
        raise OneClickError("dedicated daemon container ownership differs: " + name)
    return identifier, name, int(state["Pid"])


def assert_stale_retry8_is_quiescent() -> None:
    for unit in (RETRY8_UNIT, FAILED_RETRY9_UNIT):
        state = systemd_state(unit)
        if (state.get("ActiveState") != "failed"
                or state.get("SubState") != "failed"
                or state.get("Result") != "exit-code"
                or state.get("ExecMainStatus") != "2"
                or int(state.get("MainPID") or 0) != 0):
            raise OneClickError("stale TPC-H unit state differs: " + unit)
    processes = subprocess.run(["/bin/ps", "-eo", "pid=,args="],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, timeout=20, env=bytecode_env())
    if processes.returncode:
        raise OneClickError("TPC-H process inventory failed")
    active = []
    for line in processes.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2 and int(fields[0]) != os.getpid() and any(
                marker in fields[1] for marker in TPCH_ACTIVE_MARKERS):
            active.append(line.strip())
    if active:
        raise OneClickError("TPC-H controller/workload remains active: " + active[0])


def assert_stale_container_processes(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        identifier, name, pid = validate_owned_row(row, STALE_ATTEMPT, STALE_BUNDLE)
        completed = run_checked([*DOCKER_CLI, "top", identifier,
                                 "-eo", "pid,ppid,args"],
                                "stale container process inventory", 30)
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if len(lines) != 2:
            raise OneClickError("stale TPC-H container has an active exec: " + name)
        fields = lines[1].split(None, 2)
        command = fields[2] if len(fields) == 3 else ""
        expected = ("mysqld --bind-address=127.0.0.1 --port=65103"
                    if name.endswith("_db") else
                    "/usr/bin/coreutils --coreutils-prog-shebang=sleep /usr/bin/sleep 2147483647")
        if len(fields) != 3 or int(fields[0]) != pid or not command.startswith(expected):
            raise OneClickError("stale TPC-H container process identity differs: " + name)


def remove_exact_rows(rows: list[dict[str, Any]], attempt: str,
                      bundle_id: str, expected: Mapping[str, str] | None,
                      reason: str) -> dict[str, Any]:
    observed: dict[str, tuple[str, int]] = {}
    for row in rows:
        identifier, name, pid = validate_owned_row(row, attempt, bundle_id)
        observed[identifier] = (name, pid)
    if expected is not None and ({key: value[0] for key, value in observed.items()}
                                 != dict(expected)):
        raise OneClickError("stale Retry8 exact container set differs")
    removed = []
    for identifier, (name, pid) in sorted(observed.items(), key=lambda item: item[1][0]):
        run_checked([*DOCKER_CLI, "stop", "--time", "120", identifier],
                    "stop exact stale TPC-H container", 150)
        run_checked([*DOCKER_CLI, "rm", identifier],
                    "remove exact stale TPC-H container", 30)
        removed.append({"container_id": identifier, "name": name,
                        "host_pid_before": pid})
    if docker_inventory():
        raise OneClickError("dedicated daemon is not empty after exact reconciliation")
    return {"schema_version": "mysql_tpch_retry9_stale_daemon_reconcile_v1",
            "status": "PASS_EXACT_TPCH_DAEMON_RECONCILED",
            "reason": reason, "stale_attempt": attempt,
            "stale_bundle_id": bundle_id, "containers": removed,
            "dedicated_daemon_empty_after": True,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "accepted_round_authorized": False, "accepted_rounds_credit": 0}


def reconcile_stale_retry8(receipt: Path) -> dict[str, Any]:
    rows = docker_inventory()
    if not rows:
        value = {"schema_version": "mysql_tpch_retry9_stale_daemon_reconcile_v1",
                 "status": "PASS_DEDICATED_DAEMON_ALREADY_EMPTY",
                 "reason": "pre_bootstrap_retry8_reconcile",
                 "stale_attempt": STALE_ATTEMPT, "stale_bundle_id": STALE_BUNDLE,
                 "containers": [], "dedicated_daemon_empty_after": True,
                 "formal_scope": "mysql_only",
                 "cross_engine_comparison_prohibited": True,
                 "postgresql_mutation_prohibited": True,
                 "accepted_round_authorized": False, "accepted_rounds_credit": 0}
    else:
        assert_stale_retry8_is_quiescent()
        assert_stale_container_processes(rows)
        value = remove_exact_rows(rows, STALE_ATTEMPT, STALE_BUNDLE,
                                  STALE_CONTAINERS,
                                  "pre_bootstrap_retry8_reconcile")
    seal_new(receipt, value, 0o400)
    return value


def reconcile_failed_current_attempt(plan: Mapping[str, Any], receipt: Path) -> None:
    if receipt.exists():
        return
    rows = docker_inventory()
    if rows:
        spec = sealed(Path(str(plan["bootstrap_spec"])), "bootstrap spec for cleanup")
        names = {str(spec["containers"]["database"]),
                 str(spec["containers"]["client"])}
        observed_names = {str(row.get("Name") or "").lstrip("/") for row in rows}
        if observed_names != names:
            raise OneClickError("current failed-attempt container set differs")
        value = remove_exact_rows(rows, str(plan["attempt_token"]), TARGET_ID,
                                  None, "post_failure_current_attempt_reconcile")
        seal_new(receipt, value, 0o400)
    else:
        value = {"schema_version": "mysql_tpch_retry9_stale_daemon_reconcile_v1",
                 "status": "PASS_DEDICATED_DAEMON_ALREADY_EMPTY",
                 "reason": "post_failure_current_attempt_reconcile",
                 "stale_attempt": str(plan["attempt_token"]),
                 "stale_bundle_id": TARGET_ID, "containers": [],
                 "dedicated_daemon_empty_after": True,
                 "formal_scope": "mysql_only",
                 "cross_engine_comparison_prohibited": True,
                 "postgresql_mutation_prohibited": True,
                 "accepted_round_authorized": False, "accepted_rounds_credit": 0}
        seal_new(receipt, value, 0o400)


def pid_identity(pid: int, role: str) -> dict[str, Any]:
    if pid <= 1:
        raise OneClickError(role + " PID is invalid")
    stat_text = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
    close = stat_text.rfind(")")
    fields = stat_text[close + 2:].split()
    if len(fields) < 20:
        raise OneClickError(role + " /proc stat differs")
    cmdline = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    return {"role": role, "pid": pid, "starttime_ticks": int(fields[19]),
            "cmdline_sha256": hashlib.sha256(cmdline).hexdigest(),
            "cmdline": cmdline.replace(b"\0", b" ").decode("utf-8", "replace").strip()}


def direct_children(pid: int) -> list[int]:
    path = Path("/proc") / str(pid) / "task" / str(pid) / "children"
    try:
        return [int(value) for value in path.read_text(encoding="ascii").split()]
    except FileNotFoundError:
        return []


def acquire_mutex() -> None:
    try:
        MUTEX.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise OneClickError("TPC-H/TPC-C fourflow mutex is occupied") from exc
    try:
        units = subprocess.run(["/usr/bin/systemctl", "list-units", "--all",
                                "--no-legend", "--plain", FOURFLOW_UNIT],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, timeout=20, env=bytecode_env())
        if units.returncode or any(line.split()[2:4] in (["active", "running"],
                                                         ["activating", "start"])
                                   for line in units.stdout.splitlines() if line.split()):
            raise OneClickError("TPC-C fourflow unit is active/unknown")
        processes = subprocess.run(["/bin/ps", "-eo", "pid=,args="],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, timeout=20, env=bytecode_env())
        if processes.returncode or any(marker in processes.stdout
                                       for marker in FOURFLOW_PROCESS_MARKERS):
            raise OneClickError("TPC-C fourflow controller/workload is active or unknown")
    except BaseException:
        MUTEX.rmdir()
        raise


def wait_bootstrap(unit: str, deadline: float, pids: dict[str, Any]) -> None:
    while time.time() < deadline:
        state = systemd_state(unit)
        main_pid = int(state.get("MainPID") or 0)
        if main_pid > 1 and "bootstrap" not in pids:
            pids["bootstrap"] = pid_identity(main_pid, "bootstrap")
        if (state.get("ActiveState") == "active" and state.get("SubState") == "exited"
                and state.get("ExecMainStatus") == "0" and state.get("Result") == "success"):
            if "bootstrap" not in pids:
                raise OneClickError("bootstrap PID was not observed")
            return
        if state.get("ActiveState") in ("failed", "inactive"):
            raise OneClickError("bootstrap unit failed: " + json.dumps(state, sort_keys=True))
        time.sleep(1)
    raise OneClickError("bootstrap unit timed out")


def observe_retry9(unit: str, process: subprocess.Popen[str], deadline: float,
                   pids: dict[str, Any]) -> tuple[str, str]:
    while time.time() < deadline and process.poll() is None:
        try:
            state = systemd_state(unit)
        except OneClickError:
            time.sleep(1)
            continue
        controller = int(state.get("MainPID") or 0)
        if controller > 1 and "controller" not in pids:
            pids["controller"] = pid_identity(controller, "controller")
        if controller > 1 and "workload" not in pids:
            for child in direct_children(controller):
                identity = pid_identity(child, "workload")
                if "mysql_tpch_tpcc_shared_collector_entry_current_v1.py" in identity["cmdline"]:
                    pids["workload"] = identity
                    break
        time.sleep(1)
    if process.poll() is None:
        raise OneClickError("Retry9 launch_and_wait timed out")
    stdout, stderr = process.communicate(timeout=10)
    if process.returncode:
        raise OneClickError(f"Retry9 launch_and_wait failed rc={process.returncode} stderr={stderr[-1600:]}")
    if set(pids) != {"bootstrap", "controller", "workload"}:
        raise OneClickError("required bootstrap/controller/workload PID was not observed")
    return stdout, stderr


def execute(args: argparse.Namespace) -> int:
    paths = verify_package()
    plan_path = args.plan.resolve(strict=True)
    plan = sealed(plan_path, "Retry9 plan")
    authority = sealed(args.authority, "Retry9 controller authority")
    token_path = args.token.resolve(strict=True)
    if token_path.is_symlink() or stat.S_IMODE(token_path.stat().st_mode) != 0o600:
        raise OneClickError("controller token mode differs")
    token = token_path.read_text(encoding="ascii").strip()
    now = time.time()
    if (authority.get("plan_sha256") != sha256(plan_path)
            or authority.get("token_sha256") != hashlib.sha256(token.encode()).hexdigest()
            or not float(authority.get("not_before_epoch", 0)) <= now < float(authority.get("expires_epoch", 0))
            or plan.get("status") != "SEALED_NOT_EXECUTED"
            or plan.get("stale_attempt") != STALE_ATTEMPT
            or plan.get("stale_bundle_id") != STALE_BUNDLE
            or plan.get("stale_container_ids") != sorted(STALE_CONTAINERS)
            or plan.get("retry8_failed_unit") != RETRY8_UNIT
            or plan.get("failed_retry9_bootstrap_unit") != FAILED_RETRY9_UNIT
            or plan.get("accepted_round_authorized") is not False):
        raise OneClickError("controller authority/plan differs or expired")
    completion_dir = plan_path.parent.parent / "completion"
    completion = completion_dir / "retry9.completion.json"
    failure = completion_dir / "retry9.failure.json"
    pids: dict[str, Any] = {}
    mutex_owned = False
    try:
        acquire_mutex()
        mutex_owned = True
        target = Path(plan["target"])
        verify_bundle(target, TARGET_ID, TARGET_SHA, "runtime target")
        stale_reconcile = reconcile_stale_retry8(
            Path(plan["stale_reconcile_receipt"])
        )
        spec_path = Path(plan["bootstrap_spec"]).resolve(strict=True)
        spec = sealed(spec_path, "bootstrap spec")
        bootstrap_token = Path(plan["bootstrap_token"]).resolve(strict=True)
        if (sha256(spec_path) != plan["bootstrap_spec_sha256"]
                or sha256(bootstrap_token) != plan["bootstrap_token_sha256"]
                or bootstrap_token.is_symlink()
                or stat.S_IMODE(bootstrap_token.stat().st_mode) != 0o600):
            raise OneClickError("bootstrap spec/token changed after materialization")
        boot_plan_path = Path(plan["bootstrap_plan"]).resolve(strict=True)
        boot_plan = sealed(boot_plan_path, "bootstrap plan")
        expected = expected_bootstrap_argv(
            spec, target, spec_path, bootstrap_token,
            Path(plan["bootstrap_evidence"]),
        )
        if (sha256(boot_plan_path) != plan["bootstrap_plan_sha256"]
                or boot_plan.get("argv") != plan.get("bootstrap_argv")
                or boot_plan.get("argv") != expected
                or plan.get("bootstrap_argv_source") != "sealed_BOOT_PLAN.argv_only"):
            raise OneClickError("BOOT_PLAN changed or argv equality differs")
        bootstrap_argv = boot_plan["argv"]
        started = subprocess.run(bootstrap_argv, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, timeout=60,
                                 env=bytecode_env())
        if started.returncode:
            raise OneClickError("bootstrap systemd-run failed: " + started.stderr[-1200:])
        wait_bootstrap(str(boot_plan["unit"]), time.time() + 900, pids)
        evidence = Path(plan["bootstrap_evidence"])
        sealed(evidence, "bootstrap evidence")
        runtime_python = RUNTIME_ROOT / "runtime" / RUNTIME_TREE / "bin/python3.11"
        command = Path(plan["qualification_command"])
        run_checked([str(runtime_python), "-B", str(target / "mysql_tpch_distributed_v2_authority.py"),
                     "--spec", plan["bootstrap_spec"], "--bootstrap", str(evidence),
                     "--emit-command-spec", "qualification_solo", "--output", str(command)],
                    "fresh command materializer", 120)
        blkio = Path(plan["blkio_authority"])
        blkio_value = sealed(blkio, "blkio authority")
        database = blkio_value.get("database_container")
        if not isinstance(database, str) or not database:
            raise OneClickError("fresh database container is absent")
        supplement = Path(plan["blkio_supplement"])
        run_checked([str(runtime_python), "-B", str(paths["support"] / "qualification/mysql_tpch_legacy_blkio_supplement_v1.py"),
                     "--bundle", str(target), "--legacy-authority", str(blkio),
                     "--container", database, "--output", str(supplement)],
                    "fresh blkio supplement", 120)
        run_checked([str(runtime_python), "-B", str(paths["materializer"] / "materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py"),
                     "--bundle", str(target), "--support-bundle", str(paths["support"]),
                     "--successor-command", str(command), "--retry8-failure-evidence",
                     str(paths["failure"]), "--supplement", str(supplement),
                     "--mysql-defaults-file", plan["mysql_defaults"], "--output-dir",
                     plan["retry9_materials"], "--valid-seconds", "1800"],
                    "Retry9 workload materializer", 180)
        retry9_materialization = Path(plan["retry9_materials"]) / "retry5.materialization.json"
        run_checked([str(runtime_python), "-B", str(paths["wrapper"] / "controller/mysql_tpch_retry5_systemd219_completion_v1.py"),
                     "materialize", "--retry5-bundle", str(paths["materializer"]),
                     "--retry5-materialization", str(retry9_materialization),
                     "--collector-bundle", str(paths["support"]), "--output-dir",
                     plan["retry9_systemd"]], "Retry9 systemd materializer", 180)
        retry9_plan = sealed(Path(plan["retry9_systemd"]) / "retry5.plan.json", "Retry9 systemd plan")
        launcher = Path(plan["retry9_systemd"]) / "launch_and_wait.sh"
        process = subprocess.Popen(["/bin/sh", str(launcher)], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True,
                                   env=bytecode_env())
        stdout, stderr = observe_retry9(str(retry9_plan["unit"]), process,
                                        time.time() + 900, pids)
        receipt = Path(retry9_plan["receipt"])
        receipt_value = sealed(receipt, "Retry9 completion")
        if receipt_value.get("status") != "PASS_RETRY5_PACED_181_SAMPLES_COMPLETED_ZERO_CREDIT":
            raise OneClickError("Retry9 inner completion status differs")
        verify_package()
        verify_bundle(target, TARGET_ID, TARGET_SHA, "runtime target post-execute")
        pid_outputs = {role: pids[role] for role in ("bootstrap", "controller", "workload")}
        result = {"schema_version": "mysql_tpch_retry9_one_click_completion_v1",
                  "status": "PASS_FRESH_B235_RETRY9_COMPLETED_ZERO_CREDIT",
                  "attempt_token": plan["attempt_token"], "pids": pid_outputs,
                  "bootstrap_plan_sha256": sha256(boot_plan_path),
                  "bootstrap_argv_exact_match": True,
                  "inner_completion": str(receipt),
                  "inner_completion_sha256": sha256(receipt),
                  "launcher_stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
                  "launcher_stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
                  "package_closure_unchanged_after_execute": True,
                  "target_closure_unchanged_after_execute": True,
                  "stale_retry8_reconcile": plan["stale_reconcile_receipt"],
                  "stale_retry8_reconcile_sha256": sha256(
                      Path(plan["stale_reconcile_receipt"])),
                  "stale_retry8_reconcile_status": stale_reconcile["status"],
                  "tpcc_fourflow_mutex_held_for_full_window": True,
                  "accepted_round_authorized": False, "accepted_rounds_credit": 0}
        seal_new(completion, result, 0o400)
        print(json.dumps(result, sort_keys=True))
        return 0
    except BaseException as exc:
        closure_error = None
        reconcile_error = None
        reconcile_receipt = Path(str(plan.get("post_failure_reconcile_receipt", "")))
        try:
            reconcile_failed_current_attempt(plan, reconcile_receipt)
        except BaseException as reconcile_exc:
            reconcile_error = str(reconcile_exc)
        try:
            verify_package()
            verify_bundle(Path(plan["target"]), TARGET_ID, TARGET_SHA,
                          "runtime target post-failure")
        except BaseException as closure_exc:
            closure_error = str(closure_exc)
        if not failure.exists():
            inner_failure = Path(str(plan.get("retry9_systemd", ""))) / "completion/retry5.failure.json"
            pid_outputs = {role: pids.get(role)
                           for role in ("bootstrap", "controller", "workload")}
            value = {"schema_version": "mysql_tpch_retry9_one_click_failure_v1",
                     "status": "FAILED_ZERO_CREDIT", "error": str(exc),
                     "post_failure_closure_error": closure_error,
                     "post_failure_closure_passed": closure_error is None,
                     "post_failure_reconcile_error": reconcile_error,
                     "post_failure_reconcile_passed": reconcile_error is None,
                     "post_failure_reconcile": (str(reconcile_receipt)
                         if reconcile_receipt.exists() else None),
                     "post_failure_reconcile_sha256": (sha256(reconcile_receipt)
                         if reconcile_receipt.exists() else None),
                     "attempt_token": plan.get("attempt_token"), "pids": pid_outputs,
                     "inner_failure": str(inner_failure) if inner_failure.exists() else None,
                     "inner_failure_sha256": sha256(inner_failure) if inner_failure.exists() else None,
                     "accepted_round_authorized": False, "accepted_rounds_credit": 0}
            seal_new(failure, value, 0o400)
            sys.stderr.write(json.dumps({"status": "FAILED_ZERO_CREDIT",
                                         "failure": str(failure),
                                         "pids": pid_outputs},
                                        sort_keys=True) + "\n")
        raise
    finally:
        if mutex_owned:
            MUTEX.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    material = modes.add_parser("materialize")
    material.add_argument("--runtime-root", type=Path, default=RUNTIME_ROOT)
    material.add_argument("--output-dir", type=Path, required=True)
    material.add_argument("--attempt-token")
    material.add_argument("--valid-seconds", type=int, default=2400,
                          choices=range(1800, 3601))
    execution = modes.add_parser("execute")
    execution.add_argument("--plan", type=Path, required=True)
    execution.add_argument("--authority", type=Path, required=True)
    execution.add_argument("--token", type=Path, required=True)
    args = parser.parse_args()
    return materialize(args) if args.mode == "materialize" else execute(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OneClickError, OSError, UnicodeError, ValueError, KeyError,
            json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n")
        raise SystemExit(2)
