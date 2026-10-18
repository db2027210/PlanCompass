from pathlib import Path

from scripts import guard_stop_external_mixstrg_v1 as guard


def test_guard_scope_is_exact_and_non_deleting():
    assert guard.SCRIPT == "/home/anonymous/working/benchbase/run_mixstrg_0809.sh"
    assert guard.CONTAINERS == (
        "bench-run-mixstrg-opt", "bench-pg-mixstrg-0817",
    )
    source = Path(guard.__file__).read_text(encoding="utf-8")
    assert '["docker", "stop", "--time", "30", container]' in source
    assert '"docker", "rm"' not in source
    assert '"docker", "kill"' not in source


def test_current_process_is_never_returned_as_script_target():
    assert all(row["pid"] != guard.os.getpid() for row in guard.matching_script_processes())
