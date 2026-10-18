from scripts import reset_mysql_xp_data_planes_v6 as reset


def test_exact_stopped_discard_is_authorized_only_after_zero_session_proof():
    stopped = {
        "Status": "exited", "Running": False, "Pid": 0, "ExitCode": 137,
    }
    assert reset.stopped_discard_predecessor_authorized(
        stopped, allow_unresponsive_old_container=True,
    )
    assert not reset.stopped_discard_predecessor_authorized(
        stopped, allow_unresponsive_old_container=False,
    )


def test_live_or_unproven_discard_is_never_authorized():
    assert not reset.stopped_discard_predecessor_authorized(
        {"Status": "running", "Running": True, "Pid": 123, "ExitCode": 0},
        allow_unresponsive_old_container=True,
    )
    assert not reset.stopped_discard_predecessor_authorized(
        {"Status": "exited", "Running": False, "Pid": 0},
        allow_unresponsive_old_container=True,
    )


def test_host_pid_kill_wait_covers_slow_docker_reap():
    assert reset.HOST_PID_KILL_EXIT_WAIT_SECONDS >= 180
