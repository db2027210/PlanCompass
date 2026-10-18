import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "capture/mysql_tpcc_typed_interference_gate_v1.py"


def load_gate():
    sys.path.insert(0, str(ROOT / "capture"))
    spec = importlib.util.spec_from_file_location("tpcc_gate", GATE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def row(command, cpuset, category="mysql"):
    return {
        "pid": 10,
        "start_ticks": 20,
        "category": category,
        "command": command,
        "cpuset_cpus": cpuset,
    }


def test_only_explicit_other_port_and_disjoint_mysql_client_is_exempt():
    gate = load_gate()
    target = gate.parse_cpu_list(gate.EXPECTED_CPUSET)
    assert gate.proven_disjoint_mysql_client(
        row("/usr/bin/mysql --host=127.0.0.1 --port=65203", "200-207,456-463"),
        target,
    )
    assert not gate.proven_disjoint_mysql_client(
        row("/usr/bin/mysql --host=127.0.0.1 --port=65206", "200-207,456-463"),
        target,
    )
    assert not gate.proven_disjoint_mysql_client(
        row("/usr/bin/mysql --host=127.0.0.1 --port=65203", "208-215,464-471"),
        target,
    )
    assert not gate.proven_disjoint_mysql_client(
        row("mysqld --port=65203", "200-207,456-463"),
        target,
    )
    assert not gate.proven_disjoint_mysql_client(
        row("/usr/bin/mysql --host=127.0.0.1", "200-207,456-463"),
        target,
    )
