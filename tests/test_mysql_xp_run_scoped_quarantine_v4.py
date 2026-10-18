from pathlib import Path

import pytest

from scripts import reset_mysql_xp_data_planes_client_primary_v4 as reset


def test_run_and_token_are_both_preserved() -> None:
    source = Path(
        "/disk2/runtime/quarantine/"
        "v1249_to_c_complete_lifecycle_v110_20260906_xp_io/"
        "outer_007_batch_2"
    )
    assert reset._run_scoped_root(source) == Path(
        "/disk2/runtime/quarantine/"
        "v1249_to_c_complete_lifecycle_v110_20260906_xp_io"
        "__outer_007_batch_2"
    )


def test_unscoped_input_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="input differs"):
        reset._run_scoped_root(Path("/disk2/runtime/quarantine/outer_007_batch_2"))


def test_install_covers_low_level_reset_and_cleanup() -> None:
    try:
        reset.install()
        assert reset.implementation._prepare_fresh_xp is reset._prepare_fresh_xp
        assert (
            reset.implementation.purge_completed_discard_quarantine
            is reset.purge_completed_discard_quarantine
        )
    finally:
        reset.uninstall()
    assert reset.implementation._prepare_fresh_xp is reset._ORIGINAL_PREPARE
    assert (
        reset.implementation.purge_completed_discard_quarantine
        is reset._ORIGINAL_PURGE
    )
