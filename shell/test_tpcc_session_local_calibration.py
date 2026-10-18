#!/usr/bin/env python3
"""Regression tests for isolated TPC-C session-startup calibration evidence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET

from tpcc_session_local_calibration import (
    PARAMETERS,
    SEMANTICS,
    candidate_jdbc_url,
    canonical_params,
    prepare_bundle,
    seal_calibration,
)


PARAMS = {
    "seq_page_cost": 1,
    "random_page_cost": 4,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000,
    "parallel_tuple_cost": 0.1,
}


def expect_value_error(function: object, *args: object, **kwargs: object) -> None:
    try:
        function(*args, **kwargs)  # type: ignore[operator]
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def transaction_audit(
    kind: str,
    connection_id: str,
    transaction_ordinal: int,
    params: dict[str, str],
) -> list[dict[str, object]]:
    transaction_id = f"{connection_id}:{transaction_ordinal}"
    common: dict[str, object] = {
        "schema_version": 1,
        "connection_id": connection_id,
        "transaction_id": transaction_id,
        "transaction_ordinal": transaction_ordinal,
        "client_kind": kind,
        "calibration_id": "candidate-01",
        "application_name": f"ra-tpcc-{kind}-candidate-01",
    }
    return [
        {
            **common,
            "event": "tpcc_transaction_parameter_readback",
            "sequence": 1,
            "readback_phase": "after_set_local_before_business_sql",
            "settings": params,
        },
        {
            **common,
            "event": "tpcc_transaction_business_sql_dispatch",
            "sequence": 2,
            "sql_sha256": "synthetic",
        },
    ]


def run_java_transaction_wrapper_test(root: Path) -> None:
    harness = root / "TransactionWrapperHarness.java"
    harness.write_text(
        r'''
import java.lang.reflect.*;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import resourceadaptive.tpcc.SessionReadbackDriver;

public final class TransactionWrapperHarness {
    private static Object defaultValue(Class<?> type) {
        if (!type.isPrimitive()) return null;
        if (type == boolean.class) return false;
        if (type == byte.class) return (byte) 0;
        if (type == short.class) return (short) 0;
        if (type == int.class) return 0;
        if (type == long.class) return 0L;
        if (type == float.class) return 0.0f;
        if (type == double.class) return 0.0d;
        if (type == char.class) return '\0';
        return null;
    }

    public static void main(String[] args) throws Exception {
        Path audit = Path.of(args[0]);
        List<String> operations = new ArrayList<>();
        Map<String, String> params = new LinkedHashMap<>();
        params.put("seq_page_cost", "1");
        params.put("random_page_cost", "4");
        params.put("cpu_tuple_cost", "0.01");
        params.put("cpu_index_tuple_cost", "0.005");
        params.put("cpu_operator_cost", "0.0025000000000000001");
        params.put("parallel_setup_cost", "1000");
        params.put("parallel_tuple_cost", "0.10000000000000001");

        InvocationHandler resultHandler = new InvocationHandler() {
            boolean first = true;
            public Object invoke(Object proxy, Method method, Object[] methodArgs) {
                if (method.getName().equals("next")) {
                    boolean value = first;
                    first = false;
                    return value;
                }
                if (method.getName().equals("getString")) {
                    int index = (Integer) methodArgs[0];
                    if (index == 1) return "ra-tpcc-native-candidate-01";
                    return new ArrayList<>(params.values()).get(index - 2);
                }
                return defaultValue(method.getReturnType());
            }
        };
        InvocationHandler statementHandler = (proxy, method, methodArgs) -> {
            if (method.getName().equals("execute")) {
                operations.add((String) methodArgs[0]);
                return true;
            }
            if (method.getName().equals("executeQuery")) {
                operations.add("READBACK");
                resultHandler.getClass().getDeclaredField("first").setBoolean(resultHandler, true);
                return Proxy.newProxyInstance(
                    TransactionWrapperHarness.class.getClassLoader(),
                    new Class<?>[] {ResultSet.class},
                    resultHandler
                );
            }
            return defaultValue(method.getReturnType());
        };
        Statement rawStatement = (Statement) Proxy.newProxyInstance(
            TransactionWrapperHarness.class.getClassLoader(),
            new Class<?>[] {Statement.class},
            statementHandler
        );
        InvocationHandler preparedHandler = (proxy, method, methodArgs) -> {
            if (method.getName().equals("execute")) {
                operations.add("BUSINESS");
                return true;
            }
            return defaultValue(method.getReturnType());
        };
        PreparedStatement rawPrepared = (PreparedStatement) Proxy.newProxyInstance(
            TransactionWrapperHarness.class.getClassLoader(),
            new Class<?>[] {PreparedStatement.class},
            preparedHandler
        );
        InvocationHandler connectionHandler = (proxy, method, methodArgs) -> {
            return switch (method.getName()) {
                case "getAutoCommit" -> false;
                case "createStatement" -> rawStatement;
                case "prepareStatement" -> rawPrepared;
                case "commit" -> { operations.add("COMMIT"); yield null; }
                case "rollback" -> { operations.add("ROLLBACK"); yield null; }
                default -> defaultValue(method.getReturnType());
            };
        };
        Connection rawConnection = (Connection) Proxy.newProxyInstance(
            TransactionWrapperHarness.class.getClassLoader(),
            new Class<?>[] {Connection.class},
            connectionHandler
        );
        Connection wrapped = SessionReadbackDriver.wrapConnection(
            rawConnection,
            audit,
            "native",
            "candidate-01",
            params
        );
        PreparedStatement business = wrapped.prepareStatement("SELECT business()");
        business.execute();
        wrapped.commit();
        business.execute();
        wrapped.rollback();
        business.execute();
        wrapped.commit();

        List<String> expected = new ArrayList<>();
        for (int transaction = 0; transaction < 3; transaction++) {
            for (String parameter : params.keySet()) {
                expected.add("SET LOCAL " + parameter + " = " + params.get(parameter));
            }
            expected.add("READBACK");
            expected.add("BUSINESS");
            expected.add(transaction == 1 ? "ROLLBACK" : "COMMIT");
        }
        if (!operations.equals(expected)) {
            throw new AssertionError("unexpected operation order: " + operations);
        }
        List<String> auditLines = Files.readAllLines(audit);
        if (auditLines.size() != 6
            || !auditLines.get(0).contains("transaction_parameter_readback")
            || !auditLines.get(1).contains("business_sql_dispatch")
            || !auditLines.get(2).contains("transaction_parameter_readback")
            || !auditLines.get(3).contains("business_sql_dispatch")
            || !auditLines.get(4).contains("transaction_parameter_readback")
            || !auditLines.get(5).contains("business_sql_dispatch")) {
            throw new AssertionError("unexpected transaction audit: " + auditLines);
        }
    }
}
''',
        encoding="utf-8",
    )
    classes = root / "harness-classes"
    classes.mkdir()
    driver_source = (
        Path(__file__).with_name("tpcc_session_jdbc") / "SessionReadbackDriver.java"
    )
    jdbc_jar = Path("/home/anonymous/benchbase-postgres/lib/postgresql-42.7.4.jar")
    subprocess.run(
        [
            "javac",
            "-cp",
            str(jdbc_jar),
            "-d",
            str(classes),
            str(driver_source),
            str(harness),
        ],
        check=True,
    )
    subprocess.run(
        [
            "java",
            "-cp",
            f"{classes}:{jdbc_jar}",
            "TransactionWrapperHarness",
            str(root / "transaction-audit.jsonl"),
        ],
        check=True,
    )


def main() -> None:
    driver_source = (
        Path(__file__).with_name("tpcc_session_jdbc") / "SessionReadbackDriver.java"
    ).read_text(encoding="utf-8")
    assert "private static final Object AUDIT_LOCK" in driver_source
    assert "synchronized (AUDIT_LOCK)" in driver_source
    assert "while (buffer.hasRemaining())" in driver_source

    params = canonical_params(PARAMS)
    assert tuple(params) == PARAMETERS
    expect_value_error(canonical_params, {**PARAMS, "work_mem": 1})

    url = candidate_jdbc_url(
        "jdbc:postgresql://127.0.0.1:59640/fullgrid_tpcc?sslmode=disable",
        "native-candidate-01",
    )
    assert url.startswith("jdbc:ra-postgresql://")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url[len("jdbc:ra-") :]).query)
    assert query["ApplicationName"] == ["native-candidate-01"]
    assert "options" not in query
    expect_value_error(
        candidate_jdbc_url,
        "jdbc:postgresql://127.0.0.1:59640/fullgrid_tpcc?options=-c%20work_mem=1MB",
        "native-candidate-01",
    )

    with tempfile.TemporaryDirectory(prefix="tpcc-session-local-") as temp_dir:
        root = Path(temp_dir)
        os.environ["TPCC_SESSION_TEST_PASSWORD"] = "postgres"
        params_path = write_json(root / "params.json", PARAMS)
        xml_path = root / "base.xml"
        xml_path.write_text(
            """
            <parameters>
              <dbtype>POSTGRES</dbtype>
              <driver>org.postgresql.Driver</driver>
              <url>jdbc:postgresql://127.0.0.1:59640/fullgrid_tpcc?sslmode=disable</url>
              <username>postgres</username>
              <password>postgres</password>
              <scalefactor>1</scalefactor>
              <terminals>2</terminals>
              <works><work><time>10</time><weights>45,43,4,4,4</weights></work></works>
            </parameters>
            """,
            encoding="utf-8",
        )
        fake_benchbase = root / "benchbase.jar"
        fake_benchbase.write_bytes(b"not executed")
        bundle_dir = root / "bundle"
        manifest_path = prepare_bundle(
            argparse.Namespace(
                output_dir=bundle_dir,
                calibration_id="candidate-01",
                params=params_path,
                benchbase_xml=xml_path,
                benchbase_jar=fake_benchbase,
                postgresql_jdbc_jar=Path(
                    "/home/anonymous/benchbase-postgres/lib/postgresql-42.7.4.jar"
                ),
                database_user="postgres",
                database_password_env="TPCC_SESSION_TEST_PASSWORD",
                jdbc_url=(
                    "jdbc:postgresql://127.0.0.1:59640/fullgrid_tpcc?"
                    "sslmode=disable"
                ),
                native_terminals=2,
                sensitive_clients=1,
                duration_seconds=10,
                warehouses=10,
                java_xmx="-Xmx1g",
            )
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["parameter_semantics"] == SEMANTICS
        assert manifest["parameter_semantics"]["formal_contract"] == (
            "SET_LOCAL_plus_immediate_readback"
        )
        assert manifest["parameter_semantics"]["effective_scope"] == "transaction"
        assert manifest["parameter_semantics"]["transaction_local"] is True
        assert manifest["parameter_semantics"]["session_startup_options_used"] is False
        assert manifest["parameter_semantics"]["alter_system_used"] is False
        assert manifest["connection_contract"]["native"]["minimum_connection_readbacks"] == 2
        assert manifest["connection_contract"]["sensitive"]["implementation"].startswith(
            "dedicated JDBC"
        )
        assert manifest["connection_contract"]["sensitive"][
            "required_runtime_secret_environment"
        ] == ["TPCC_DB_PASSWORD"]
        generated_xml = ET.parse(bundle_dir / "benchbase_native_session.xml").getroot()
        assert generated_xml.findtext("scalefactor") == "10"
        assert generated_xml.findtext("terminals") == "2"
        assert generated_xml.findtext("works/work/time") == "10"
        assert generated_xml.findtext("driver") == "resourceadaptive.tpcc.SessionReadbackDriver"
        driver_jar = Path(manifest["build_contract"]["driver_jar"])
        assert driver_jar.is_file()
        native_command = manifest["connection_contract"]["native"]["command"]
        assert "com.oltpbenchmark.DBWorkload" in native_command
        assert any(str(driver_jar) in argument for argument in native_command)

        defaults = {
            "schema_version": 1,
            "artifact_type": "tpcc_pg_global_defaults_snapshot_v1",
            "auditor_connection_candidate_options": False,
            "parameters": {
                name: {"setting": params[name], "reset_val": params[name], "source": "default"}
                for name in PARAMETERS
            },
        }
        before = write_json(root / "before.json", defaults)
        after = write_json(root / "after.json", defaults)
        readbacks = root / "readbacks.jsonl"
        native_1_first = transaction_audit("native", "native-1", 1, params)
        native_2_first = transaction_audit("native", "native-2", 1, params)
        records = [
            native_1_first[0],
            native_2_first[0],
            native_1_first[1],
            native_2_first[1],
            *transaction_audit("native", "native-1", 2, params),
            *transaction_audit("sensitive", "sensitive-1", 1, params),
            *transaction_audit("sensitive", "sensitive-1", 2, params),
        ]
        records.append(transaction_audit("native", "native-1", 3, params)[0])
        readbacks.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        summary = write_json(
            root / "summary.json",
            {
                "status": "complete",
                "window_mean_stability": {"stable": True},
                "worker_cleanup": {"fully_released": True},
                "native_sql_exception_occurrences": 0,
            },
        )
        seal_path = root / "seal.json"
        seal_calibration(
            argparse.Namespace(
                manifest=manifest_path,
                readbacks=readbacks,
                defaults_before=before,
                defaults_after=after,
                measurement_summary=summary,
                output=seal_path,
            )
        )
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
        assert seal["artifact_type"] == "tpcc_session_local_calibration_seal_v1"
        assert seal["usable"] is True
        assert seal["global_defaults"]["unchanged"] is True
        assert seal["connection_readback_audit"]["native"]["observed_connections"] == 2
        native_audit = seal["connection_readback_audit"]["native"]
        assert native_audit["observed_transactions"] == 4
        assert native_audit["observed_complete_transactions"] == 3
        assert native_audit["aborted_before_business_transactions"] == 1
        assert seal["parameter_semantics"]["transaction_local"] is True

        non_trailing_records = [
            *transaction_audit("native", "native-1", 1, params),
            transaction_audit("native", "native-1", 2, params)[0],
            *transaction_audit("native", "native-1", 3, params),
            *transaction_audit("native", "native-2", 1, params),
            *transaction_audit("sensitive", "sensitive-1", 1, params),
        ]
        non_trailing_path = root / "non-trailing-readback.jsonl"
        non_trailing_path.write_text(
            "".join(json.dumps(record) + "\n" for record in non_trailing_records),
            encoding="utf-8",
        )
        expect_value_error(
            seal_calibration,
            argparse.Namespace(
                manifest=manifest_path,
                readbacks=non_trailing_path,
                defaults_before=before,
                defaults_after=after,
                measurement_summary=summary,
                output=root / "must-not-exist-non-trailing.json",
            ),
        )

        session_only_manifest = json.loads(json.dumps(manifest))
        session_only_manifest["parameter_semantics"] = {
            **SEMANTICS,
            "effective_scope": "session",
            "transaction_local": False,
            "session_startup_options_used": True,
        }
        session_only_path = write_json(
            root / "session-only-manifest.json",
            session_only_manifest,
        )
        expect_value_error(
            seal_calibration,
            argparse.Namespace(
                manifest=session_only_path,
                readbacks=readbacks,
                defaults_before=before,
                defaults_after=after,
                measurement_summary=summary,
                output=root / "must-not-exist-session-only.json",
            ),
        )

        changed = json.loads(json.dumps(defaults))
        changed["parameters"]["random_page_cost"]["setting"] = "5"
        changed_after = write_json(root / "changed-after.json", changed)
        expect_value_error(
            seal_calibration,
            argparse.Namespace(
                manifest=manifest_path,
                readbacks=readbacks,
                defaults_before=before,
                defaults_after=changed_after,
                measurement_summary=summary,
                output=root / "must-not-exist.json",
            ),
        )

        bad_records = list(records)
        bad_records[:2] = transaction_audit(
            "native",
            "native-bad",
            1,
            {**params, "random_page_cost": "5"},
        )
        bad_readbacks = root / "bad-readbacks.jsonl"
        bad_readbacks.write_text(
            "".join(json.dumps(record) + "\n" for record in bad_records),
            encoding="utf-8",
        )
        expect_value_error(
            seal_calibration,
            argparse.Namespace(
                manifest=manifest_path,
                readbacks=bad_readbacks,
                defaults_before=before,
                defaults_after=after,
                measurement_summary=summary,
                output=root / "must-not-exist-2.json",
            ),
        )

        run_java_transaction_wrapper_test(root)

    sources = [
        Path(__file__).with_name("tpcc_session_local_calibration.py"),
        Path(__file__).with_name("tpcc_session_jdbc") / "SessionReadbackDriver.java",
        Path(__file__).with_name("tpcc_session_jdbc") / "TpccSensitiveSessionClient.java",
    ]
    joined = "\n".join(path.read_text(encoding="utf-8") for path in sources).upper()
    assert "ALTER SYSTEM" not in joined
    print("TPC-C session-local calibration regression passed", flush=True)


if __name__ == "__main__":
    main()
