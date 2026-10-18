#!/usr/bin/env python3
"""Re-sign an expired TPC-H bootstrap pair for qualification only.

This tool never launches or mutates the data plane.  It preserves every field
of the sealed bootstrap spec/evidence except the short validity window and the
evidence's corresponding canonical spec hash.  It writes a new no-overwrite
pair which must still pass the target bundle's sealed live verifier.
"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys


class ResignError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(
            value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def load_sealed(path, label):
    path = Path(path).resolve(strict=True)
    sidecar = Path(str(path) + ".sha256")
    if (
        path.is_symlink()
        or not path.is_file()
        or sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii", errors="strict")
        != "%s  %s\n" % (sha256(path), path.name)
    ):
        raise ResignError("%s seal differs" % label)
    value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    if not isinstance(value, dict):
        raise ResignError("%s must be an object" % label)
    return value


def atomic_json(path, value):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or sidecar.exists():
        raise ResignError("refusing to overwrite re-signed authority")
    temporary = path.with_name(".%s.%s" % (path.name, secrets.token_hex(6)))
    temporary.write_bytes(canonical(value))
    os.chmod(str(temporary), 0o600)
    os.replace(str(temporary), str(path))
    sidecar.write_text("%s  %s\n" % (sha256(path), path.name), encoding="ascii")
    os.chmod(str(sidecar), 0o600)


def parse_time(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-spec", type=Path, required=True)
    parser.add_argument("--old-evidence", type=Path, required=True)
    parser.add_argument("--main-agent-token-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--valid-seconds", type=int, default=3600)
    args = parser.parse_args()
    if not 900 <= args.valid_seconds <= 3600:
        raise ResignError("qualification validity must be 900..3600 seconds")
    spec = load_sealed(args.old_spec, "old bootstrap spec")
    evidence = load_sealed(args.old_evidence, "old bootstrap evidence")
    token_path = args.main_agent_token_file.resolve(strict=True)
    token_state = token_path.stat()
    if (
        token_path.is_symlink()
        or not token_path.is_file()
        or token_state.st_uid != os.geteuid()
        or stat.S_IMODE(token_state.st_mode) != 0o600
    ):
        raise ResignError("main-agent token authority differs")
    token = token_path.read_text(encoding="ascii", errors="strict").strip()
    old_created = parse_time(spec.get("created_at_utc"))
    old_expires = parse_time(spec.get("expires_at_utc"))
    now = datetime.now(timezone.utc)
    if (
        spec.get("schema_version")
        != "mysql_tpch_distributed_v2_bootstrap_spec_v1"
        or spec.get("formal_scope") != "mysql_only"
        or spec.get("cross_engine_comparison_prohibited") is not True
        or spec.get("postgresql_mutation_prohibited") is not True
        or spec.get("qualification_only") is not True
        or spec.get("formal_r0_authorized") is not False
        or spec.get("formal_authorized") is not False
        or spec.get("accepted_round_authorized") is not False
        or spec.get("main_agent_token_sha256")
        != sha_bytes(token.encode("ascii"))
        or old_created.tzinfo is None
        or old_expires.tzinfo is None
        or old_expires <= old_created
        or (old_expires - old_created).total_seconds() > 3600
        or now < old_expires.astimezone(timezone.utc)
        or evidence.get("schema_version")
        != "mysql_tpch_distributed_v2_bootstrap_evidence_v1"
        or evidence.get("status")
        != "PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY"
        or evidence.get("attempt_token") != spec.get("attempt_token")
        or evidence.get("bundle_id") != (spec.get("bundle") or {}).get("bundle_id")
        or evidence.get("spec_sha256") != sha_bytes(canonical(spec))
        or evidence.get("qualification_only") is not True
        or evidence.get("formal_r0_authorized") is not False
        or evidence.get("formal_authorized") is not False
        or evidence.get("accepted_round_authorized") is not False
    ):
        raise ResignError("expired qualification pair boundary differs")
    new_spec = dict(spec)
    new_spec["created_at_utc"] = now.isoformat()
    new_spec["expires_at_utc"] = (
        now + timedelta(seconds=args.valid_seconds)
    ).isoformat()
    new_evidence = dict(evidence)
    new_evidence["spec_sha256"] = sha_bytes(canonical(new_spec))
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    spec_output = output / "bootstrap_spec.json"
    evidence_output = output / "bootstrap_evidence.json"
    atomic_json(spec_output, new_spec)
    atomic_json(evidence_output, new_evidence)
    print(
        json.dumps(
            {
                "status": "RESIGNED_QUALIFICATION_ONLY_REQUIRES_SEALED_LIVE_VERIFY",
                "spec": str(spec_output),
                "evidence": str(evidence_output),
                "old_spec_sha256": sha256(args.old_spec),
                "new_spec_sha256": sha256(spec_output),
                "valid_seconds": args.valid_seconds,
                "formal_r0_authorized": False,
                "formal_authorized": False,
                "accepted_round_authorized": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (ResignError, OSError, UnicodeError, ValueError, KeyError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
