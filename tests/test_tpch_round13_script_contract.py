from pathlib import Path


WORKLOAD = Path("/tmp/tpch_round13_gradient_debug_workload.sh")
CONTROLLER = Path("/tmp/run_tpch_round13_device_provenance_controller.sh")


def _preflight_block(text: str) -> str:
    start = text.index('if [[ "$preflight_only" == "1" ]]')
    end = text.index("\nfi", start)
    return text[start:end]


def test_preflight_is_read_only_and_precedes_mutation() -> None:
    text = WORKLOAD.read_text(encoding="utf-8")
    block = _preflight_block(text)
    assert text.index('if [[ "$preflight_only" == "1" ]]') < text.index(
        "bounded docker update"
    )
    for forbidden in ("docker update", "docker restart", "docker start", "CHECKPOINT"):
        assert forbidden not in block


def test_controller_has_bounded_preflight_and_total_optimizer_deadline() -> None:
    text = CONTROLLER.read_text(encoding="utf-8")
    assert (
        'bounded120 "$round_root/tpch_gradient_debug_workload.sh" '
        "--preflight-only"
    ) in text
    assert "controller_deadline=$((SECONDS + 21600))" in text
    assert "controller_exit_status.json" in text


def test_restore_evidence_is_atomic() -> None:
    text = CONTROLLER.read_text(encoding="utf-8")
    assert 'temporary = target + ".tmp"' in text
    assert "os.replace(temporary, target)" in text
    assert "os.fsync(handle.fileno())" in text
