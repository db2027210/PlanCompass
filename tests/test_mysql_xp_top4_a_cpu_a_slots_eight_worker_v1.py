from pathlib import Path

from scripts import build_mysql_xp_top4_algorithm_a_cpu_a_slots_eight_worker_release_v1 as release
from scripts import launch_mysql_algorithm_a_xp_top4_cpu_a_slots_eight_worker_scene_v1 as launcher


def test_a_release_is_complete_top4_factorial_on_eight_workers() -> None:
    source = Path(release.__file__).read_text(encoding="utf-8")
    assert '"candidate_batches": [[1, 2, 3, 4, 5, 6, 7, 8]]' in source
    assert '"physical_replay_parallelism_per_scene": 8' in source
    assert '"candidates_per_round": 16' in source
    assert '"historical_algorithm_b_or_c_credit": False' in source


def test_a_launcher_requires_terminal_b_and_eight_lane_reset() -> None:
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    assert "range(1, 9)" in source
    assert 'state.get("physical_replay_parallelism") == 8' in source
    assert "refresh_mysql_xp8_cpu_a_slots_live_authority_from_reset_v1.py" in source
