#!/usr/bin/env python3
"""Build, authorize, or execute the node02 JOB/XP four-flow gate.

Execution is explicit and requires a short-lived main-agent authority whose
token and plan hashes match.  Solo flows are serialized; the paired flows are
started concurrently.  Missing evidence, nonzero children, reused tokens, or
non-reciprocal peer bindings fail closed and no dynamic gate is written.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import time
from typing import Any, Mapping


MODES = ("qualification_solo", "qualification_paired")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mysql_xp_successor_binding_v1 as xp_binding
TARGETS = {"job": "job_memory", "xp": "hybench_xp_io"}
BUNDLE_IDS = {
    "job": {"6b4844bcdcc7ada8dfd76c6676f87c98dc382aa76979504d3edc65baaab52dd7"},
    "xp": {
        "f43f33b5d60cf6de2fe63f96852e6a46ea3b132b5d52039a1e60021771dc9262",
        "e91031f5af956af87904a240fcf6ce6673d2511dfc66f1a6337f36698133050d",
        "f2dd55ea6b5ea4db704aa533bddc9161b378c30735b9606a092efccc1315179f",
        "f6528795e2eabc84c7d29ff8d387bc50ffd0bbb055e4fc0987da5aa8626717c2",
        "21d1a853548ebdff573d18a82c7a124b810b5656e3e3a330146f87d3e0b460ad",
        "ba68d4a57f7ebb03f8e9c662250492aac7341757c66c56256665c41b5ee3a77e",
    },
}
ADAPTER_KINDS = {"job": "sql_query_native", "xp": "hybench_xp"}


class FourFlowError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_sealed(path: Path, label: str) -> dict[str, Any]:
    sidecar = Path(str(path) + ".sha256")
    if path.is_symlink() or not path.is_file():
        raise FourFlowError("%s is absent or unsafe" % label)
    expected = "%s  %s\n" % (sha256(path), path.name)
    if sidecar.is_symlink() or not sidecar.is_file() or sidecar.read_text(encoding="ascii") != expected:
        raise FourFlowError("%s sidecar differs" % label)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FourFlowError("%s must be an object" % label)
    return value


def atomic(path: Path, value: Mapping[str, Any], mode: int = 0o600) -> None:
    payload = canonical(value) + b"\n"
    sidecar = Path(str(path) + ".sha256")
    if any(item.exists() or item.is_symlink() for item in (path, sidecar)):
        raise FourFlowError("refusing to overwrite four-flow material")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)
    sidecar.write_text("%s  %s\n" % (hashlib.sha256(payload).hexdigest(), path.name), encoding="ascii")
    sidecar.chmod(mode)


def token(path: Path) -> str:
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise FourFlowError("flow token must be regular 0600")
    value = path.read_text(encoding="ascii").strip()
    if len(value) < 32 or any(character.isspace() for character in value):
        raise FourFlowError("flow token differs")
    return value


def validate_launch(path: Path, backend: str, mode: str, dispatch_sha: str) -> dict[str, Any]:
    launch = load_sealed(path, "%s %s launch" % (backend, mode))
    command_path = Path(str(launch.get("command_spec") or ""))
    authority_path = Path(str(launch.get("main_agent_authority") or ""))
    token_path = Path(str(launch.get("main_agent_token_file") or ""))
    command = load_sealed(command_path, "qualification command")
    authority = load_sealed(authority_path, "qualification authority")
    secret = token(token_path)
    expected_scenario = TARGETS[backend]
    expected_bundles = BUNDLE_IDS[backend]
    launch_bundle = Path(str(launch.get("bundle") or "")).name
    if backend == "xp":
        binding_path = Path(str(launch.get("xp_successor_binding") or ""))
        binding = xp_binding.validate_binding(
            binding_path, Path(str(launch.get("bundle") or ""))
        )
        if launch.get("xp_successor_binding_sha256") != sha256(binding_path):
            raise FourFlowError("XP successor binding identity differs")
    if (
        launch.get("status") != "MATERIALIZED_NOT_EXECUTED"
        or launch.get("formal_scope") != "mysql_only"
        or launch.get("cross_engine_comparison_prohibited") is not True
        or launch.get("postgresql_mutation_prohibited") is not True
        or launch.get("scenario_backend") != backend
        or launch_bundle not in expected_bundles
        or launch.get("mode") != mode
        or launch.get("shared_dispatch_sha256") != dispatch_sha
        or launch.get("capture_authorized") is not False
        or launch.get("formal_r0_authorized") is not False
        or launch.get("accepted_round_authorized") is not False
        or command.get("mode") != mode
        or command.get("bundle_id") != launch_bundle
        or (command.get("workload_metric_adapter") or {}).get("kind") != ADAPTER_KINDS[backend]
        or authority.get("mode") != mode
        or authority.get("qualification_attempt_id") != command.get("qualification_attempt_id")
        or authority.get("command_spec_sha256") != sha256(command_path)
        or authority.get("main_agent_token_sha256") != hashlib.sha256(secret.encode("utf-8")).hexdigest()
        or authority.get("accepted_round_authorized") is not False
        or not isinstance(launch.get("container"), str)
        or not isinstance(launch.get("mysql_client_container"), str)
        or not Path(str(launch.get("output") or "")).is_absolute()
    ):
        raise FourFlowError("%s %s launch semantics differ" % (backend, mode))
    if mode == "qualification_paired":
        peer = command.get("paired_peer_workload") or {}
        if (
            peer.get("scenario") == expected_scenario
            or peer.get("scenario") not in set(TARGETS.values())
            or not launch.get("peer_readback")
            or not launch.get("peer_container")
        ):
            raise FourFlowError("paired peer binding differs")
    elif command.get("paired_peer_workload") is not None:
        raise FourFlowError("solo flow binds a peer")
    launch["_command"] = command
    launch["_token_sha256"] = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return launch


def command(dispatch: Path, launch: Mapping[str, Any]) -> list[str]:
    collector = {
        "job": "mysql_job_distributed_live_readback_qualification_v2.py",
        "xp": "mysql_distributed_live_readback_qualification_v1.py",
    }[launch["scenario_backend"]]
    argv = [
        sys.executable, str(dispatch), "--scenario-backend", launch["scenario_backend"],
        "--backend-path", str(Path(str(launch["bundle"])) / "qualification" / collector),
        "--" + str(launch["mode"]).replace("qualification_", "qualification-"),
        "--bundle", str(launch["bundle"]), "--container", str(launch["container"]),
        "--mysql-client-container", str(launch["mysql_client_container"]),
        "--mysql-binary-in-client", "/usr/bin/mysql",
        "--mysql-defaults-file", str(launch["mysql_defaults_file"]),
        "--output", str(launch["output"]),
        "--main-agent-token-file", str(launch["main_agent_token_file"]),
        "--main-agent-authority", str(launch["main_agent_authority"]),
        "--command-spec", str(launch["command_spec"]),
    ]
    if launch["mode"] == "qualification_paired":
        argv.extend(["--peer-readback", str(launch["peer_readback"]), "--peer-container", str(launch["peer_container"])])
    if launch["scenario_backend"] == "xp":
        argv[6:6] = ["--xp-successor-binding", str(launch["xp_successor_binding"])]
    return argv


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    dispatch = args.shared_dispatch.resolve(strict=True)
    dispatch_sha = sha256(dispatch)
    paths = {
        "A_solo": args.job_solo_launch,
        "A_paired": args.job_paired_launch,
        "B_solo": args.xp_solo_launch,
        "B_paired": args.xp_paired_launch,
    }
    definitions = {
        "A_solo": ("job", "qualification_solo"),
        "A_paired": ("job", "qualification_paired"),
        "B_solo": ("xp", "qualification_solo"),
        "B_paired": ("xp", "qualification_paired"),
    }
    rows = {name: validate_launch(paths[name], *definitions[name], dispatch_sha) for name in paths}
    ids = {name: row["_command"]["qualification_attempt_id"] for name, row in rows.items()}
    if len(set(ids.values())) != 4 or len({row["_token_sha256"] for row in rows.values()}) != 4:
        raise FourFlowError("four-flow attempts/tokens must be unique")
    if rows["B_solo"].get("xp_successor_binding_sha256") != rows["B_paired"].get("xp_successor_binding_sha256"):
        raise FourFlowError("XP solo/paired successor bindings differ")
    if (
        rows["A_paired"]["_command"]["paired_peer_workload"].get("qualification_attempt_id") != ids["B_paired"]
        or rows["B_paired"]["_command"]["paired_peer_workload"].get("qualification_attempt_id") != ids["A_paired"]
    ):
        raise FourFlowError("paired flow bindings are not reciprocal")
    return {
        "schema_version": "mysql_job_xp_four_flow_execution_plan_v1",
        "status": "SEALED_NOT_AUTHORIZED_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "shared_dispatch": str(dispatch),
        "shared_dispatch_sha256": dispatch_sha,
        "sequence": ["A_solo", "B_solo", ["A_paired", "B_paired"], "compare_bidirectional"],
        "flows": {name: str(paths[name].resolve()) for name in paths},
        "attempt_ids": ids,
        "job_attempt_token": args.job_attempt_token,
        "xp_attempt_token": args.xp_attempt_token,
        "xp_successor_binding_sha256": rows["B_solo"].get("xp_successor_binding_sha256"),
        "job_gate_output": str(args.job_gate_output.resolve()),
        "xp_gate_output": str(args.xp_gate_output.resolve()),
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "accepted_round_authorized": False,
    }


def authorize(plan_path: Path, authority_path: Path, token_path: Path, ttl: int) -> None:
    if ttl < 600 or ttl > 3600:
        raise FourFlowError("four-flow authority TTL must be 600..3600 seconds")
    plan = load_sealed(plan_path, "four-flow plan")
    if plan.get("status") != "SEALED_NOT_AUTHORIZED_NOT_EXECUTED":
        raise FourFlowError("four-flow plan status differs")
    if token_path.exists() or token_path.is_symlink():
        raise FourFlowError("refusing to overwrite four-flow token")
    secret = secrets.token_urlsafe(48)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(secret + "\n", encoding="ascii")
    token_path.chmod(0o600)
    now = datetime.now(timezone.utc)
    atomic(authority_path, {
        "schema_version": "mysql_job_xp_main_agent_four_flow_authority_v1",
        "status": "AUTHORIZED_DEBUG_QUALIFICATION_ONLY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "plan_sha256": sha256(plan_path),
        "main_agent_token_sha256": hashlib.sha256(secret.encode("utf-8")).hexdigest(),
        "not_before_epoch": now.timestamp() - 1.0,
        "expires_epoch": (now + timedelta(seconds=ttl)).timestamp(),
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    })


def execute(plan_path: Path, authority_path: Path, token_path: Path) -> dict[str, Any]:
    plan = load_sealed(plan_path, "four-flow plan")
    authority = load_sealed(authority_path, "four-flow authority")
    secret = token(token_path)
    now = time.time()
    if (
        authority.get("status") != "AUTHORIZED_DEBUG_QUALIFICATION_ONLY"
        or authority.get("plan_sha256") != sha256(plan_path)
        or authority.get("main_agent_token_sha256") != hashlib.sha256(secret.encode("utf-8")).hexdigest()
        or float(authority.get("not_before_epoch") or 0) > now
        or float(authority.get("expires_epoch") or 0) <= now
        or authority.get("accepted_round_authorized") is not False
    ):
        raise FourFlowError("four-flow execution authority differs")
    dispatch = Path(plan["shared_dispatch"])
    if sha256(dispatch) != plan["shared_dispatch_sha256"]:
        raise FourFlowError("shared dispatch identity differs")
    definitions = {
        "A_solo": ("job", "qualification_solo"), "A_paired": ("job", "qualification_paired"),
        "B_solo": ("xp", "qualification_solo"), "B_paired": ("xp", "qualification_paired"),
    }
    rows = {name: validate_launch(Path(path), *definitions[name], plan["shared_dispatch_sha256"]) for name, path in plan["flows"].items()}
    for name in ("A_solo", "B_solo"):
        completed = subprocess.run(command(dispatch, rows[name]), check=False, timeout=600)
        if completed.returncode != 0:
            raise FourFlowError("%s failed rc=%d" % (name, completed.returncode))
    children = {name: subprocess.Popen(command(dispatch, rows[name])) for name in ("A_paired", "B_paired")}
    failures = {}
    for name, child in children.items():
        try:
            code = child.wait(timeout=600)
        except subprocess.TimeoutExpired:
            child.terminate()
            code = -1
        if code != 0:
            failures[name] = code
    if failures:
        raise FourFlowError("paired qualification failed: %s" % failures)
    evidence = {name: rows[name]["output"] for name in rows}
    for path in evidence.values():
        load_sealed(Path(path), "four-flow evidence")
    for scenario, attempt, output in (
        ("job_memory", plan["job_attempt_token"], plan["job_gate_output"]),
        ("hybench_xp_io", plan["xp_attempt_token"], plan["xp_gate_output"]),
    ):
        argv = [
            sys.executable, str(dispatch), "--scenario-backend", "job",
            "--backend-path", str(Path(str(rows["A_solo"]["bundle"])) / "qualification/mysql_job_distributed_live_readback_qualification_v2.py"),
            "--compare-bidirectional",
            "--a-solo-evidence", evidence["A_solo"], "--a-paired-evidence", evidence["A_paired"],
            "--b-solo-evidence", evidence["B_solo"], "--b-paired-evidence", evidence["B_paired"],
            "--gate-scenario", scenario, "--host-id", "node02", "--attempt-token", attempt,
            "--output", output,
        ]
        completed = subprocess.run(argv, check=False, timeout=60)
        if completed.returncode != 0:
            raise FourFlowError("%s gate comparison failed" % scenario)
        gate = load_sealed(Path(output), "%s dynamic gate" % scenario)
        if gate.get("status") != "PASS" or gate.get("accepted_round_authorized") is not False:
            raise FourFlowError("%s dynamic gate did not pass" % scenario)
    return {"status": "PASS_FOUR_FLOW_BIDIRECTIONAL_GATE", "evidence": evidence, "job_gate": plan["job_gate_output"], "xp_gate": plan["xp_gate_output"], "accepted_rounds_credit": 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--build-plan", action="store_true")
    modes.add_argument("--authorize-plan", action="store_true")
    modes.add_argument("--execute", action="store_true")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--authority", type=Path)
    parser.add_argument("--main-agent-token-file", type=Path)
    parser.add_argument("--ttl-seconds", type=int, default=3600)
    parser.add_argument("--shared-dispatch", type=Path)
    parser.add_argument("--job-solo-launch", type=Path)
    parser.add_argument("--job-paired-launch", type=Path)
    parser.add_argument("--xp-solo-launch", type=Path)
    parser.add_argument("--xp-paired-launch", type=Path)
    parser.add_argument("--job-attempt-token")
    parser.add_argument("--xp-attempt-token")
    parser.add_argument("--job-gate-output", type=Path)
    parser.add_argument("--xp-gate-output", type=Path)
    args = parser.parse_args(argv)
    if args.build_plan:
        required = (args.shared_dispatch, args.job_solo_launch, args.job_paired_launch, args.xp_solo_launch, args.xp_paired_launch, args.job_attempt_token, args.xp_attempt_token, args.job_gate_output, args.xp_gate_output)
        if not all(required):
            raise FourFlowError("build-plan inputs are incomplete")
        atomic(args.plan, build_plan(args))
    elif args.authorize_plan:
        if args.authority is None or args.main_agent_token_file is None:
            raise FourFlowError("authorize-plan requires authority and token paths")
        authorize(args.plan, args.authority, args.main_agent_token_file, args.ttl_seconds)
    else:
        if args.authority is None or args.main_agent_token_file is None:
            raise FourFlowError("execute requires authority and token paths")
        print(json.dumps(execute(args.plan, args.authority, args.main_agent_token_file), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FourFlowError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
