from scripts import wait_launch_mysql_algorithm_c_boundary_resume_v7 as successor


def test_known_uncoordinated_mixstrg_controller_is_blocked():
    assert successor.is_uncoordinated_controller(
        "bash /home/anonymous/working/benchbase/run_mixstrg_0809.sh row"
    )
    assert not successor.is_uncoordinated_controller(
        "python scripts/run_mysql_four_scene_algorithm_c.py"
    )
