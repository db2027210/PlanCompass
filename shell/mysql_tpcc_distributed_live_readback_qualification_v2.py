#!/usr/bin/env python3
"""TPC-C v4 client-only SQL-counter transport wrapper.

The shared v1 qualification collector is retained byte-for-byte.  This small
sealed wrapper replaces only its database-counter transport: TPC-C counters
must be read through the already-frozen client container, never a host MySQL
CLI.  It maps the host defaults file to the read-only config mount inside that
client and validates every involved mount before issuing ``docker exec``.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Mapping


SCRIPT = Path(__file__).resolve()
SHARED = SCRIPT.with_name("mysql_distributed_live_readback_qualification_v1.py")
CLIENT_PATHS = {
    "bundle": "/opt/mysql-tpcc-bundle",
    "python": "/opt/mysql-tpcc-python",
    "config": "/opt/mysql-tpcc-config",
}
TRANSPORT_CONTRACT = {
    "schema_version": "mysql_algorithm_c_database_counter_transport_v2",
    "kind": "exact_client_container_docker_exec_stdout_v2",
    "mysql_binary_in_client": "/usr/bin/mysql",
    "stdout_only": True,
    "host_mysql_cli_required": False,
    "host_mysql_cli_prohibited": True,
    "docker_socket_mount_prohibited": True,
    "target_datadir_mount_prohibited": True,
    "client_paths": CLIENT_PATHS,
}


def _load_shared():
    if SHARED.is_symlink() or not SHARED.is_file():
        raise RuntimeError("sealed shared qualification collector is absent")
    spec = importlib.util.spec_from_file_location("sealed_tpcc_shared_qualification", SHARED)
    if spec is None or spec.loader is None:
        raise RuntimeError("sealed shared qualification collector cannot be loaded")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def _mounts_by_destination(client: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    mounts = client.get("Mounts")
    if not isinstance(mounts, list):
        raise RuntimeError("client inspect mount list is absent")
    result = {
        str(row.get("Destination")): row for row in mounts
        if isinstance(row, Mapping)
    }
    if len(result) != len(mounts):
        raise RuntimeError("client mount destinations are duplicated")
    return result


def install(shared):
    """Install strict v2 counter functions into the shared collector module."""

    def prepare_database_counter_transport(
        runtime, bundle, database_container, database_row, defaults_file,
        mysql_client_container, mysql_binary_in_client,
    ):
        contract = runtime.get("database_counter_transport")
        if contract != TRANSPORT_CONTRACT:
            raise shared.QualificationError("TPC-C v4 client SQL transport contract differs")
        if not mysql_client_container or mysql_binary_in_client != "/usr/bin/mysql":
            raise shared.QualificationError("TPC-C v4 client SQL transport arguments differ")
        if (not str(database_container).endswith("_db") or
                mysql_client_container != str(database_container)[:-3] + "_client"):
            raise shared.QualificationError("TPC-C database/client name pairing differs")
        defaults_file = Path(defaults_file).resolve(strict=True)
        if defaults_file.is_symlink() or not defaults_file.is_file():
            raise shared.QualificationError("TPC-C defaults file is absent or unsafe")
        client = shared.inspect_container(mysql_client_container)
        mounts = _mounts_by_destination(client)
        database_sources = {
            row.get("Source") for row in (database_row.get("Mounts") or [])
            if row.get("Destination") == "/var/lib/mysql"
        }
        if any("/var/run/docker.sock" in (str(row.get("Source")) + " " + str(row.get("Destination")))
               for row in mounts.values()):
            raise shared.QualificationError("TPC-C client has Docker socket mount")
        if any(row.get("Source") in database_sources for row in mounts.values()):
            raise shared.QualificationError("TPC-C client has target datadir mount")
        expected = {
            CLIENT_PATHS["bundle"]: str(Path(bundle).resolve()),
            CLIENT_PATHS["python"]: str(Path(runtime["toolchain"]["python_runtime_root"]).resolve()),
            CLIENT_PATHS["config"]: str(defaults_file.parent),
        }
        required_read_only_mounts = []
        for destination, source in expected.items():
            mount = mounts.get(destination)
            if not isinstance(mount, Mapping) or mount.get("Source") != source or mount.get("RW") is not False:
                raise shared.QualificationError("TPC-C client SQL read-only mount differs: %s" % destination)
            required_read_only_mounts.append({"source": source, "destination": destination})
        basename = defaults_file.name
        if basename in {"", ".", ".."} or "/" in basename:
            raise shared.QualificationError("TPC-C defaults filename is unsafe")
        binding = {
            "container_id": client.get("Id"), "image_id": client.get("Image"),
            "created": client.get("Created"),
            "inspect_config_sha256": shared._client_container_config_identity(client),
            "required_read_only_mounts": required_read_only_mounts,
        }
        checked = shared.validate_client_container_readback(
            client, runtime["client_resource_envelope"], binding, "/measurement"
        )
        version = shared.run(
            ["docker", "exec", mysql_client_container, "/usr/bin/mysql", "--version"],
            timeout=5, label="TPC-C client-container native MySQL CLI readback",
        ).strip()
        if not version or "mysql" not in version.lower():
            raise shared.QualificationError("TPC-C client-container MySQL CLI is absent")
        return {
            "schema_version": "mysql_algorithm_c_database_counter_transport_readback_v2",
            "status": "PASS_EXACT_CLIENT_DOCKER_EXEC_SQL_STDOUT_BRIDGE",
            "kind": contract["kind"], "client_container": mysql_client_container,
            "client_container_id": checked["container_id"], "client_pid": checked["pid"],
            "client_inspect_config_sha256": checked["inspect_config_sha256"],
            "mysql_binary_in_client": "/usr/bin/mysql",
            "defaults_file_in_client": CLIENT_PATHS["config"] + "/" + basename,
            "mysql_version_stdout": version, "stdout_only": True,
            "host_mysql_cli_required": False, "host_mysql_cli_prohibited": True,
            "docker_socket_mounted": False, "target_datadir_mounted": False,
            "target_device_write_authorized": False,
            "accepted_round_authorized": False,
        }

    def db_counter_sample(mysql_binary, defaults_file, port, database_counter_transport=None):
        if database_counter_transport is None:
            raise shared.QualificationError("TPC-C host MySQL counter fallback is prohibited")
        transport = database_counter_transport
        current = shared.inspect_container(transport["client_container"])
        if (current.get("Id") != transport["client_container_id"] or
                int((current.get("State") or {}).get("Pid") or 0) != int(transport["client_pid"]) or
                shared._client_container_config_identity(current) != transport["client_inspect_config_sha256"]):
            raise shared.QualificationError("TPC-C database-counter client identity changed")
        names = ",".join("'%s'" % name for name in shared.DB_COUNTERS)
        sql = ("SELECT VARIABLE_NAME,VARIABLE_VALUE FROM performance_schema."
               "global_status WHERE VARIABLE_NAME IN (%s)" % names)
        output = shared.run(
            ["docker", "exec", transport["client_container"], transport["mysql_binary_in_client"],
             "--defaults-extra-file=%s" % transport["defaults_file_in_client"],
             "--protocol=TCP", "--host=127.0.0.1", "--port=%d" % int(port),
             "--batch", "--skip-column-names", "--execute", sql],
            timeout=1, label="TPC-C client SQL database counter sample",
        )
        observed = {}
        for line in output.splitlines():
            pieces = line.split("\t")
            if len(pieces) == 2:
                observed[pieces[0].lower()] = int(pieces[1])
        counters = {name: observed[name.lower()] for name in shared.DB_COUNTERS if name.lower() in observed}
        if set(counters) != set(shared.DB_COUNTERS):
            missing = sorted(set(shared.DB_COUNTERS) - set(counters))
            raise shared.QualificationError("database counter coverage incomplete: %s" % ",".join(missing))
        return counters

    shared.prepare_database_counter_transport = prepare_database_counter_transport
    shared.db_counter_sample = db_counter_sample
    return shared


def main() -> int:
    return install(_load_shared()).main()


if __name__ == "__main__":
    raise SystemExit(main())
