#!/usr/bin/env python3
"""Prepare or run the isolated HyBench XP/IO setlocal direction calibration."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
from typing import Any, Dict, Mapping, Sequence

import hyxp_setlocal_contract as contract


def run(command: Sequence[str], *, env: Mapping[str, str]) -> None:
    process = subprocess.Popen(
        list(command),
        env=dict(env),
        start_new_session=True,
    )
    try:
        returncode = process.wait()
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=30)
        raise
    if returncode != 0:
        raise RuntimeError(f"command failed ({returncode}): {list(command)}")


def global_readback() -> Dict[str, float]:
    sql = (
        "SELECT name, setting FROM pg_settings WHERE name IN "
        "('seq_page_cost','random_page_cost','cpu_tuple_cost',"
        "'cpu_index_tuple_cost','cpu_operator_cost','parallel_setup_cost',"
        "'parallel_tuple_cost') ORDER BY name"
    )
    env = os.environ.copy()
    env["PGPASSWORD"] = "postgres"
    completed = subprocess.run(
        [
            "psql",
            "-h",
            "127.0.0.1",
            "-p",
            str(contract.PROFILE["port"]),
            "-U",
            "postgres",
            "-d",
            str(contract.PROFILE["database"]),
            "-AtF",
            "|",
            "-c",
            sql,
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    result = {}
    for line in completed.stdout.splitlines():
        name, value = line.split("|", 1)
        result[name] = float(value)
    return contract.validate_global_readback(result)


def props_for_point(
    roles: Mapping[str, Path],
    point_root: Path,
) -> tuple[Path, Path]:
    warmup = point_root / "hybench_warmup_setlocal.props"
    measurement = point_root / "hybench_measurement_setlocal.props"
    contract.rewrite_props(roles["base_warmup_props"], warmup)
    contract.rewrite_props(roles["base_measurement_props"], measurement)
    return warmup, measurement


def point_environment(
    roles: Mapping[str, Path],
    *,
    params: Mapping[str, Any],
    audit_path: Path,
    run_id: str,
    phase: str,
) -> Dict[str, str]:
    hybench_root = roles["hybench_jar"].parent.parent
    shadow_root = roles["shadow_launcher"].parent
    env = os.environ.copy()
    env.update(
        {
            "HYXP_DB_LEASE_HELD": "1",
            "HYXP_SETLOCAL_DRIVER_JAR": str(roles["setlocal_driver_jar"]),
            "HYXP_HYBENCH_ROOT": str(hybench_root),
            "HYXP_SETLOCAL_PARAMETERS": contract.parameter_environment(params),
            "HYXP_SETLOCAL_AUDIT_PATH": str(audit_path),
            "HYXP_SETLOCAL_RUN_ID": run_id,
            "HYXP_SETLOCAL_PHASE": phase,
            "HYXP_FROZEN_MEASUREMENT_WRAPPER": str(
                roles["frozen_measurement_wrapper"]
            ),
            "HYXP_SETLOCAL_SHADOW_ROOT": str(shadow_root),
        }
    )
    return env


def run_point(
    *,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    calibration_id: str,
    params: Mapping[str, Any],
    point_root: Path,
) -> Dict[str, Any]:
    if point_root.exists():
        raise RuntimeError(f"point directory exists; refuse overwrite: {point_root}")
    point_root.mkdir(parents=True, mode=0o700)
    roles = contract.artifacts_by_role(manifest)
    warmup_props, measurement_props = props_for_point(roles, point_root)
    run_id = f"{calibration_id}-{os.getpid()}"
    before_path = point_root / "global_defaults_before.json"

    lock_handle = contract.DB_LOCK.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_handle.close()
        raise RuntimeError(f"HyBench XP database lane is leased: {contract.DB_LOCK}") from exc
    try:
        restore_env = os.environ.copy()
        restore_env["HYXP_DB_LEASE_HELD"] = "1"
        run([str(roles["snapshot_restore"])], env=restore_env)

        contract.atomic_json(before_path, global_readback())

        warmup_audit = point_root / "warmup_setlocal_audit.jsonl"
        warmup_env = point_environment(
            roles,
            params=params,
            audit_path=warmup_audit,
            run_id=run_id,
            phase="warmup",
        )
        run(
            [
                str(roles["shadow_launcher"]),
                "-t",
                "runxp",
                "-c",
                str(warmup_props),
                "-f",
                str(roles["hybench_statements"]),
            ],
            env=warmup_env,
        )
        contract.validate_audit(
            warmup_audit,
            run_id=run_id,
            phase="warmup",
            params=params,
        )

        measurement_audit = point_root / "measurement_setlocal_audit.jsonl"
        measurement_env = point_environment(
            roles,
            params=params,
            audit_path=measurement_audit,
            run_id=run_id,
            phase="measurement",
        )
        measurement_root = point_root / "measurement"
        run(
            [
                os.environ.get("HYXP_PYTHON", "/home/anonymous/miniconda3/envs/py310/bin/python3"),
                "-u",
                str(roles["measurement_adapter"]),
                "-t",
                "runxp",
                "-c",
                str(measurement_props),
                "-f",
                str(roles["hybench_statements"]),
                "--output-root",
                str(measurement_root),
                "--container",
                "pg5v1_hyxp_compute00_59630_w1",
                "--port",
                "59630",
                "--minimum-measurement-seconds",
                "180",
                "--measurement-seconds",
                "600",
                "--minimum-windows",
                "6",
                "--coverage-min",
                "0.90",
                "--io-cv-max",
                "0.10",
                "--cpu-quota-cores",
                "10.5",
                "--memory-bytes",
                "30404000000",
                "--io-read-bps",
                "100000",
                "--io-write-bps",
                "837500",
                "--target-resource",
                "io",
            ],
            env=measurement_env,
        )
        summaries = sorted(measurement_root.glob("*/summary.json"))
        if len(summaries) != 1:
            raise RuntimeError(f"expected one measurement summary, got {len(summaries)}")
        summary_path = summaries[0]

        after_path = point_root / "global_defaults_after.json"
        contract.atomic_json(after_path, global_readback())
        seal = contract.point_seal(
            manifest_path=manifest_path,
            audit_path=measurement_audit,
            global_before_path=before_path,
            global_after_path=after_path,
            summary_path=summary_path,
            run_id=run_id,
            params=params,
        )
        seal.update(
            {
                "calibration_id": calibration_id,
                "warmup_audit_sha256": contract.sha256_path(warmup_audit),
            }
        )
        seal_path = point_root / "setlocal_point_seal.json"
        contract.atomic_json(seal_path, seal)
        row = {
            "calibration_id": calibration_id,
            "status": "usable",
            "params": contract.validate_params(params),
            "seal_path": str(seal_path.resolve()),
            "seal_sha256": contract.sha256_path(seal_path),
            "manifest_sha256": contract.sha256_path(manifest_path),
            "raw_result": seal["raw_result"],
            "current_run_only": True,
            "used_for_optimizer_candidate_selection": False,
            "legacy_calibration_evidence_consumed": False,
        }
        if calibration_id.endswith("_baseline"):
            row["event"] = "setlocal_fresh_baseline"
        else:
            matched = next(
                probe
                for probe in contract.probes()
                if calibration_id.endswith(probe["calibration_id"])
            )
            row.update(
                {
                    "event": "fresh_model_calibration_replay",
                    "phase": "coordinate_axis_direction_calibration",
                    "design": "one_parameter_at_a_time_endpoints",
                    "parameter": matched["parameter"],
                    "direction": matched["direction"],
                    "changed_parameters": [matched["parameter"]],
                }
            )
        return row
    finally:
        try:
            if before_path.is_file():
                exit_path = point_root / "global_defaults_exit.json"
                exit_defaults = global_readback()
                contract.atomic_json(exit_path, exit_defaults)
                if contract.read_json(before_path) != exit_defaults:
                    raise RuntimeError(
                        "global planner defaults changed before point exit"
                    )
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()


def completed_rows(path: Path, manifest_sha: str) -> Dict[str, Dict[str, Any]]:
    result = {}
    if not path.is_file():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        calibration_id = str(row.get("calibration_id") or "")
        if not calibration_id or calibration_id in result:
            raise RuntimeError(f"duplicate/empty calibration id in ledger: {row}")
        seal = Path(str(row.get("seal_path") or ""))
        sealed = contract.read_json(seal) if seal.is_file() else {}
        if (
            row.get("status") != "usable"
            or row.get("manifest_sha256") != manifest_sha
            or not seal.is_file()
            or contract.sha256_path(seal) != row.get("seal_sha256")
            or sealed.get("status") != "usable"
            or sealed.get("manifest_sha256") != manifest_sha
            or sealed.get("calibration_id") != calibration_id
            or sealed.get("requested_parameters") != row.get("params")
            or sealed.get("raw_result") != row.get("raw_result")
            or sealed.get("global_defaults", {}).get("unchanged") is not True
            or sealed.get("candidate_parameter_application", {}).get(
                "tp_ap_connections_verified"
            ) is not True
        ):
            raise RuntimeError(f"invalid existing setlocal calibration row: {row}")
        result[calibration_id] = row
    return result


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def calibrate(args: argparse.Namespace) -> int:
    manifest_path = args.manifest.resolve()
    manifest = contract.verify_manifest(manifest_path)
    manifest_sha = contract.sha256_path(manifest_path)
    output = contract.OUTPUT_ROOT / "calibration"
    planned = []
    for scope in range(1, args.blocks + 1):
        planned.append(
            (
                f"scope{scope:02d}_baseline",
                dict(contract.DEFAULTS),
                output / f"scope_{scope:02d}/baseline",
            )
        )
        for probe in contract.probes():
            planned.append(
                (
                    f"scope{scope:02d}_{probe['calibration_id']}",
                    probe["params"],
                    output
                    / f"scope_{scope:02d}/points/{probe['calibration_id']}",
                )
            )
    ledger = output / "setlocal_fresh_model_calibration.jsonl"
    completed = completed_rows(ledger, manifest_sha)
    pending = [item for item in planned if item[0] not in completed]
    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "contract": manifest["contract_id"],
                    "manifest_sha256": manifest_sha,
                    "planned_points": len(planned),
                    "completed_points": len(completed),
                    "pending_ids": [item[0] for item in pending],
                    "execute_required": True,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    if completed and not args.resume:
        raise RuntimeError("existing setlocal calibration requires --resume")
    for calibration_id, params, point_root in pending:
        row = run_point(
            manifest_path=manifest_path,
            manifest=manifest,
            calibration_id=calibration_id,
            params=params,
            point_root=point_root,
        )
        append_jsonl(ledger, row)
    final_rows = completed_rows(ledger, manifest_sha)
    if len(final_rows) != len(planned):
        raise RuntimeError(
            f"setlocal calibration incomplete: {len(final_rows)}/{len(planned)}"
        )
    calibration_rows = [
        row for calibration_id, row in final_rows.items()
        if not calibration_id.endswith("_baseline")
    ]
    if len(calibration_rows) != args.blocks * 14:
        raise RuntimeError("setlocal calibration does not contain 14 points per scope")
    seal_path = output / "setlocal_calibration_seal.json"
    seal = {
        "schema_version": 1,
        "status": "complete",
        "contract_id": manifest["contract_id"],
        "manifest_sha256": manifest_sha,
        "blocks": args.blocks,
        "baseline_points": args.blocks,
        "usable_calibration_points": len(calibration_rows),
        "ledger_path": str(ledger.resolve()),
        "ledger_sha256": contract.sha256_path(ledger),
        "point_seals": {
            calibration_id: row["seal_sha256"]
            for calibration_id, row in sorted(final_rows.items())
        },
        "candidate_parameter_application": "transaction_local_set_local",
        "tp_ap_immediate_readback_required": True,
        "global_defaults_unchanged_required": True,
        "legacy_calibration_evidence_consumed": False,
    }
    if seal_path.exists():
        existing = contract.read_json(seal_path)
        if contract.canonical_json(existing) != contract.canonical_json(seal):
            raise RuntimeError(f"existing calibration seal differs: {seal_path}")
    else:
        contract.atomic_json(seal_path, seal)
    print(
        json.dumps(
            {
                "status": "complete",
                "usable_calibration_points": len(calibration_rows),
                "seal": str(seal_path),
                "seal_sha256": contract.sha256_path(seal_path),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate-manifest")
    generate.add_argument("--output", type=Path, default=contract.MANIFEST_PATH)
    for name in (
        "driver_jar",
        "driver_source",
        "shadow_launcher",
        "measurement_adapter",
        "measurement_wrapper",
        "restore_script",
        "warmup_props",
        "measurement_props",
        "statements",
        "hybench_jar",
        "postgres_jar",
    ):
        generate.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--manifest", type=Path, default=contract.MANIFEST_PATH)
    calibration = commands.add_parser("calibrate")
    calibration.add_argument("--manifest", type=Path, default=contract.MANIFEST_PATH)
    calibration.add_argument("--blocks", type=int, default=3)
    calibration.add_argument("--execute", action="store_true")
    calibration.add_argument("--resume", action="store_true")
    return value


def main() -> int:
    args = parser().parse_args()
    if args.command == "generate-manifest":
        keyword = {
            name: getattr(args, name)
            for name in (
                "driver_jar",
                "driver_source",
                "shadow_launcher",
                "measurement_adapter",
                "measurement_wrapper",
                "restore_script",
                "warmup_props",
                "measurement_props",
                "statements",
                "hybench_jar",
                "postgres_jar",
            )
        }
        manifest = contract.generate_manifest(args.output, **keyword)
        print(
            json.dumps(
                {
                    "status": "manifest_generated",
                    "path": str(args.output.resolve()),
                    "sha256": contract.sha256_path(args.output),
                    "contract": manifest["contract_id"],
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    if args.command == "verify":
        contract.verify_manifest(args.manifest)
        print(
            json.dumps(
                {
                    "status": "verified",
                    "manifest_sha256": contract.sha256_path(args.manifest),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    if args.blocks < 1:
        raise RuntimeError("--blocks must be positive")
    return calibrate(args)


if __name__ == "__main__":
    raise SystemExit(main())
