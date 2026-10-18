#!/usr/bin/env python3
"""Offline tests for the HyBench XP/IO setlocal calibration package."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import shutil

import hyxp_setlocal_contract as contract


ROOT = Path(__file__).resolve().parent
DRIVER_SOURCE = (
    ROOT
    / "hyxp_setlocal_driver/com/resourceadaptive/hyxp/SetLocalPostgresDriver.java"
)
PROBE_SOURCE = ROOT / "hyxp_setlocal_driver/SetLocalPostgresDriverProbe.java"


def test_offline_jdbc_transactions_and_concurrent_audit() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        classes = root / "classes"
        classes.mkdir()
        subprocess.run(
            [
                "javac",
                "--release",
                "11",
                "-d",
                str(classes),
                str(DRIVER_SOURCE),
                str(PROBE_SOURCE),
            ],
            check=True,
        )
        audit = root / "audit.jsonl"
        env = os.environ.copy()
        env.update(
            {
                "HYXP_SETLOCAL_PARAMETERS": contract.parameter_environment(
                    contract.DEFAULTS
                ),
                "HYXP_SETLOCAL_AUDIT_PATH": str(audit),
                "HYXP_SETLOCAL_RUN_ID": "offline-probe",
                "HYXP_SETLOCAL_PHASE": "measurement",
            }
        )
        completed = subprocess.run(
            ["java", "-cp", str(classes), "SetLocalPostgresDriverProbe"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        assert "HYXP_SETLOCAL_JDBC_PROBE_OK" in completed.stdout
        assert "HYXP_SETLOCAL_CONCURRENT_AUDIT_OK" in completed.stdout
        seal = contract.validate_audit(
            audit,
            run_id="offline-probe",
            phase="measurement",
            params=contract.DEFAULTS,
        )
        assert seal["rows"] == 259
        assert seal["roles"] == ["ap", "tp"]
        rows = [
            json.loads(line)
            for line in audit.read_text(encoding="utf-8").splitlines()
        ]
        assert len(rows) == 259
        by_connection = {}
        for row in rows:
            by_connection.setdefault(row["connection_id"], []).append(
                row["transaction_sequence"]
            )
        assert len(by_connection) == 34
        for sequences in by_connection.values():
            assert sequences == list(range(1, len(sequences) + 1))
        lengths = sorted(len(sequences) for sequences in by_connection.values())
        assert lengths.count(8) == 32
        assert lengths[:2] == [1, 2]


def test_props_are_isolated_driver_urls() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "base.props"
        destination = root / "setlocal.props"
        source.write_text(
            "classname=org.postgresql.Driver\n"
            "classname_ap=org.postgresql.Driver\n"
            "url=jdbc:postgresql://127.0.0.1:59630/hybench_sf10x\n"
            "url_tp=jdbc:postgresql://127.0.0.1:59630/hybench_sf10x\n"
            "url_ap=jdbc:postgresql://127.0.0.1:59630/hybench_sf10x\n",
            encoding="utf-8",
        )
        contract.rewrite_props(source, destination)
        text = destination.read_text(encoding="utf-8")
        assert text.count("com.resourceadaptive.hyxp.SetLocalPostgresDriver") == 2
        assert "jdbc:hyxp-setlocal:tp:jdbc:postgresql:" in text
        assert "jdbc:hyxp-setlocal:ap:jdbc:postgresql:" in text
        assert source.read_text(encoding="utf-8").startswith(
            "classname=org.postgresql.Driver"
        )


def test_operational_sources_forbid_global_parameter_writes() -> None:
    prohibited = "ALTER" + " SYSTEM"
    paths = (
        DRIVER_SOURCE,
        ROOT / "build_hyxp_setlocal_driver.sh",
        ROOT / "hyxp_setlocal_shadow_launcher.sh",
        ROOT / "hyxp_setlocal_measurement_adapter.py",
        ROOT / "run_hyxp_setlocal_direction_calibration.py",
    )
    for path in paths:
        assert prohibited.lower() not in path.read_text(encoding="utf-8").lower()


def test_shadow_launcher_runs_from_hybench_root() -> None:
    launcher = (
        ROOT / "hyxp_setlocal_shadow_launcher.sh"
    ).read_text(encoding="utf-8")
    assert 'cd "$HYXP_HYBENCH_ROOT"' in launcher
    assert "com.hybench.HyBench" in launcher


def test_probe_registry() -> None:
    probes = contract.probes()
    assert len(probes) == 14
    assert {probe["parameter"] for probe in probes} == set(contract.PARAMETERS)
    for probe in probes:
        assert set(probe["params"]) == set(contract.PARAMETERS)


def test_manifest_seals_compiled_driver() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        driver_jar = root / "hyxp_setlocal_driver.jar"
        subprocess.run(
            [
                "bash",
                str(ROOT / "build_hyxp_setlocal_driver.sh"),
                str(driver_jar),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        shadow = root / "shadow/hybench_unblocked"
        shadow.parent.mkdir()
        shutil.copyfile(ROOT / "hyxp_setlocal_shadow_launcher.sh", shadow)
        fixtures = {}
        for name in (
            "measurement_adapter",
            "measurement_wrapper",
            "restore_script",
            "statements",
            "hybench_jar",
            "postgres_jar",
        ):
            path = root / name
            path.write_text(f"{name}\n", encoding="utf-8")
            fixtures[name] = path
        props = (
            "classname=org.postgresql.Driver\n"
            "classname_ap=org.postgresql.Driver\n"
            "url=jdbc:postgresql://127.0.0.1/db\n"
            "url_tp=jdbc:postgresql://127.0.0.1/db\n"
            "url_ap=jdbc:postgresql://127.0.0.1/db\n"
        )
        warmup_props = root / "warmup.props"
        measurement_props = root / "measurement.props"
        warmup_props.write_text(props, encoding="utf-8")
        measurement_props.write_text(props, encoding="utf-8")
        original_output = contract.OUTPUT_ROOT
        contract.OUTPUT_ROOT = root / "attempt41"
        try:
            manifest_path = contract.OUTPUT_ROOT / "sealed/manifest.json"
            contract.generate_manifest(
                manifest_path,
                driver_jar=driver_jar,
                driver_source=DRIVER_SOURCE,
                shadow_launcher=shadow,
                measurement_adapter=fixtures["measurement_adapter"],
                measurement_wrapper=fixtures["measurement_wrapper"],
                restore_script=fixtures["restore_script"],
                warmup_props=warmup_props,
                measurement_props=measurement_props,
                statements=fixtures["statements"],
                hybench_jar=fixtures["hybench_jar"],
                postgres_jar=fixtures["postgres_jar"],
            )
            verified = contract.verify_manifest(manifest_path)
            assert verified["application_contract"]["autocommit_allowed"] is False
            driver_jar.write_bytes(driver_jar.read_bytes() + b"tamper")
            try:
                contract.verify_manifest(manifest_path)
            except RuntimeError as exc:
                assert "SHA changed" in str(exc)
            else:
                raise AssertionError("tampered setlocal driver jar was accepted")
        finally:
            contract.OUTPUT_ROOT = original_output


def main() -> int:
    test_offline_jdbc_transactions_and_concurrent_audit()
    test_props_are_isolated_driver_urls()
    test_operational_sources_forbid_global_parameter_writes()
    test_probe_registry()
    test_manifest_seals_compiled_driver()
    print("HyBench XP setlocal calibration tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
