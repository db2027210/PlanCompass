#!/usr/bin/env python3
"""Two-level JIT issuer for the immutable XP v9 qualification bootstrap.

The outer issuer lease is exactly 60 minutes.  A lease can create a child
bootstrap attempt only while live; the child runtime authority remains at most
30 minutes because the sealed production service rejects a longer duration.
This tool performs no SSH, Docker, systemd, or workload action.
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
import stat


V9_ID = "168f1505fa89a80dee9fb39533300b7e1e1a4ecce6e9d60af2704072e4cb67ac"
V9_MANIFEST_SHA256 = (
    "02c85d1bc350fbd8a95a0e5007581fd16721402c6faec59f48ee89dd2151ca26"
)
LEASE_SCHEMA = "mysql_xp_v9_jit_issuer_lease_v1"
TOKEN_RE = re.compile(r"[A-Za-z0-9_.:-]{32,128}$")


class IssuerError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def parse_utc(value):
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo != timezone.utc:
        raise IssuerError("issuer timestamp must use UTC")
    return parsed


def gate_binding():
    return {
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "database_cpu_quota_cores": 5.3,
        "database_io_read_bps": 29360128,
        "database_io_write_bps": 524288,
        "capture_only_io_bps_per_direction": 1205862400,
        "capture_is_formal_r0": False,
        "capture_accepted_round_credit": 0,
        "active_parameter_count": 7,
        "required_nonempty_subsets": 127,
        "fixed_real_replay_top_k": 5,
        "required_trace_transactions": [
            "AT1", "AT2", "AT3", "AT4", "AT5", "AT6",
        ],
        "sealed_real_trace_registry_required_before_fresh_r0": True,
        "missing_trace_registry_policy": "FAIL_CLOSED",
        "attempt_authorization_outside_attempt_root_required": True,
        "private_directory_mode": "0700",
        "private_file_mode": "0600",
    }


def verify_v9(bundle):
    bundle = Path(bundle).resolve(strict=True)
    manifest_path = bundle / "bundle_manifest.json"
    sidecar = bundle / "bundle_manifest.json.sha256"
    if (
        bundle.name != V9_ID
        or digest(manifest_path) != V9_MANIFEST_SHA256
        or sidecar.read_text(encoding="ascii")
        != V9_MANIFEST_SHA256 + "  bundle_manifest.json\n"
    ):
        raise IssuerError("XP v9 bundle/manifest identity differs")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != V9_ID
        or hashlib.sha256(canonical(unsigned)).hexdigest() != V9_ID
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise IssuerError("XP v9 manifest scope differs")
    actual = {}
    for relative in manifest["files"]:
        candidate = bundle / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise IssuerError("XP v9 file absent: " + relative)
        actual[relative] = digest(candidate)
    if actual != manifest["files"]:
        raise IssuerError("XP v9 manifest file closure differs")
    contract = json.loads(
        (bundle / "xp_executable_launch_contract_v3.json")
        .read_text(encoding="utf-8")
    )
    algorithm = contract["algorithm"]
    database = contract["database_resource_envelope"]
    capture = contract["capture_only_resource_envelope"]
    expected = gate_binding()
    if (
        database["cpu_quota_cores"]
        != expected["database_cpu_quota_cores"]
        or database["io_read_bps"]
        != expected["database_io_read_bps"]
        or database["io_write_bps"]
        != expected["database_io_write_bps"]
        or capture["io_read_bps"]
        != expected["capture_only_io_bps_per_direction"]
        or capture["io_write_bps"]
        != expected["capture_only_io_bps_per_direction"]
        or capture["accepted_rounds_credit"] != 0
        or capture["capture_is_r0"] is not False
        or algorithm["active_parameter_count"] != 7
        or algorithm["required_nonempty_subsets"] != 127
        or algorithm["fixed_real_replay_top_k"] != 5
    ):
        raise IssuerError("XP v9 formal/capture/algorithm gate differs")
    return bundle


def private_token(path):
    path = Path(path).resolve(strict=True)
    parent = path.parent
    if (
        path.is_symlink()
        or not path.is_file()
        or stat.S_IMODE(path.stat().st_mode) != 0o600
        or parent.is_symlink()
        or not parent.is_dir()
        or stat.S_IMODE(parent.stat().st_mode) != 0o700
        or path.stat().st_uid != parent.stat().st_uid
        or path.stat().st_gid != parent.stat().st_gid
    ):
        raise IssuerError("issuer private token metadata differs")
    token = path.read_text(encoding="ascii").strip()
    if TOKEN_RE.fullmatch(token) is None:
        raise IssuerError("issuer private token content differs")
    return token


def write_sealed(path, value):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists():
        raise IssuerError("refusing to overwrite sealed issuer evidence")
    payload = canonical(value)
    descriptor = os.open(
        str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    sidecar.write_text(
        hashlib.sha256(payload).hexdigest() + "  " + path.name + "\n",
        encoding="ascii",
    )
    os.chmod(path, 0o400)
    os.chmod(sidecar, 0o400)


def prepare_lease(bundle, token_file, output, now=None):
    verify_v9(bundle)
    token = private_token(token_file)
    observed = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    lease = {
        "schema_version": LEASE_SCHEMA,
        "status": "ISSUER_LEASE_NOT_USED",
        "bundle_id": V9_ID,
        "bundle_manifest_sha256": V9_MANIFEST_SHA256,
        "issuer_created_at_utc": observed.isoformat(),
        "issuer_expires_at_utc":
            (observed + timedelta(minutes=60)).isoformat(),
        "issuer_valid_minutes": 60,
        "issuer_token_sha256":
            hashlib.sha256(token.encode("ascii")).hexdigest(),
        "child_runtime_valid_minutes_max": 30,
        "child_runtime_valid_minutes_default": 25,
        "formal_r0_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
        "gate_binding": gate_binding(),
    }
    write_sealed(output, lease)
    return lease


def load_lease(path, token_file, now=None):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if (
        path.is_symlink()
        or not path.is_file()
        or stat.S_IMODE(path.stat().st_mode) != 0o400
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii")
        != digest(path) + "  " + path.name + "\n"
    ):
        raise IssuerError("sealed issuer lease differs")
    lease = json.loads(path.read_text(encoding="utf-8"))
    token = private_token(token_file)
    created = parse_utc(lease["issuer_created_at_utc"])
    expires = parse_utc(lease["issuer_expires_at_utc"])
    observed = now or datetime.now(timezone.utc)
    if (
        lease.get("schema_version") != LEASE_SCHEMA
        or lease.get("bundle_id") != V9_ID
        or lease.get("bundle_manifest_sha256") != V9_MANIFEST_SHA256
        or lease.get("issuer_valid_minutes") != 60
        or (expires - created).total_seconds() != 3600
        or not created <= observed < expires
        or lease.get("issuer_token_sha256")
        != hashlib.sha256(token.encode("ascii")).hexdigest()
        or lease.get("gate_binding") != gate_binding()
        or lease.get("formal_launch_authorized") is not False
        or lease.get("accepted_round_authorized") is not False
    ):
        raise IssuerError("issuer lease authority differs or expired")
    return lease


def load_materializer():
    path = (
        Path(__file__).resolve().parents[1]
        / "materializer/mysql_xp_v9_qualification_materializer_v1.py"
    )
    spec = importlib.util.spec_from_file_location(
        "mysql_xp_v9_qualification_materializer", path
    )
    if spec is None or spec.loader is None:
        raise IssuerError("v9 materializer module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def issue(bundle, token_file, lease_path, output_root, runtime_valid_minutes=25):
    bundle = verify_v9(bundle)
    lease = load_lease(lease_path, token_file)
    if not isinstance(runtime_valid_minutes, int) or not (
        10 <= runtime_valid_minutes <= 30
    ):
        raise IssuerError("child runtime validity must be within 10..30")
    module = load_materializer()
    result = module.create_materials(
        bundle, Path(output_root), runtime_valid_minutes
    )
    material = Path(result["materials_directory"])
    manifest = json.loads(
        (material / "materials_manifest.json").read_text(encoding="utf-8")
    )
    bootstrap = json.loads(
        (material / "bootstrap_spec.json").read_text(encoding="utf-8")
    )
    gates = manifest["formal_gate_binding"]
    if (
        manifest["bundle_id"] != V9_ID
        or manifest["bundle_manifest_sha256"] != V9_MANIFEST_SHA256
        or bootstrap["formal_scope"] != "mysql_only"
        or bootstrap["cross_engine_comparison_prohibited"] is not True
        or gates["database_cpu_quota_cores"] != 5.3
        or gates["database_io_read_bps"] != 29360128
        or gates["database_io_write_bps"] != 524288
        or gates["capture_only_io_bps_per_direction"] != 1205862400
        or gates["capture_accepted_round_credit"] != 0
        or gates["required_nonempty_subsets"] != 127
        or gates["required_trace_transactions"]
        != ["AT1", "AT2", "AT3", "AT4", "AT5", "AT6"]
        or "/authorizations/" not in manifest["authorization_root"]
        or manifest["attempt_root_must_be_absent_during_authorization_install"]
        is not True
        or manifest["private_credentials"]["directory_mode"] != "0700"
        or manifest["private_credentials"]["file_mode"] != "0600"
        or manifest["formal_r0_authorized"] is not False
        or manifest["accepted_round_authorized"] is not False
    ):
        raise IssuerError("child v9 materials binding differs")
    record_root = Path(output_root).resolve() / "issuer_records"
    record_root.mkdir(mode=0o700, exist_ok=True)
    record = {
        "schema_version": "mysql_xp_v9_jit_issuance_record_v1",
        "status": "CHILD_MATERIALS_ISSUED_NOT_INSTALLED_NOT_LAUNCHED",
        "issuer_lease_sha256": digest(lease_path),
        "issuer_expires_at_utc": lease["issuer_expires_at_utc"],
        "bundle_id": V9_ID,
        "bundle_manifest_sha256": V9_MANIFEST_SHA256,
        "attempt_id": result["attempt_id"],
        "child_materials_directory": str(material),
        "child_materials_manifest_sha256":
            digest(material / "materials_manifest.json"),
        "child_expires_at_utc": result["expires_at_utc"],
        "runtime_valid_minutes": runtime_valid_minutes,
        "formal_r0_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
        "gate_binding": gate_binding(),
    }
    record_path = record_root / (result["attempt_id"] + ".json")
    write_sealed(record_path, record)
    result["issuer_record"] = str(record_path)
    result["issuer_lease_expires_at_utc"] = lease["issuer_expires_at_utc"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    prepare = subparsers.add_parser("prepare-lease")
    prepare.add_argument("--bundle", type=Path, required=True)
    prepare.add_argument("--issuer-token-file", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    child = subparsers.add_parser("issue")
    child.add_argument("--bundle", type=Path, required=True)
    child.add_argument("--issuer-token-file", type=Path, required=True)
    child.add_argument("--lease", type=Path, required=True)
    child.add_argument("--output-root", type=Path, required=True)
    child.add_argument("--runtime-valid-minutes", type=int, default=25)
    args = parser.parse_args()
    if args.action == "prepare-lease":
        value = prepare_lease(
            args.bundle, args.issuer_token_file, args.output
        )
    else:
        value = issue(
            args.bundle,
            args.issuer_token_file,
            args.lease,
            args.output_root,
            args.runtime_valid_minutes,
        )
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except IssuerError as exc:
        raise SystemExit("NOT_READY: %s" % exc)
