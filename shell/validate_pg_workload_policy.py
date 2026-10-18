#!/usr/bin/env python3
"""2026-08-06 PostgreSQL experiment workload policy preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Dict, List


XP_WORKLOAD_KEYS = {
    "xtpclient",
    "xapclient",
    "at1_percent",
    "at2_percent",
    "at3_percent",
    "at4_percent",
    "at5_percent",
    "at6_percent",
}
XP_OPERATIONAL_KEYS = {
    "password",
    "password_ap",
    "url",
    "url_tp",
    "url_ap",
    "xpRunMins",
}
XP_FORBIDDEN_KEYS = {
    "ap_query_ids",
    "ap_query_weights",
    "ap_query_sequence",
    "ap_query_sequence_limit",
}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_props(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"{path}:{line_number}: empty property key")
        if key in values:
            raise ValueError(f"{path}:{line_number}: duplicate property {key}")
        values[key] = value.strip()
    return values


def validate_xp(args: argparse.Namespace) -> Dict[str, object]:
    source = Path(args.source_props).resolve()
    generated = Path(args.generated_props).resolve()
    statement = Path(args.statement_file).resolve()
    jar = Path(args.jar).resolve()
    for path in (source, generated, statement, jar):
        if not path.is_file():
            raise ValueError(f"required file missing: {path}")

    source_values = parse_props(source)
    generated_values = parse_props(generated)
    present_forbidden = sorted(XP_FORBIDDEN_KEYS.intersection(generated_values))
    if present_forbidden:
        raise ValueError(f"forbidden XP properties present: {present_forbidden}")

    changed = sorted(
        key
        for key in set(source_values).union(generated_values)
        if source_values.get(key) != generated_values.get(key)
    )
    allowed_changes = XP_WORKLOAD_KEYS.union(XP_OPERATIONAL_KEYS)
    illegal_changes = sorted(set(changed).difference(allowed_changes))
    if illegal_changes:
        raise ValueError(f"XP properties changed outside policy allowlist: {illegal_changes}")

    for key in ("xtpclient", "xapclient"):
        try:
            value = int(generated_values[key])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{key} must be a positive integer") from exc
        if value <= 0:
            raise ValueError(f"{key} must be a positive integer, got {value}")

    at_values: List[int] = []
    for index in range(1, 7):
        key = f"at{index}_percent"
        try:
            value = int(generated_values[key])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{key} must be a non-negative integer") from exc
        if value < 0:
            raise ValueError(f"{key} must be non-negative, got {value}")
        at_values.append(value)
    if sum(at_values) != 100:
        raise ValueError(f"at1_percent..at6_percent must sum to 100, got {sum(at_values)}")

    statement_sha = sha256_path(statement)
    if statement_sha != args.statement_sha256:
        raise ValueError(
            f"native statement SHA mismatch: expected {args.statement_sha256}, got {statement_sha}"
        )

    with zipfile.ZipFile(jar) as archive:
        class_name = "com/hybench/workload/APClient.class"
        try:
            ap_client = archive.read(class_name)
        except KeyError as exc:
            raise ValueError(f"clean JAR missing {class_name}") from exc
    binary_forbidden = sorted(key for key in XP_FORBIDDEN_KEYS if key.encode() in ap_client)
    if binary_forbidden:
        raise ValueError(f"clean JAR still contains forbidden selectors: {binary_forbidden}")
    missing_iq = [f"execIQ{i}" for i in range(1, 7) if f"execIQ{i}".encode() not in ap_client]
    if missing_iq:
        raise ValueError(f"clean JAR missing native IQ methods: {missing_iq}")

    return {
        "contract": "pg_workload_policy_xp_v1",
        "status": "PASS",
        "source_props": str(source),
        "source_props_sha256": sha256_path(source),
        "generated_props": str(generated),
        "generated_props_sha256": sha256_path(generated),
        "changed_keys": changed,
        "workload_values": {key: generated_values[key] for key in sorted(XP_WORKLOAD_KEYS)},
        "operational_changes": sorted(set(changed).intersection(XP_OPERATIONAL_KEYS)),
        "statement_file": str(statement),
        "statement_sha256": statement_sha,
        "jar": str(jar),
        "jar_sha256": sha256_path(jar),
        "native_iq_methods": [f"execIQ{i}" for i in range(1, 7)],
        "forbidden_selector_capability_absent": True,
    }


def validate_job(args: argparse.Namespace) -> Dict[str, object]:
    sql_dir = Path(args.sql_dir).resolve()
    if not sql_dir.is_dir():
        raise ValueError(f"JOB SQL directory missing: {sql_dir}")
    files = sorted(
        (path for path in sql_dir.glob("*.sql") if re.fullmatch(r"\d+[a-z]\.sql", path.name)),
        key=lambda path: (int(re.match(r"\d+", path.name).group()), path.name),
    )
    if len(files) != 113:
        raise ValueError(f"canonical JOB requires exactly 113 SQL files, found {len(files)}")
    names = [path.name for path in files]
    if len(set(names)) != 113:
        raise ValueError("canonical JOB SQL filenames are not unique")
    empty = [path.name for path in files if path.stat().st_size == 0]
    if empty:
        raise ValueError(f"canonical JOB contains empty SQL files: {empty}")
    entries = [
        {"name": path.name, "sha256": sha256_path(path), "size": path.stat().st_size}
        for path in files
    ]
    manifest_digest = hashlib.sha256(
        json.dumps(entries, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return {
        "contract": "pg_workload_policy_job_canonical_113_v1",
        "status": "PASS",
        "sql_dir": str(sql_dir),
        "sql_count": 113,
        "unique_sql_count": 113,
        "manifest_sha256": manifest_digest,
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    xp = subparsers.add_parser("xp")
    xp.add_argument("--source-props", required=True)
    xp.add_argument("--generated-props", required=True)
    xp.add_argument("--statement-file", required=True)
    xp.add_argument("--statement-sha256", required=True)
    xp.add_argument("--jar", required=True)

    job = subparsers.add_parser("job")
    job.add_argument("--sql-dir", required=True)

    args = parser.parse_args()
    try:
        result = validate_xp(args) if args.command == "xp" else validate_job(args)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False), flush=True)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
