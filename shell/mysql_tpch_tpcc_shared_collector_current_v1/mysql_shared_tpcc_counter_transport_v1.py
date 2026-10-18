#!/usr/bin/env python3
"""Install the sealed TPC-C client-container database-counter transport."""

from pathlib import Path


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


def install(shared):
    def prepare_database_counter_transport(
        runtime,
        bundle,
        database_container,
        database_row,
        defaults_file,
        mysql_client_container,
        mysql_binary_in_client,
    ):
        contract = runtime.get("database_counter_transport")
        if (
            contract != TRANSPORT_CONTRACT
            or not mysql_client_container
            or mysql_binary_in_client != "/usr/bin/mysql"
            or not str(database_container).endswith("_db_" + runtime.get("live_attempt_id", ""))
            and not str(database_container).startswith("ra_mysql_tpcc_v2_db_")
        ):
            raise shared.QualificationError(
                "TPC-C database-counter transport contract differs"
            )
        client = shared.inspect_container(mysql_client_container)
        client_id = shared._full_container_id(client, "database-counter client")
        client_name = shared._container_name(client, "database-counter client")
        if client_name != mysql_client_container:
            raise shared.QualificationError("TPC-C counter client name differs")
        mounts = client.get("Mounts") or []
        database_sources = {
            row.get("Source")
            for row in database_row.get("Mounts") or []
            if row.get("Destination") == "/var/lib/mysql"
        }
        if (
            any(
                "/var/run/docker.sock"
                in (str(row.get("Source")) + " " + str(row.get("Destination")))
                for row in mounts
            )
            or any(row.get("Source") in database_sources for row in mounts)
        ):
            raise shared.QualificationError("TPC-C counter client mount isolation differs")
        required_mounts = [
            {"source": str(bundle), "destination": CLIENT_PATHS["bundle"]},
            {
                "source": runtime["client_runtime_mounts"]["python"]["source"],
                "destination": CLIENT_PATHS["python"],
            },
            {
                "source": str(defaults_file.parent),
                "destination": CLIENT_PATHS["config"],
            },
        ]
        basename = defaults_file.name
        binding = {
            "container_id": client_id,
            "container_name": client_name,
            "image_id": client.get("Image"),
            "created": client.get("Created"),
            "inspect_config_sha256": shared._client_container_config_identity(client),
            "required_read_only_mounts": required_mounts,
        }
        checked = shared.validate_client_container_readback(
            client,
            runtime["client_resource_envelope"],
            binding,
            "/measurement",
        )
        version = shared.run(
            [
                *shared.DOCKER_CLI,
                "exec",
                client_id,
                "/usr/bin/mysql",
                "--version",
            ],
            timeout=5,
            label="TPC-C client MySQL CLI readback",
        ).strip()
        if not version or "mysql" not in version.lower():
            raise shared.QualificationError("TPC-C client MySQL CLI is absent")
        return {
            "schema_version": "mysql_algorithm_c_database_counter_transport_readback_v2",
            "status": "PASS_EXACT_CLIENT_DOCKER_EXEC_SQL_STDOUT_BRIDGE",
            "kind": contract["kind"],
            "client_container": mysql_client_container,
            "client_container_name": client_name,
            "client_container_id": checked["container_id"],
            "client_container_short_id": checked["container_short_id"],
            "client_pid": checked["pid"],
            "client_process_start_ticks": checked["process_start_ticks"],
            "client_inspect_config_sha256": checked["inspect_config_sha256"],
            "mysql_binary_in_client": "/usr/bin/mysql",
            "mysql_defaults_file_in_client": CLIENT_PATHS["config"] + "/" + basename,
            "mysql_version_stdout": version,
            "stdout_only": True,
            "host_mysql_cli_required": False,
            "host_mysql_cli_prohibited": True,
            "docker_socket_mounted": False,
            "target_datadir_mounted": False,
            "target_device_write_authorized": False,
            "accepted_round_authorized": False,
        }

    def db_counter_sample(
        mysql_binary,
        defaults_file,
        port,
        database_counter_transport=None,
    ):
        if database_counter_transport is None:
            raise shared.QualificationError("TPC-C host MySQL counter fallback is prohibited")
        transport = database_counter_transport
        current = shared.inspect_container(transport["client_container"])
        current_id = shared._full_container_id(current, "database-counter client")
        current_name = shared._container_name(current, "database-counter client")
        current_pid = int((current.get("State") or {}).get("Pid") or 0)
        if (
            current_id != transport["client_container_id"]
            or current_name != transport["client_container_name"]
            or current_pid != int(transport["client_pid"])
            or shared.process_start_ticks(current_pid)
            != int(transport["client_process_start_ticks"])
            or shared._client_container_config_identity(current)
            != transport["client_inspect_config_sha256"]
        ):
            raise shared.QualificationError("TPC-C counter client identity changed")
        output = shared.run(
            [
                *shared.DOCKER_CLI,
                "exec",
                transport["client_container_id"],
                transport["mysql_binary_in_client"],
                "--defaults-extra-file=%s"
                % transport["mysql_defaults_file_in_client"],
                "--protocol=TCP",
                "--host=127.0.0.1",
                "--port=%d" % int(port),
                "--batch",
                "--raw",
                "--skip-column-names",
                "--execute",
                "SHOW GLOBAL STATUS",
            ],
            timeout=1,
            label="TPC-C client SQL database counter sample",
        )
        required_lower = {name.lower() for name in shared.DB_COUNTERS}
        observed = {}
        for line in output.splitlines():
            pieces = line.split("\t", 1)
            if len(pieces) != 2 or pieces[0].strip().lower() not in required_lower:
                continue
            key = pieces[0].strip().lower()
            if key in observed:
                raise shared.QualificationError("duplicate database counter: %s" % pieces[0])
            try:
                observed[key] = int(pieces[1].strip())
            except ValueError as exc:
                raise shared.QualificationError(
                    "database counter is not an integer: %s" % pieces[0]
                ) from exc
        counters = {
            name: observed[name.lower()]
            for name in shared.DB_COUNTERS
            if name.lower() in observed
        }
        if set(counters) != set(shared.DB_COUNTERS):
            missing = sorted(set(shared.DB_COUNTERS) - set(counters))
            raise shared.QualificationError(
                "database counter coverage incomplete: %s" % ",".join(missing)
            )
        return counters

    shared.prepare_database_counter_transport = prepare_database_counter_transport
    shared.db_counter_sample = db_counter_sample
    return shared
