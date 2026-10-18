from shell import launch_mysql_sgd_four_lane_controllers as launcher
from shell import manual_mysql_sgd_lane_controller as controller
from shell import report_mysql_sgd_progress_every_5min as reporter


EXPECTED = ("job_memory", "hybench_xp_io", "tpch_memory", "tpcc_cpu")


def test_launcher_and_reporter_cover_only_four_algorithm_c_lanes() -> None:
    assert launcher.SCENARIOS == EXPECTED
    assert launcher.EXPECTED_CONTRACT_VERSION == controller.CONTROLLER_CONTRACT_VERSION
    assert tuple(row[0] for row in reporter.SCENARIOS) == EXPECTED
    assert reporter.SCENARIOS[2][2] == "node01 / /mnt/nvme1n1"
    assert reporter.SCENARIOS[3][2] == "node01 / /mnt/mysql_nvme0n1"


def test_all_controllers_use_standard_algorithm_c_entry() -> None:
    for scenario in EXPECTED:
        registration = controller.AUTO_LAUNCH_REGISTRY[scenario]
        assert registration["config"].name == f"{scenario}.algorithm_c.optimizer.json"
        assert registration["service_factory"] == (
            "mysql_algorithm_c_production_services:build"
        )
