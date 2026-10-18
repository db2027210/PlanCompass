from pathlib import Path

from scripts import prepare_mysql_xp_eight_worker_cpu_a_slots_scene_authority_v1 as authority


def test_authority_uses_current_resource_freeze_and_unlocked_reset() -> None:
    source = Path(authority.__file__).read_text(encoding="utf-8")
    assert "frozen_mysql_xp_eight_worker_cpu_a_slots_20260828_v6" in source
    assert "reset_mysql_xp_data_planes_mixed_eight_worker_v5" in source
    assert "prepare_mysql_xp_eight_worker_scene_authority_v1" in source
    assert "source_id not in" in source
    assert "xp_io_w3" in source and "xp_io_w4" in source
    assert "unused_slot_live_identity_synthesized" in source
    assert "current_live_identity_read_back" in source
    assert "historical_container_id_superseded" in source
    assert 'labels.get(\\"ra.owner\\") != \\"mysql_four_scene_v5\\"' in source
    assert "current_blkio_authority_read_back" in source
    assert "old_slot_audit_not_target_envelope_validation" in source
    assert "_enforce_blkio" in source
