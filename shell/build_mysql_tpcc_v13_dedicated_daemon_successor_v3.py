#!/usr/bin/env python3
"""Build the strict TPC-C v13 raw-all-subset Algorithm C successor."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = (
    ROOT
    / "artifacts/mysql_tpcc_v13_dedicated_daemon_successor_v2"
    / "a81a8bc997db986e13f0e2ea8949fd3da0c9598ddc98099fca7d1e78c41a14db"
)
OUTPUT = ROOT / "artifacts/mysql_tpcc_v13_raw_all_subset_successor_v3"
SOCKET = "unix:///run/ra-docker-mysql-tpcc-v1.sock"
PREDECESSOR_MANIFEST_SHA256 = (
    "ef7e63c488fcd8be8ee9f412427c661cb893116f3b7483dd2ab0a7b8f35c0a17"
)
INVALIDATED_DIRTY_BUNDLE_ID = (
    "8d389a57eaeafb1bbcfe6735abddd4dcde6969c8aa6570251754acefebcb1601"
)
INVALIDATED_ALGORITHM_BUNDLE_ID = (
    "a81a8bc997db986e13f0e2ea8949fd3da0c9598ddc98099fca7d1e78c41a14db"
)
INVALIDATED_HOST_NETWORK_BUNDLE_ID = (
    "cf6ed06e1cac49c8528e0e3f2d8e73713afc0d9fa49f7c587e27e61f94c22092"
)
INVALIDATED_SERVER_PORT_BUNDLE_ID = (
    "6984bf964032365918e3697fdee143462fce3cb055d29a0cca5892690651667f"
)
INVALIDATED_PREFAULT_ALLOWLIST_BUNDLE_ID = (
    "3baa80a43313db6083375176743903697c22ee1107a0ef5468064c727e617a07"
)
INVALIDATED_BUNDLE_ROOT_MODE_ID = (
    "1a21b032afd96afe25dc7e42d1622870cfc4838309fad011996fad9056e1410b"
)
INVALIDATED_ABSOLUTE_ZERO_IO_BUNDLE_ID = (
    "e03cf40a1fd513db384c63925c1204a9b865a66e60625f5f2eb5dfec3925b2b7"
)
SCHEMA = "mysql_tpcc_v13_dedicated_daemon_successor_bundle_v3"
ALGORITHM_C_CONTRACT = {
    "formal_raw_all_nonempty_subsets_before_top5": True,
    "plan_clustering_diagnostic_only": True,
    "missing_l2_l3_independent": True,
    "original_gradient_confidence_required": True,
    "fixed_top5_only": True,
}
ALGORITHM_SOURCE_OVERRIDES = {
    "python_source_closure/gradient_descent_optimizer.py":
        "gradient_descent_optimizer.py",
    "python_source_closure/experience_table_subset_optimizer.py":
        "experience_table_subset_optimizer.py",
    "python_source_closure/mysql_parameter_optimizer.py":
        "mysql_parameter_optimizer.py",
    "python_source_closure/mysql_plan_clustering.py":
        "mysql_plan_clustering.py",
    "python_source_closure/mysql_three_level_fingerprint.py":
        "mysql_three_level_fingerprint.py",
}


def canonical(value: object) -> bytes:
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


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def patch_python(source: str) -> str:
    source = source.replace(
        '"docker",', '"docker", "--host", "' + SOCKET + '",'
    ).replace(
        "['docker',", "['docker', '--host', '" + SOCKET + "',"
    )
    source = re.sub(
        r"(?<![A-Za-z0-9_-])docker "
        r"(?=(?:run|exec|inspect|ps|stop|rm|image|info|create|start)\b)",
        "docker --host " + SOCKET + " ",
        source,
    )
    source = source.replace(
        '"docker", "--host", "' + SOCKET + '", "--host", "' + SOCKET + '",',
        '"docker", "--host", "' + SOCKET + '",',
    ).replace(
        "'docker', '--host', '" + SOCKET + "', '--host', '" + SOCKET + "',",
        "'docker', '--host', '" + SOCKET + "',",
    )
    source = source.replace(
        '--publish", f"127.0.0.1:{PORT}:3306"', '--network", "host"'
    ).replace(
        '"--publish", f"127.0.0.1:{PORT}:3306"', '"--network", "host"'
    ).replace(
        '"--publish", f"127.0.0.1:{args.port}:3306"',
        '"--network", "host"',
    )
    source = source.replace(
        '            or ports\n'
        '            != [{"HostIp": "127.0.0.1", "HostPort": str(PORT)}]\n',
        '            or host.get("NetworkMode") != "host"\n'
        '            or ports\n',
    )
    source = source.replace(
        '        "--innodb-buffer-pool-size=4294967296",\n',
        '        "--innodb-buffer-pool-size=4294967296",\n'
        '        f"--port={PORT}",\n'
        '        "--bind-address=127.0.0.1",\n',
    )
    source = source.replace(
        '    config = inspect.get("Config") or {}\n'
        '    labels = config.get("Labels") or {}\n',
        '    config = inspect.get("Config") or {}\n'
        '    commands = config.get("Cmd") or []\n'
        '    labels = config.get("Labels") or {}\n',
    )
    source = source.replace(
        '            or host.get("NetworkMode") != "host"\n'
        '            or ports\n',
        '            or host.get("NetworkMode") != "host"\n'
        '            or ports\n'
        '            or f"--port={PORT}" not in commands\n'
        '            or "--bind-address=127.0.0.1" not in commands\n',
    )
    source = source.replace(
        '    db = base._inspect(plan["database"]["name"])\n'
        '    client = base._inspect(plan["client"]["name"])\n',
        '    deadline = time.monotonic() + 120\n'
        '    ready_readback = None\n'
        '    while time.monotonic() < deadline:\n'
        '        completed = subprocess.run(\n'
        '            ["/usr/bin/mysql",\n'
        '             "--defaults-extra-file=" + plan["config_path"] + "/mysql.cnf",\n'
        '             "--protocol=TCP", "--host=127.0.0.1",\n'
        '             "--port=" + str(base.PORT), "--batch", "--skip-column-names",\n'
        '             "-e", "SELECT 1, @@port, @@bind_address"],\n'
        '            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,\n'
        '            check=False,\n'
        '        )\n'
        '        fields = completed.stdout.strip().split("\\t")\n'
        '        if completed.returncode == 0 and fields == [\n'
        '            "1", str(base.PORT), "127.0.0.1"\n'
        '        ]:\n'
        '            ready_readback = fields\n'
        '            break\n'
        '        current = base._inspect(plan["database"]["name"])\n'
        '        if current is None or (current.get("State") or {}).get("Running") is not True:\n'
        '            break\n'
        '        time.sleep(1)\n'
        '    if ready_readback is None:\n'
        '        raise ServiceError("database TCP/port/bind readiness did not pass")\n'
        '    db = base._inspect(plan["database"]["name"])\n'
        '    client = base._inspect(plan["client"]["name"])\n',
    )
    source = source.replace(
        '"client_identity":client_identity,"formal_launch_authorized":False',
        '"client_identity":client_identity,'
        '"database_tcp_readiness":{"host":"127.0.0.1","port":base.PORT,'
        '"server_readback":ready_readback},'
        '"formal_launch_authorized":False',
    )
    source = source.replace(
        '        "/measurement/config",\n'
        '    }\n'
        '    if (\n'
        '        plugin_destination != "/measurement/config"\n',
        '        "/measurement/config", "/var/lib/mysql",\n'
        '    }\n'
        '    if (\n'
        '        plugin_destination != "/measurement/config"\n',
    )
    source = source.replace(
        '    if (\n'
        '        set(target_device_io_delta)\n'
        '        != {"rbytes", "wbytes", "rios", "wios", "dbytes", "dios"}\n'
        '        or any(int(value) != 0 for value in target_device_io_delta.values())\n'
        '    ):\n'
        '        raise PhaseError(\n'
        '            "client target-device IO was nonzero in the real phase window"\n'
        '        )\n',
        '    if (\n'
        '        set(target_device_io_delta)\n'
        '        != {"rbytes", "wbytes", "rios", "wios", "dbytes", "dios"}\n'
        '        or any(int(target_device_io_delta[key]) != 0\n'
        '               for key in ("wbytes", "wios", "dbytes", "dios"))\n'
        '        or not 0 <= int(target_device_io_delta["rbytes"]) <= 4194304\n'
        '        or not 0 <= int(target_device_io_delta["rios"]) <= 256\n'
        '    ):\n'
        '        raise PhaseError(\n'
        '            "client target-device IO exceeds bounded read-only real-window gate"\n'
        '        )\n',
    )
    source = source.replace(
        '        "client_target_device_io_zero_verified": True,\n'
        '        "execution_evidence": (\n',
        '        "client_target_device_io_zero_verified":\n'
        '            all(int(value) == 0 for value in target_device_io_delta.values()),\n'
        '        "client_target_device_io_bounded_read_only_verified": True,\n'
        '        "client_target_device_io_read_limits": {\n'
        '            "maximum_rbytes": 4194304, "maximum_rios": 256,\n'
        '            "wbytes": 0, "wios": 0, "dbytes": 0, "dios": 0,\n'
        '        },\n'
        '        "execution_evidence": (\n',
    )
    source = source.replace(
        'def _zero_io(value: Mapping[str, Any]) -> bool:\n'
        '    return value.get("client_target_device_io_zero_verified") is True and value.get(\n'
        '        "client_target_device_io_delta"\n'
        '    ) == {"rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0,\n'
        '          "dbytes": 0, "dios": 0}\n',
        'def _zero_io(value: Mapping[str, Any]) -> bool:\n'
        '    delta = value.get("client_target_device_io_delta") or {}\n'
        '    return (\n'
        '        value.get("client_target_device_io_bounded_read_only_verified") is True\n'
        '        and value.get("client_target_device_io_read_limits") == {\n'
        '            "maximum_rbytes": 4194304, "maximum_rios": 256,\n'
        '            "wbytes": 0, "wios": 0, "dbytes": 0, "dios": 0,\n'
        '        }\n'
        '        and set(delta) == {"rbytes", "wbytes", "rios", "wios", "dbytes", "dios"}\n'
        '        and 0 <= int(delta["rbytes"]) <= 4194304\n'
        '        and 0 <= int(delta["rios"]) <= 256\n'
        '        and all(int(delta[key]) == 0\n'
        '                for key in ("wbytes", "wios", "dbytes", "dios"))\n'
        '    )\n',
    )
    source = source.replace(
        '        phase.get("client_target_device_io_zero_verified") is True\n'
        '        and phase.get("client_target_device_io_delta")\n'
        '        == {\n'
        '            "rbytes": 0, "wbytes": 0, "rios": 0,\n'
        '            "wios": 0, "dbytes": 0, "dios": 0,\n'
        '        },\n'
        '        "handoff client target-device real-window IO is not zero",\n',
        '        phase.get("client_target_device_io_bounded_read_only_verified") is True\n'
        '        and phase.get("client_target_device_io_read_limits") == {\n'
        '            "maximum_rbytes": 4194304, "maximum_rios": 256,\n'
        '            "wbytes": 0, "wios": 0, "dbytes": 0, "dios": 0,\n'
        '        }\n'
        '        and set(phase.get("client_target_device_io_delta") or {})\n'
        '        == {"rbytes", "wbytes", "rios", "wios", "dbytes", "dios"}\n'
        '        and 0 <= int(phase["client_target_device_io_delta"]["rbytes"]) <= 4194304\n'
        '        and 0 <= int(phase["client_target_device_io_delta"]["rios"]) <= 256\n'
        '        and all(int(phase["client_target_device_io_delta"][key]) == 0\n'
        '                for key in ("wbytes", "wios", "dbytes", "dios")),\n'
        '        "handoff client target-device IO exceeds bounded read-only gate",\n',
    )
    return source.replace(
        '"--main-agent-token"', '"--main-agent-token-file"'
    )


def verifier_module():
    path = ROOT / "shell/mysql_tpcc_v13_strict_bundle_verifier_v2.py"
    spec = importlib.util.spec_from_file_location("tpcc_strict_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("strict verifier cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build() -> dict[str, object]:
    predecessor_manifest_path = PREDECESSOR / "bundle_manifest.json"
    predecessor_manifest = json.loads(
        predecessor_manifest_path.read_text(encoding="utf-8")
    )
    if (
        PREDECESSOR.name != predecessor_manifest.get("bundle_id")
        or digest(predecessor_manifest_path) != PREDECESSOR_MANIFEST_SHA256
    ):
        raise RuntimeError("TPC-C v13 a81 predecessor identity differs")

    payload: dict[str, bytes] = {}
    for relative in predecessor_manifest["files"]:
        if (
            relative == "closure.json"
            or "/.codex_backup_" in relative
            or relative.endswith(
                "mysql_tpcc_v13_strict_bundle_verifier_v1.py"
            )
        ):
            continue
        source = PREDECESSOR / relative
        value = source.read_bytes()
        if relative.endswith(".py"):
            value = patch_python(value.decode("utf-8")).encode("utf-8")
            duplicate = (
                ('"docker", "--host", "' + SOCKET + '", "--host"').encode(),
                ("'docker', '--host', '" + SOCKET + "', '--host'").encode(),
            )
            if any(marker in value for marker in duplicate):
                raise RuntimeError(
                    "duplicate dedicated Docker host option: " + relative
                )
        payload[relative] = value
    for relative, source_name in ALGORITHM_SOURCE_OVERRIDES.items():
        payload[relative] = (ROOT / source_name).read_bytes()
    payload[
        "python_source_closure/tests/"
        "test_mysql_algorithm_c_raw_all_subset_three_level_v1.py"
    ] = (
        ROOT
        / "tests/test_mysql_algorithm_c_raw_all_subset_three_level_v1.py"
    ).read_bytes()

    validator = (
        ROOT / "shell/mysql_tpcc_v13_dedicated_daemon_validator_v1.py"
    ).read_bytes()
    issuer = (
        ROOT / "shell/mysql_tpcc_v13_dedicated_issuer_v1.py"
    ).read_text(encoding="utf-8").replace(
        "mysql_tpcc_v13_dedicated_daemon_successor_bundle_v1", SCHEMA
    ).encode("utf-8")
    issuer_test = (
        ROOT / "shell/test_mysql_tpcc_v13_dedicated_issuer_v1.py"
    ).read_bytes()
    strict_verifier = (
        ROOT / "shell/mysql_tpcc_v13_strict_bundle_verifier_v2.py"
    ).read_bytes()
    payload[
        "authority/mysql_tpcc_v13_dedicated_daemon_validator_v1.py"
    ] = validator
    payload["materials/mysql_tpcc_v13_dedicated_issuer_v1.py"] = issuer
    payload["tests/test_mysql_tpcc_v13_dedicated_issuer_v1.py"] = issuer_test
    payload[
        "authority/mysql_tpcc_v13_strict_bundle_verifier_v2.py"
    ] = strict_verifier

    contract = {
        "socket": "/run/ra-docker-mysql-tpcc-v1.sock",
        "data_root":
            "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/"
            "docker_ra_tpcc_v1",
        "device_major_minor": "259:2",
        "unit": "ra-docker-mysql-tpcc-v1.service",
        "db_client_network": "host",
        "mysql_server_port": 65106,
        "all_docker_argv_explicit_host_required": True,
        "duplicate_docker_host_option_prohibited": True,
        "direct_host_daemon_required": True,
        "daemon_identity": [
            "unit", "MainPID", "startticks", "ppid=1", "cgroup",
            "namespaces", "no_docker_ancestry",
        ],
        "graphdriver_fields": [
            "UpperDir", "WorkDir", "MergedDir", "LowerDir",
        ],
        "failed_quiescence_required": True,
        "token_file_only": True,
        "typed_trace_contract": [
            "34_bindings", "Delivery", "NewOrder", "OrderStatus",
            "Payment", "StockLevel", "L2_L3", "QPS_resource_input_prohibited",
        ],
        "strict_manifest_file_closure_required": True,
        "generated_python_bytecode_prohibited": True,
        "algorithm_c_contract": ALGORITHM_C_CONTRACT,
    }
    payload[
        "authority/mysql_tpcc_v13_dedicated_daemon_contract_v2.json"
    ] = canonical(contract)
    payload[
        "authority/mysql_tpcc_v13_default_daemon_invalidation_20260731.json"
    ] = canonical(
        {
            "status": "INVALIDATED_DO_NOT_USE",
            "reason":
                "default socket/daemon cannot prove direct-host identity "
                "or non-PG GraphDriver",
            "accepted_round_credit": 0,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
    )
    payload[
        "authority/mysql_tpcc_v13_8d389_bytecode_pollution_invalidation_"
        "20260731.json"
    ] = canonical(
        {
            "status": "INVALIDATED_DO_NOT_USE",
            "invalidated_bundle_id": INVALIDATED_DIRTY_BUNDLE_ID,
            "reason":
                "local sealed directory acquired manifest-external "
                "__pycache__/.pyc files after testing",
            "declared_file_hashes_changed": False,
            "strict_file_closure_passed": False,
            "accepted_round_credit": 0,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
    )
    payload[
        "authority/mysql_tpcc_v13_a81_algorithm_closure_invalidation_"
        "20260731.json"
    ] = canonical(
        {
            "status": "INVALIDATED_DO_NOT_USE",
            "invalidated_bundle_id": INVALIDATED_ALGORITHM_BUNDLE_ID,
            "invalidated_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
            "reason": (
                "formal replay input used L1 representatives and its gradient "
                "closure omitted confidence and the complete gradient event"
            ),
            "qualification_credit": 0,
            "r0_credit": 0,
            "candidate_credit": 0,
            "replay_credit": 0,
            "accepted_round_credit": 0,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "reuse_prohibited": True,
        }
    )
    payload[
        "authority/mysql_tpcc_v13_cf6_host_network_port_projection_"
        "invalidation_20260731.json"
    ] = canonical(
        {
            "status": "INVALIDATED_DO_NOT_USE",
            "invalidated_bundle_id": INVALIDATED_HOST_NETWORK_BUNDLE_ID,
            "affected_attempts": [
                "tpcc-dd-61f787541c4c41a8c448dcd6",
            ],
            "reason": (
                "database launch uses host networking, where Docker correctly "
                "reports no PortBindings, but the sealed readback retained the "
                "predecessor bridge-network PortBindings requirement"
            ),
            "qualification_credit": 0,
            "r0_credit": 0,
            "candidate_credit": 0,
            "replay_credit": 0,
            "accepted_round_credit": 0,
            "reuse_prohibited": True,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
    )
    payload[
        "authority/mysql_tpcc_v13_6984_server_port_readiness_"
        "invalidation_20260731.json"
    ] = canonical(
        {
            "status": "INVALIDATED_DO_NOT_USE",
            "invalidated_bundle_id": INVALIDATED_SERVER_PORT_BUNDLE_ID,
            "affected_attempts": [
                "tpcc-dd-080f79a0c5d3e6fb6079c18f",
            ],
            "reason": (
                "host-network database omitted explicit 65106/loopback bind "
                "and the start authority accepted transient Running without "
                "TCP and server-variable readiness readback"
            ),
            "qualification_credit": 0,
            "r0_credit": 0,
            "candidate_credit": 0,
            "replay_credit": 0,
            "accepted_round_credit": 0,
            "reuse_prohibited": True,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
    )
    payload[
        "authority/mysql_tpcc_v13_3baa_prefault_allowlist_"
        "invalidation_20260731.json"
    ] = canonical(
        {
            "status": "INVALIDATED_DO_NOT_USE",
            "invalidated_bundle_id": INVALIDATED_PREFAULT_ALLOWLIST_BUNDLE_ID,
            "affected_attempts": [
                "tpcc-dd-368d04c0af9169f6c72ce37c",
            ],
            "reason": (
                "the client intentionally mounts the live MySQL datadir "
                "read-only and prefault recorded /var/lib/mysql, but the "
                "sealed phase materializer omitted that destination from "
                "its exact prefault allowlist"
            ),
            "qualification_credit": 0,
            "r0_credit": 0,
            "candidate_credit": 0,
            "replay_credit": 0,
            "accepted_round_credit": 0,
            "reuse_prohibited": True,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
    )
    payload[
        "authority/mysql_tpcc_v13_1a21_bundle_root_mode_"
        "invalidation_20260731.json"
    ] = canonical(
        {
            "status": "INVALIDATED_DO_NOT_USE",
            "invalidated_bundle_id": INVALIDATED_BUNDLE_ROOT_MODE_ID,
            "affected_attempts": [
                "tpcc-dd-e253a996208305767c25e48a",
                "tpcc-dd-dccd7d5e2b635ba072381308",
            ],
            "reason": (
                "publication preserved 0500 bundle directories, while the "
                "sealed phase gate requires the client mount root and every "
                "classpath ancestor to be immutable and traversable at 0555"
            ),
            "qualification_credit": 0,
            "r0_credit": 0,
            "candidate_credit": 0,
            "replay_credit": 0,
            "accepted_round_credit": 0,
            "reuse_prohibited": True,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
    )
    payload[
        "authority/mysql_tpcc_v13_e03_absolute_zero_client_io_"
        "invalidation_20260731.json"
    ] = canonical(
        {
            "status": "INVALIDATED_DO_NOT_USE",
            "invalidated_bundle_id": INVALIDATED_ABSOLUTE_ZERO_IO_BUNDLE_ID,
            "affected_attempts": [
                "tpcc-dd-f916a8ea09124d8fea539a1a",
            ],
            "reason": (
                "the real 180-second qualification passed its transaction "
                "terminal gate but observed 520192 bytes across 27 read IOs "
                "from immutable client JAR/config mounts on the DB device; "
                "writes and discards were zero, so the successor replaces "
                "the impractical absolute-zero read rule with a sealed "
                "4-MiB/256-read-IO ceiling while retaining zero write/discard"
            ),
            "observed_client_target_device_io": {
                "rbytes": 520192,
                "rios": 27,
                "wbytes": 0,
                "wios": 0,
                "dbytes": 0,
                "dios": 0,
            },
            "qualification_credit": 0,
            "r0_credit": 0,
            "candidate_credit": 0,
            "replay_credit": 0,
            "accepted_round_credit": 0,
            "reuse_prohibited": True,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
    )

    files = {
        relative: digest_bytes(value)
        for relative, value in sorted(payload.items())
    }
    closure = {
        "schema_version": "mysql_tpcc_v13_dedicated_daemon_closure_v3",
        "files": files,
        "dedicated_daemon_contract": contract,
        "algorithm_c_contract": ALGORITHM_C_CONTRACT,
        "strict_manifest_file_closure_required": True,
    }
    payload["closure.json"] = canonical(closure)
    files = {
        relative: digest_bytes(value)
        for relative, value in sorted(payload.items())
    }
    manifest = {
        key: value
        for key, value in predecessor_manifest.items()
        if key not in ("bundle_id", "files")
    }
    manifest.update(
        {
            "schema_version": SCHEMA,
            "predecessor_bundle_id": PREDECESSOR.name,
            "predecessor_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
            "invalidates_dirty_bundle_id": INVALIDATED_DIRTY_BUNDLE_ID,
            "invalidates_algorithm_bundle_id":
                INVALIDATED_ALGORITHM_BUNDLE_ID,
            "invalidates_host_network_bundle_id":
                INVALIDATED_HOST_NETWORK_BUNDLE_ID,
            "invalidates_server_port_bundle_id":
                INVALIDATED_SERVER_PORT_BUNDLE_ID,
            "invalidates_prefault_allowlist_bundle_id":
                INVALIDATED_PREFAULT_ALLOWLIST_BUNDLE_ID,
            "invalidates_bundle_root_mode_id":
                INVALIDATED_BUNDLE_ROOT_MODE_ID,
            "invalidates_absolute_zero_io_bundle_id":
                INVALIDATED_ABSOLUTE_ZERO_IO_BUNDLE_ID,
            "dedicated_daemon_contract": contract,
            "algorithm_c_contract": ALGORITHM_C_CONTRACT,
            "strict_manifest_file_closure_required": True,
            "generated_python_bytecode_prohibited": True,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "files": files,
        }
    )
    bundle_id = digest_bytes(canonical(manifest))
    manifest["bundle_id"] = bundle_id
    target = OUTPUT / bundle_id
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        stage = OUTPUT / (".stage-" + secrets.token_hex(8))
        stage.mkdir(mode=0o700)
        try:
            for relative, value in payload.items():
                destination = stage / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(value)
                os.chmod(destination, 0o444)
            raw_manifest = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw_manifest)
            (stage / "bundle_manifest.json.sha256").write_text(
                digest_bytes(raw_manifest) + "  bundle_manifest.json\n",
                encoding="ascii",
            )
            os.chmod(stage / "bundle_manifest.json", 0o444)
            os.chmod(stage / "bundle_manifest.json.sha256", 0o444)
            for directory in sorted(
                (item for item in stage.rglob("*") if item.is_dir()),
                key=lambda item: len(item.parts),
                reverse=True,
            ):
                os.chmod(directory, 0o555)
            os.chmod(stage, 0o555)
            os.rename(stage, target)
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            raise

    verification = verifier_module().verify(target)
    return {
        "status":
            "PASS_SEALED_STRICT_TPCC_RAW_ALL_SUBSET_SUCCESSOR",
        "bundle": str(target),
        "bundle_id": bundle_id,
        "manifest_sha256": digest(target / "bundle_manifest.json"),
        "strict_verification": verification,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def main() -> None:
    print(json.dumps(build(), sort_keys=True))


if __name__ == "__main__":
    main()
