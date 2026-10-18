from scripts import preflight_mysql_four_scene_live_v8 as gate


def test_only_topology_proven_megasas_status_worker_is_exempt() -> None:
    proven = {"proven": True}
    assert gate.classify_d_state_name(
        "kworker/u1024:2-poll_megasas0_status", proven,
    ) == "exempt_unrelated_disk3_megasas_status_poll"
    for name in (
        "mysqld",
        "postgres",
        "kworker/u1024:2-events",
        "kworker/u1024:2-poll_nvme2_status",
        "poll_megasas0_status",
    ):
        assert gate.classify_d_state_name(
            name, proven,
        ) == "blocking_unknown_or_target_relevant"


def test_megasas_worker_fails_closed_without_live_topology_proof() -> None:
    assert gate.classify_d_state_name(
        "kworker/u1024:2-poll_megasas0_status", {"proven": False},
    ) == "blocking_unknown_or_target_relevant"


def test_target_journal_worker_is_recorded_but_not_external_workload_proof() -> None:
    assert gate.classify_d_state_name(
        "jbd2/nvme2n1-8", {"proven": False},
    ) == "observed_target_journal_thread_nonblocking"
    for name in (
        "jbd2/nvme1n1-8",
        "jbd2/sda1-8",
        "postgres",
        "mysqld",
    ):
        assert gate.classify_d_state_name(
            name, {"proven": True},
        ) == "blocking_unknown_or_target_relevant"
