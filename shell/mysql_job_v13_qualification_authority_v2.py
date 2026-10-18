#!/usr/bin/env python3
"""Materialize one short-lived JOB v13 solo or paired qualification.

This is control-plane only.  It creates a token, command, authority, and an
exact launch descriptor; it never starts a workload or contacts MySQL.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, Mapping


sys.dont_write_bytecode = True
TARGET_ID = "6b4844bcdcc7ada8dfd76c6676f87c98dc382aa76979504d3edc65baaab52dd7"
BASE_SHA256 = "1f51404fc91f063ad584443dc1d2b13be0ab0cac51ea46b3a04c7ad3d9205535"
SAFE_ID = re.compile(r"[A-Za-z0-9_.:-]{32,128}$")


class AuthorityError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def load_base(path: Path):
    if path.is_symlink() or not path.is_file() or sha256(path) != BASE_SHA256:
        raise AuthorityError("sealed JOB solo renderer differs")
    spec = importlib.util.spec_from_file_location("_job_v13_solo_renderer", path)
    if spec is None or spec.loader is None:
        raise AuthorityError("cannot load sealed JOB solo renderer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def safe_peer_metric(path: Path, peer_attempt: str) -> str:
    path = path.resolve(strict=False)
    parent = path.parent
    ancestor = parent if parent.exists() else parent.parent
    if (
        not path.is_absolute()
        or path.name != "workload_metrics.jsonl"
        or parent.name != peer_attempt
        or path.is_symlink()
        or parent.is_symlink()
        or ancestor.is_symlink()
        or not ancestor.is_dir()
    ):
        raise AuthorityError("paired peer metric target differs")
    return str(path)


def write_exclusive(path: Path, value: Mapping[str, Any], mode: int = 0o600) -> None:
    payload = canonical(value)
    sidecar = Path(str(path) + ".sha256")
    if any(item.exists() or item.is_symlink() for item in (path, sidecar)):
        raise AuthorityError("refusing to overwrite authority material")
    path.parent.mkdir(parents=True, exist_ok=True)
    for destination, data in (
        (path, payload),
        (sidecar, ("%s  %s\n" % (hashlib.sha256(payload).hexdigest(), path.name)).encode("ascii")),
    ):
        descriptor = os.open(str(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())


def materialize(args: argparse.Namespace) -> Mapping[str, Any]:
    if SAFE_ID.fullmatch(args.qualification_attempt_id or "") is None:
        raise AuthorityError("qualification attempt ID differs")
    if args.ttl_seconds < 60 or args.ttl_seconds > 3600:
        raise AuthorityError("qualification TTL must be 60..3600 seconds")
    paired = args.mode == "qualification_paired"
    peer_values = (
        args.peer_scenario,
        args.peer_qualification_attempt_id,
        args.peer_workload_metric_stream,
        args.peer_readback,
        args.peer_container,
    )
    if not paired and any(value is not None for value in peer_values):
        raise AuthorityError("solo qualification cannot bind peer material")
    if paired and (
        not all(value is not None for value in peer_values)
        or args.peer_scenario != "hybench_xp_io"
        or SAFE_ID.fullmatch(args.peer_qualification_attempt_id or "") is None
        or args.peer_qualification_attempt_id == args.qualification_attempt_id
    ):
        raise AuthorityError("paired XP binding differs")
    raw_spec = json.loads(args.bootstrap_spec.read_text(encoding="utf-8"))
    target = Path(str((raw_spec.get("bundle") or {}).get("path") or "")).resolve(strict=True)
    if target.name != TARGET_ID:
        raise AuthorityError("JOB v13 target bundle differs")
    base = load_base(target / "controller/render_mysql_job_v32_qualification_solo_tmpfs_v2.py")
    spec = base.load(args.bootstrap_spec, "bootstrap spec")
    evidence = base.extract_evidence(
        base.load(args.bootstrap_evidence, "bootstrap evidence")
    )
    binding = base.validate_inputs(spec, evidence)
    if binding["bundle_id"] != TARGET_ID:
        raise AuthorityError("JOB v13 target bundle differs")
    output = args.output_directory
    if output.exists() or output.is_symlink():
        raise AuthorityError("qualification output directory already exists")
    output.mkdir(parents=True, mode=0o700)
    token_path = output / "main_agent_token"
    token = secrets.token_urlsafe(48)
    token_path.write_text(token + "\n", encoding="ascii")
    token_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    command = dict(base.build_command(spec, evidence, binding))
    command["mode"] = args.mode
    command["qualification_attempt_id"] = args.qualification_attempt_id
    leaf = "solo" if not paired else "paired"
    command["argv"] = [
        value.replace(
            binding["attempt"] + "-qualification-solo",
            args.qualification_attempt_id,
        ).replace(
            "/measurement/qualification_solo.native.jsonl",
            "/measurement/%s.native.jsonl" % args.qualification_attempt_id,
        )
        for value in command["argv"]
    ]
    command["environment"] = {"QUALIFICATION_ROLE": leaf}
    own_metric = (
        Path(spec["paths"]["host_measurement_tmpfs"])
        / args.qualification_attempt_id / "workload_metrics.jsonl"
    )
    own_metric.parent.mkdir(parents=True, mode=0o700, exist_ok=False)
    command["workload_metric_stream"] = {
        "schema_version": "mysql_algorithm_c_debug_workload_metric_stream_v1",
        "path": str(own_metric),
    }
    if paired:
        command["paired_peer_workload"] = {
            "schema_version": "mysql_algorithm_c_paired_peer_workload_binding_v1",
            "scenario": args.peer_scenario,
            "qualification_attempt_id": args.peer_qualification_attempt_id,
            "workload_metric_stream_path": safe_peer_metric(
                args.peer_workload_metric_stream,
                args.peer_qualification_attempt_id,
            ),
        }
    command_path = output / "command.json"
    write_exclusive(command_path, command)
    now = datetime.now(timezone.utc)
    authority = {
        "schema_version": "mysql_algorithm_c_main_agent_qualification_authority_v1",
        "authorized_by": "main_agent",
        "bundle_id": TARGET_ID,
        "mode": args.mode,
        "qualification_attempt_id": args.qualification_attempt_id,
        "main_agent_token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "command_spec_sha256": sha256(command_path),
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "not_before_epoch": now.timestamp() - 1.0,
        "expires_epoch": (now + timedelta(seconds=args.ttl_seconds)).timestamp(),
    }
    authority_path = output / "main_agent_authority.json"
    write_exclusive(authority_path, authority)
    evidence_path = own_metric.parent / "qualification_evidence.json"
    launch = {
        "schema_version": "mysql_job_v13_qualification_launch_descriptor_v2",
        "status": "MATERIALIZED_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "mode": args.mode,
        "scenario_backend": "job",
        "attempt_token": binding["attempt"],
        "qualification_attempt_id": args.qualification_attempt_id,
        "bundle": spec["bundle"]["path"],
        "container": spec["containers"]["database"],
        "mysql_client_container": spec["containers"]["client"],
        "mysql_defaults_file": spec["mysql_defaults_file"],
        "output": str(evidence_path),
        "command_spec": str(command_path),
        "main_agent_authority": str(authority_path),
        "main_agent_token_file": str(token_path),
        "peer_readback": str(args.peer_readback) if paired else None,
        "peer_container": args.peer_container if paired else None,
        "shared_dispatch_sha256": sha256(args.shared_dispatch.resolve(strict=True)),
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }
    launch_path = output / "launch.json"
    write_exclusive(launch_path, launch)
    return {"command": str(command_path), "authority": str(authority_path), "launch": str(launch_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap-spec", type=Path, required=True)
    parser.add_argument("--bootstrap-evidence", type=Path, required=True)
    parser.add_argument("--shared-dispatch", type=Path, required=True)
    parser.add_argument("--mode", choices=("qualification_solo", "qualification_paired"), required=True)
    parser.add_argument("--qualification-attempt-id", required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--ttl-seconds", type=int, default=900)
    parser.add_argument("--peer-scenario")
    parser.add_argument("--peer-qualification-attempt-id")
    parser.add_argument("--peer-workload-metric-stream", type=Path)
    parser.add_argument("--peer-readback", type=Path)
    parser.add_argument("--peer-container")
    args = parser.parse_args(argv)
    print(json.dumps(materialize(args), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AuthorityError, OSError, ValueError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
