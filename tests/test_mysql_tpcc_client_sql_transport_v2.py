from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "shell/mysql_tpcc_distributed_live_readback_qualification_v2.py"


def wrapper():
    spec = importlib.util.spec_from_file_location("tpcc_sql_v2", WRAPPER)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_client_transport_maps_defaults_and_prohibits_host_fallback(tmp_path: Path) -> None:
    mod = wrapper()

    class Error(RuntimeError):
        pass

    class Shared:
        QualificationError = Error
        DB_COUNTERS = ("Threads_connected", "Queries")

    shared = Shared()
    defaults = tmp_path / "config" / "mysql.cnf"
    defaults.parent.mkdir()
    defaults.write_text("[client]\n", encoding="utf-8")
    bundle = tmp_path / "bundle"
    python_root = tmp_path / "python"
    bundle.mkdir(); python_root.mkdir()
    client = {
        "Id": "c" * 64, "Image": "sha256:" + "a" * 64,
        "Created": "now", "State": {"Pid": 4321},
        "Mounts": [
            {"Destination": "/opt/mysql-tpcc-bundle", "Source": str(bundle), "RW": False},
            {"Destination": "/opt/mysql-tpcc-python", "Source": str(python_root), "RW": False},
            {"Destination": "/opt/mysql-tpcc-config", "Source": str(defaults.parent), "RW": False},
        ],
    }
    calls = []
    shared.inspect_container = lambda _name: client
    shared._client_container_config_identity = lambda _row: "i" * 64
    shared.validate_client_container_readback = lambda *_args: {
        "container_id": "c" * 64, "pid": 4321, "inspect_config_sha256": "i" * 64,
    }
    def run(argv, **_kwargs):
        calls.append(argv)
        if argv[-1] == "--version":
            return "mysql  Ver 8.0"
        return "Threads_connected\t1\nQueries\t2\n"
    shared.run = run
    patched = mod.install(shared)
    runtime = {
        "database_counter_transport": mod.TRANSPORT_CONTRACT,
        "toolchain": {"python_runtime_root": str(python_root)},
        "client_resource_envelope": {},
    }
    transport = patched.prepare_database_counter_transport(
        runtime, bundle, "ra_mysql_tpcc_v3_db", {"Mounts": []}, defaults,
        "ra_mysql_tpcc_v3_client", "/usr/bin/mysql",
    )
    assert transport["defaults_file_in_client"] == "/opt/mysql-tpcc-config/mysql.cnf"
    assert transport["host_mysql_cli_prohibited"] is True
    assert patched.db_counter_sample(Path("/must/not/run"), defaults, 65106, transport) == {
        "Threads_connected": 1, "Queries": 2,
    }
    assert calls[-1][:4] == ["docker", "exec", "ra_mysql_tpcc_v3_client", "/usr/bin/mysql"]
    assert "--defaults-extra-file=/opt/mysql-tpcc-config/mysql.cnf" in calls[-1]
    try:
        patched.db_counter_sample(Path("/usr/bin/mysql"), defaults, 65106, None)
    except Error as exc:
        assert "host MySQL counter fallback" in str(exc)
    else:
        raise AssertionError("host fallback must fail closed")
