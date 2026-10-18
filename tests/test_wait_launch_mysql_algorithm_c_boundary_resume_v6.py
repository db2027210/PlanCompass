from scripts import wait_launch_mysql_algorithm_c_boundary_resume_v1 as base
from scripts import wait_launch_mysql_algorithm_c_boundary_resume_v6 as successor


def test_successor_keeps_global_load1_diagnostic_only():
    pressure = {"load1": 96.0}
    workloads = [{"pid": 123}]
    isolation = {"passed": True}

    assert base.load1_blockers(pressure, workloads, isolation) == [
        "host_load1_exceeds_limit"
    ]
    assert successor.direct_isolation_load1_blockers(
        pressure, workloads, isolation
    ) == []


def test_successor_does_not_weaken_direct_isolation_gate():
    # The load hook owns only global load1.  The base waiter's independent
    # workload-isolation blocker remains authoritative for this input.
    assert successor.direct_isolation_load1_blockers(
        {"load1": 1.0}, [{"pid": 123}], {"passed": False}
    ) == []
