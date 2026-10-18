#!/usr/bin/env python3
"""Run sealed TPC-C local qualification only after Retry9 releases its mutex."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
from typing import Any


MUTEX = Path("/run/lock/ra-mysql-node04-tpch-tpcc-fourflow.lock")
PINNED_PYTHON = Path(
    "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/"
    "runtime_algorithm_c_successor_20260729/runtime/"
    "python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/"
    "bin/python3.11"
)
TPH_ID = "1ef07fd3c8b547598d3c6d1419c11383abf08ca7d23d8c85e8820707963e0bf3"
TPH_SHA = "88e06baaec02c84e3cebb78cd74bf2e06b03de674118766aac23581d4c7963bd"
TPCC_ID = "025b4b9595755761b4c10bdb3324dc8f7dc2f7edea76bb906e166de4dcbdb6d8"
TPCC_SHA = "de362d1680ae78611487a5bab9109937d5f31ce941085c81d05a8bbcdfe45a04"
TARGET_ID = "b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47"


class GateError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def regular(path: Path, label: str) -> Path:
    path = Path(path)
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise GateError(label + " absent") from exc
    if stat.S_ISLNK(state.st_mode) or not resolved.is_file():
        raise GateError(label + " must be a regular non-symlink")
    return resolved


def sealed(path: Path, label: str) -> tuple[Path, dict[str, Any]]:
    path = regular(path, label)
    sidecar = regular(Path(str(path) + ".sha256"), label + " sidecar")
    if sidecar.read_text(encoding="ascii") != sha256(path) + "  " + path.name + "\n":
        raise GateError(label + " seal differs")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(label + " JSON differs") from exc
    if not isinstance(value, dict):
        raise GateError(label + " JSON object required")
    return path, value


def verify_bundle(root: Path, identity: str, manifest_sha: str, label: str) -> Path:
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or root.is_symlink() or root.name != identity:
        raise GateError(label + " root identity differs")
    manifest_path, manifest = sealed(root / "bundle_manifest.json", label + " manifest")
    if (sha256(manifest_path) != manifest_sha
            or manifest.get("bundle_id") != identity
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True
            or manifest.get("postgresql_mutation_prohibited") is not True
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False
            or manifest.get("accepted_rounds_credit") != 0):
        raise GateError(label + " manifest authority differs")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise GateError(label + " file closure absent")
    for relative, expected in files.items():
        pure = PurePosixPath(str(relative))
        if pure.is_absolute() or ".." in pure.parts:
            raise GateError(label + " unsafe closure entry")
        member = regular(root / pure, label + " member " + str(relative))
        if root not in member.parents or sha256(member) != expected:
            raise GateError(label + " file closure differs: " + str(relative))
    return root


def verify_self() -> Path:
    script = Path(__file__).resolve(strict=True)
    root = script.parents[1]
    manifest_path, manifest = sealed(root / "bundle_manifest.json", "successor manifest")
    unsigned = dict(manifest)
    identity = unsigned.pop("bundle_id", None)
    relative = script.relative_to(root).as_posix()
    if (root.name != identity
            or hashlib.sha256(canonical(unsigned)).hexdigest() != identity
            or (manifest.get("files") or {}).get(relative) != sha256(script)
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True
            or manifest.get("postgresql_mutation_prohibited") is not True
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False
            or manifest.get("accepted_rounds_credit") != 0):
        raise GateError("successor self identity/closure differs")
    return root


def verify_retry9_completion(completion_path: Path, spec: Path,
                             evidence: Path, defaults: Path) -> dict[str, Any]:
    completion_path, completion = sealed(completion_path, "Retry9 completion")
    output_root = completion_path.parent.parent
    plan_path, plan = sealed(output_root / "controller/retry9.plan.json", "Retry9 plan")
    attempt = plan.get("attempt_token")
    pids = completion.get("pids") or {}
    if (completion_path != output_root / "completion/retry9.completion.json"
            or plan_path != output_root / "controller/retry9.plan.json"
            or plan.get("schema_version") != "mysql_tpch_retry9_one_click_plan_v1"
            or plan.get("status") != "SEALED_NOT_EXECUTED"
            or plan.get("mutex") != str(MUTEX)
            or Path(str(plan.get("bootstrap_spec"))).resolve(strict=True) != spec
            or Path(str(plan.get("bootstrap_evidence"))).resolve(strict=True) != evidence
            or Path(str(plan.get("mysql_defaults"))).resolve(strict=True) != defaults
            or Path(str(plan.get("target"))).resolve(strict=True).name != TARGET_ID
            or completion.get("schema_version") != "mysql_tpch_retry9_one_click_completion_v1"
            or completion.get("status") != "PASS_FRESH_B235_RETRY9_COMPLETED_ZERO_CREDIT"
            or completion.get("attempt_token") != attempt
            or completion.get("tpcc_fourflow_mutex_held_for_full_window") is not True
            or completion.get("accepted_round_authorized") is not False
            or completion.get("accepted_rounds_credit") != 0
            or set(pids) != {"bootstrap", "controller", "workload"}
            or any(not isinstance(pids[name], dict)
                   or int(pids[name].get("pid") or 0) <= 1 for name in pids)):
        raise GateError("Retry9 PASS completion/plan/input binding differs")
    inner = regular(Path(str(completion.get("inner_completion"))), "Retry9 inner completion")
    if sha256(inner) != completion.get("inner_completion_sha256"):
        raise GateError("Retry9 inner completion binding differs")
    return {"attempt_token": attempt, "completion": str(completion_path),
            "completion_sha256": sha256(completion_path)}


def acquire_mutex() -> tuple[int, int]:
    try:
        MUTEX.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise GateError("TPC-H/TPC-C fourflow mutex is occupied; wait for Retry9") from exc
    state = MUTEX.lstat()
    if (not stat.S_ISDIR(state.st_mode) or state.st_uid != os.geteuid()
            or stat.S_IMODE(state.st_mode) != 0o700):
        raise GateError("new mutex identity differs")
    return state.st_dev, state.st_ino


def release_mutex(identity: tuple[int, int]) -> None:
    try:
        state = MUTEX.lstat()
    except FileNotFoundError as exc:
        raise GateError("owned mutex disappeared") from exc
    if (state.st_dev, state.st_ino) != identity or not stat.S_ISDIR(state.st_mode):
        raise GateError("owned mutex identity changed; refusing removal")
    MUTEX.rmdir()


def run(args: argparse.Namespace) -> int:
    if os.geteuid() != 0:
        raise GateError("root execution required")
    if Path(sys.executable).resolve(strict=True) != PINNED_PYTHON.resolve(strict=True):
        raise GateError("pinned Python execution required: " + str(PINNED_PYTHON))
    verify_self()
    verify_bundle(args.tpch_retry9_bundle, TPH_ID, TPH_SHA, "Retry9 one-click")
    tpcc = verify_bundle(args.tpcc_oneclick_bundle, TPCC_ID, TPCC_SHA,
                         "TPC-C predecessor one-click")
    spec = regular(args.tpch_bootstrap_spec, "TPC-H bootstrap spec")
    evidence = regular(args.tpch_bootstrap_evidence, "TPC-H bootstrap evidence")
    defaults = regular(args.tpch_defaults_file, "TPC-H defaults")
    verify_retry9_completion(args.tpch_retry9_completion, spec, evidence, defaults)
    mutex_identity = acquire_mutex()
    try:
        # Close the absence-to-launch race and revalidate after ownership.
        verify_retry9_completion(args.tpch_retry9_completion, spec, evidence, defaults)
        controller = regular(
            tpcc / "controller/mysql_tpcc_v16_package_safe_one_click_v1.py",
            "TPC-C predecessor controller",
        )
        command = [str(PINNED_PYTHON), "-B", str(controller), "--mode", "local",
                   "--tpch-bootstrap-spec", str(spec),
                   "--tpch-bootstrap-evidence", str(evidence),
                   "--tpch-defaults-file", str(defaults)]
        completed = subprocess.run(command, check=False)
        if completed.returncode:
            raise GateError("TPC-C predecessor failed rc=" + str(completed.returncode))
        return 0
    finally:
        release_mutex(mutex_identity)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tpch-retry9-bundle", type=Path, required=True)
    parser.add_argument("--tpch-retry9-completion", type=Path, required=True)
    parser.add_argument("--tpcc-oneclick-bundle", type=Path, required=True)
    parser.add_argument("--tpch-bootstrap-spec", type=Path, required=True)
    parser.add_argument("--tpch-bootstrap-evidence", type=Path, required=True)
    parser.add_argument("--tpch-defaults-file", type=Path, required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GateError, OSError, UnicodeError, ValueError, KeyError,
            subprocess.SubprocessError) as exc:
        print("NOT_READY: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
