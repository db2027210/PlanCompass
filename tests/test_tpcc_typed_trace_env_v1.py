from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CLASSPATH = os.pathsep.join(
    (
        str(ROOT / "runtime/benchbase-formal.jar"),
        str(ROOT / "runtime/lib/*"),
    )
)
PREFLIGHT = "com.oltpbenchmark.TpccTypedTracePreflightMain"


def _environment(tmp_path: Path, *, role: str) -> dict[str, str]:
    manifest = tmp_path / "manifest.json"
    if not manifest.exists():
        manifest.write_text('{"binding_count":34}\n', encoding="utf-8")
    environment = os.environ.copy()
    environment.update(
        {
            "MYSQL_TPCC_TYPED_TRACE_PATH": str(tmp_path / "trace.jsonl"),
            "MYSQL_TPCC_TYPED_MANIFEST_PATH": str(manifest),
            "MYSQL_TPCC_TYPED_MANIFEST_SHA256": hashlib.sha256(
                manifest.read_bytes()
            ).hexdigest(),
            "MYSQL_TPCC_TYPED_CAPTURE_TOKEN": "capture-token-v1",
            "MYSQL_TPCC_TYPED_METRICS_JDBC_URL": "jdbc:mysql://127.0.0.1:1/db",
            "MYSQL_TPCC_TYPED_DEFAULTS_FILE": "/opt/mysql-tpcc-config/mysql.cnf",
            "MYSQL_TPCC_TYPED_CORRECTNESS_VERIFIED": "1",
            "MYSQL_TPCC_TYPED_CAPTURE_ONLY": "1",
            "MYSQL_TPCC_TYPED_OUTER_AUTHORIZED": "0",
            "MYSQL_TPCC_TYPED_TRACE_ROLE": role,
        }
    )
    if role == "JOINER":
        authority = (
            tmp_path / "trace.jsonl.sensitive_join_authority.json"
        )
        value = {
            "schema_version":
                "mysql_tpcc_sensitive_stock_level_real_join_authority_v1",
            "status": "PASS_REAL_OWNER_TRACE_JOIN",
            "binding_id":
                "34:SensitiveStockLevel:"
                "SensitiveStockLevel/item-in-orderline-w1",
            "trace_header": {
                "capture_token": environment[
                    "MYSQL_TPCC_TYPED_CAPTURE_TOKEN"
                ],
                "manifest_sha256": environment[
                    "MYSQL_TPCC_TYPED_MANIFEST_SHA256"
                ],
            },
            "qps_resource_input_prohibited": True,
            "synthetic_or_fabricated_join_prohibited": True,
            "outer_execution_authorized": False,
            "accepted_round_authorized": False,
        }
        authority.write_text(
            json.dumps(value, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        environment["MYSQL_TPCC_TYPED_JOIN_AUTHORITY_PATH"] = str(authority)
        environment["MYSQL_TPCC_TYPED_JOIN_AUTHORITY_SHA256"] = (
            hashlib.sha256(authority.read_bytes()).hexdigest()
        )
    return environment


def _run(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["java", "-cp", CLASSPATH, PREFLIGHT],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_owner_requires_fresh_trace(tmp_path: Path) -> None:
    environment = _environment(tmp_path, role="OWNER")
    first = _run(environment)
    assert first.returncode == 0, first.stderr
    (tmp_path / "trace.jsonl").write_text("{}\n", encoding="utf-8")
    stale = _run(environment)
    assert stale.returncode != 0
    assert "output is not fresh" in stale.stderr


def test_joiner_requires_exact_owner_header_and_lock(tmp_path: Path) -> None:
    environment = _environment(tmp_path, role="JOINER")
    absent = _run(environment)
    assert absent.returncode != 0
    assert "join authority is absent" in absent.stderr

    trace = tmp_path / "trace.jsonl"
    header = {
        "record_type": "header",
        "schema_version": "mysql_tpcc_jdbc_typed_trace_v1",
        "manifest_sha256": environment["MYSQL_TPCC_TYPED_MANIFEST_SHA256"],
        "capture_token": environment["MYSQL_TPCC_TYPED_CAPTURE_TOKEN"],
        "capture_only": True,
        "outer_execution_authorized": False,
        "correctness_verified": True,
        "qps_included": False,
    }
    trace.write_text(
        json.dumps(header, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    (tmp_path / "trace.jsonl.lock").write_text("", encoding="utf-8")
    joined = _run(environment)
    assert joined.returncode == 0, joined.stderr

    header["capture_token"] = "different-token"
    trace.write_text(
        json.dumps(header, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    mismatch = _run(environment)
    assert mismatch.returncode != 0
    assert "join header differs" in mismatch.stderr


def test_typed_sources_require_the_sealed_defaults_file_not_credential_environment() -> None:
    trace = (ROOT / "source/TpccTypedTrace.java").read_text(encoding="utf-8")
    sensitive = (ROOT / "source/TpccSensitiveTypedMain.java").read_text(encoding="utf-8")
    assert 'MYSQL_TPCC_TYPED_DEFAULTS_FILE' in trace
    assert 'Path.of("/opt/mysql-tpcc-config/mysql.cnf")' in trace
    assert "connectionFromSealedDefaults(METRICS_URL)" in trace
    assert "MYSQL_TPCC_TYPED_METRICS_USER" not in trace
    assert "MYSQL_TPCC_TYPED_METRICS_PASSWORD" not in trace
    assert "connectionFromSealedDefaults" in sensitive
    assert "MYSQL_TPCC_TYPED_TARGET_USER" not in sensitive
    assert "MYSQL_TPCC_TYPED_TARGET_PASSWORD" not in sensitive


def test_joiner_requires_hash_bound_real_sensitive_join_authority() -> None:
    trace = (ROOT / "source/TpccTypedTrace.java").read_text(encoding="utf-8")
    orchestration = (
        ROOT / "orchestration/formal_tpcc_composite_workload.py"
    ).read_text(encoding="utf-8")
    assert "MYSQL_TPCC_TYPED_JOIN_AUTHORITY_PATH" in trace
    assert "MYSQL_TPCC_TYPED_JOIN_AUTHORITY_SHA256" in trace
    assert "PASS_REAL_OWNER_TRACE_JOIN" in trace
    assert "synthetic_or_fabricated_join_prohibited" in trace
    assert "enable_typed_consumers_before_workload" in orchestration
    assert "PASS_ENABLED_BEFORE_WORKLOAD_START" in orchestration
    assert "wait_for_real_typed_join_authority" in orchestration
    assert "qps_resource_input_prohibited" in orchestration
