#!/usr/bin/env python3
"""Prepare one current CPU-A-slot XP scene for eight-worker B/A execution."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SOURCE = Path(__file__).resolve().with_name(
    "prepare_mysql_xp_eight_worker_scene_authority_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "reset_mysql_xp_data_planes_mixed_eight_worker_v1 as reset8":
        "reset_mysql_xp_data_planes_mixed_eight_worker_v5 as reset8",
    "frozen_mysql_xp_eight_worker_20260827_v1":
        "frozen_mysql_xp_eight_worker_cpu_a_slots_20260828_v6",
    "xp_effective_resource_successor.json\"\nLEGACY_PREPARED":
        "xp_effective_resource_successor.json\"\nLEGACY_PREPARED",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"CPU-A XP8 authority predecessor differs: {old}")
    text = text.replace(old, new)

# The two CPU-A pool positions xp_io_w3/w4 were deliberately unused by the
# current four-worker successor.  Historical prepared bindings therefore
# contain placement rows but no live container_id.  Admit only those explicitly
# frozen unused positions by reading their exact running container and mounts;
# every completed-memory donor continues to require its historical binding.
old_prior = """        prior = copy.deepcopy(legacy_rows.get(source_id) or {})
        if str(prior.get(\"container\") or \"\") != str(frozen.get(\"container\") or \"\"):
            raise RuntimeError(f\"{worker_id} completed-slot predecessor differs\")
"""
new_prior = """        prior = copy.deepcopy(legacy_rows.get(source_id) or {})
        unused_slot = frozen.get(\"slot_source_kind\") == \"mixed\"
        if not prior:
            if not unused_slot or source_id not in {\"xp_io_w3\", \"xp_io_w4\"}:
                raise RuntimeError(f\"{worker_id} completed-slot predecessor differs\")
            prior = copy.deepcopy(frozen)
        if str(prior.get(\"container\") or \"\") != str(frozen.get(\"container\") or \"\"):
            raise RuntimeError(f\"{worker_id} completed-slot predecessor differs\")
        impl = reset8.base._impl
        inspected = impl[\"_docker_json\"](\"inspect\", str(frozen[\"container\"]))
        state = inspected.get(\"State\") or {}
        host = inspected.get(\"HostConfig\") or {}
        labels = (inspected.get(\"Config\") or {}).get(\"Labels\") or {}
        ports = (inspected.get(\"NetworkSettings\") or {}).get(\"Ports\") or {}
        mysql_ports = ports.get(\"3306/tcp\") or []
        mounts = {
            str(row.get(\"Destination\") or \"\"): str(row.get(\"Source\") or \"\")
            for row in inspected.get(\"Mounts\") or []
        }
        if (
            state.get(\"Running\") is not True
            or labels.get(\"ra.owner\") != \"mysql_four_scene_v5\"
            or str(host.get(\"CpusetCpus\") or \"\") != str(frozen[\"database_cpuset\"])
            or len(mysql_ports) != 1
            or int(mysql_ports[0].get(\"HostPort\") or 0) != int(frozen[\"host_port\"])
            or not mounts.get(\"/var/lib/mysql\")
            or not mounts.get(\"/run/ra-credentials\")
        ):
            raise RuntimeError(f\"{worker_id} current physical slot identity differs\")
        credential_root = Path(mounts[\"/run/ra-credentials\"])
        prior.update({
            \"container_id\": inspected[\"Id\"],
            \"container_pid\": int(state.get(\"Pid\") or 0),
            \"datadir\": mounts[\"/var/lib/mysql\"],
            \"credential_defaults\": str(credential_root / \"selected.cnf\"),
            \"benchmark_defaults\": str(credential_root / \"benchmark.cnf\"),
            \"ready\": True,
            \"current_live_identity_read_back\": True,
            \"historical_container_id_superseded\": True,
            \"unused_slot_live_identity_synthesized\": unused_slot,
        })
        prior[\"pre_transition_host_resource_readback\"] = {
            \"nano_cpus\": int(host.get(\"NanoCpus\") or 0),
            \"cpu_quota\": int(host.get(\"CpuQuota\") or 0),
            \"cpu_period\": int(host.get(\"CpuPeriod\") or 0),
            \"memory\": int(host.get(\"Memory\") or 0),
            \"memory_swap\": int(host.get(\"MemorySwap\") or 0),
            \"pids_limit\": int(host.get(\"PidsLimit\") or 0),
            \"role\": \"old_slot_audit_not_target_envelope_validation\",
        }
        prior[\"blkio_authority\"] = impl[\"prep\"]._enforce_blkio(
            inspected, freeze, frozen
        )
        prior[\"current_blkio_authority_read_back\"] = True
"""
if text.count(old_prior) != 1:
    raise RuntimeError("CPU-A XP8 unused-slot predecessor block differs")
text = text.replace(old_prior, new_prior)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._prepare_mysql_xp8_cpu_a_slots_authority_v1_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


def main() -> int:
    return int(implementation["main"]())


if __name__ == "__main__":
    raise SystemExit(main())
