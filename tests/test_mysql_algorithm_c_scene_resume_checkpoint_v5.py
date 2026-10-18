from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v5 as subject


def test_stale_task_recovery_errors_are_exact_and_scene_scoped() -> None:
    assert len(subject.STALE_TASK_ERRORS) == 4
    assert all(error.startswith("CalledProcessError: Command '['docker', 'start'")
               for error in subject.STALE_TASK_ERRORS)
    assert all("ra_mysql_disk2_algc_xp_" in error for error in subject.STALE_TASK_ERRORS)
