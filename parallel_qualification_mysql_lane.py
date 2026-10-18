#!/usr/bin/env python3
"""Run one non-countable MySQL lane for two-lane interference qualification."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import time
from typing import Any, Dict, Optional

import formal_mysql_campaign_20260721 as campaign
import formal_resource_sampler_20260721 as resource_sampler


QUALIFICATION_ID = "two_lane_qualification_20260721_v1"
CASE_ID = "mysql_hybench_xp_io"
MYSQL_QUALIFICATION_DEVICE = Path("/dev/nvme2n1")


def qualification_docker_run_args(original: Any) -> Any:
    """Use Docker's unambiguous device node for the frozen 259:6 device."""
    def build(*args: Any, **kwargs: Any) -> Any:
        argv = original(*args, **kwargs)
        frozen = Path("/dev/block/259:6")
        if frozen.resolve() != MYSQL_QUALIFICATION_DEVICE.resolve():
            raise RuntimeError(
                f"qualification MySQL device mismatch: {frozen} != {MYSQL_QUALIFICATION_DEVICE}"
            )
        replacements = 0
        prefix = str(frozen) + ":"
        for index, value in enumerate(argv):
            if isinstance(value, str) and value.startswith(prefix):
                argv[index] = str(MYSQL_QUALIFICATION_DEVICE) + value[len(str(frozen)):]
                replacements += 1
        if replacements != 2:
            raise RuntimeError(
                f"qualification expected two Docker I/O device arguments, got {replacements}"
            )
        return argv

    return build


class QualificationCgroupV1Adapter(resource_sampler.CgroupV1Adapter):
    """Apply qualification caps that this host's Docker silently omits."""

    def __init__(self, container: str, device: str, profile: Dict[str, Any]):
        inspect = json.loads(campaign.run(["docker", "inspect", container]).stdout)[0]
        pid = int(inspect["State"]["Pid"])
        paths = resource_sampler._controller_paths(pid)

        blkio = paths["blkio"]
        for filename, key in (
            ("blkio.throttle.read_bps_device", "io_read_bps"),
            ("blkio.throttle.write_bps_device", "io_write_bps"),
        ):
            requested = profile[key]
            if requested is None:
                raise resource_sampler.ResourceError(
                    f"qualification requires a finite {key}"
                )
            campaign.run(
                ["sudo", "tee", str(blkio / filename)],
                input_text=f"{device} {requested}\n",
            )

        memory = paths["memory"]
        memsw = memory / "memory.memsw.limit_in_bytes"
        no_swap_equivalence = False
        if not memsw.is_file():
            swaps = Path("/proc/swaps").read_text(encoding="utf-8").splitlines()[1:]
            if swaps:
                raise resource_sampler.ResourceError(
                    "qualification cannot emulate no-swap semantics while host swap is active"
                )
            if profile["memory_swap_bytes"] != profile["memory_bytes"]:
                raise resource_sampler.ResourceError(
                    "qualification memsw fallback requires swap limit equal to memory limit"
                )
            original_int = resource_sampler._int
            memory_limit = memory / "memory.limit_in_bytes"

            def qualification_int(path: Path) -> int:
                if path == memsw:
                    return original_int(memory_limit)
                return original_int(path)

            resource_sampler._int = qualification_int
            no_swap_equivalence = True

        super().__init__(container, device, profile)
        self.readback["qualification_cap_application"] = (
            "explicit_container_cgroup_v1_blkio_write_and_readback"
        )
        self.readback["qualification_no_swap_equivalence"] = no_swap_equivalence
        self.readback["host_swap_entries"] = []


def qualification_database_sample(port: int, *, elapsed_s: float, phase: str) -> Dict[str, Any]:
    """Read the complete status set through MySQL's SHOW compatibility API."""
    names = ",".join(repr(name) for name in campaign.DB_STATUS_NAMES)
    rows = campaign._tabular(
        port,
        f"SHOW GLOBAL STATUS WHERE Variable_name IN ({names});",
    )
    canonical = {name.lower(): name for name in campaign.DB_STATUS_NAMES}
    status = {canonical.get(row[0].lower(), row[0]): int(row[1]) for row in rows}
    missing = set(campaign.DB_STATUS_NAMES) - set(status)
    if missing:
        raise campaign.GateError(
            f"qualification database status sample missing counters: {sorted(missing)}"
        )
    return {
        "epoch_ms": time.time_ns() // 1_000_000,
        "elapsed_s": elapsed_s,
        "phase": phase,
        "status_counters": status,
        "status_source": "SHOW GLOBAL STATUS",
    }


def lane_machine(machine: Dict[str, Any], cpuset_cpus: str, cpuset_mems: str) -> Dict[str, Any]:
    """Return an isolated qualification-only copy of the frozen machine contract."""
    value = copy.deepcopy(machine)
    profile = value["resource_profiles"]["io"]
    profile["cpuset_cpus"] = cpuset_cpus
    profile["cpuset_mems"] = cpuset_mems
    return value


def wait_for_release(barrier_dir: Path, lane: str, timeout_seconds: int) -> None:
    """Publish readiness and wait for the coordinator's immutable release marker."""
    barrier_dir.mkdir(parents=True, exist_ok=True)
    ready = barrier_dir / f"{lane}.ready.json"
    ready.write_text(
        json.dumps(
            {
                "qualification_id": QUALIFICATION_ID,
                "lane": lane,
                "ready_epoch_seconds": time.time(),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    deadline = time.monotonic() + timeout_seconds
    release = barrier_dir / "release.json"
    while time.monotonic() < deadline:
        if release.is_file():
            payload = json.loads(release.read_text(encoding="utf-8"))
            if payload.get("qualification_id") != QUALIFICATION_ID:
                raise RuntimeError("qualification barrier release id mismatch")
            return
        time.sleep(0.1)
    raise TimeoutError(f"qualification barrier timeout: {barrier_dir}")


def run_lane(
    output_root: Path,
    *,
    cpuset_cpus: str,
    cpuset_mems: str,
    barrier_dir: Optional[Path],
) -> Dict[str, Any]:
    configs = campaign._configs()
    machine, fixed, benchmark, _, _, search_spaces, _, _ = configs
    machine = lane_machine(machine, cpuset_cpus, cpuset_mems)
    campaign.GATE_ROOT = output_root
    campaign._docker_run_args = qualification_docker_run_args(campaign._docker_run_args)
    campaign.CgroupV1Adapter = QualificationCgroupV1Adapter
    campaign._database_sample = qualification_database_sample

    if barrier_dir is not None:
        original_sampling = campaign.run_with_sampling
        armed = True

        def synchronized_sampling(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            nonlocal armed
            if armed:
                wait_for_release(barrier_dir, "mysql", 1800)
                armed = False
            return original_sampling(*args, **kwargs)

        campaign.run_with_sampling = synchronized_sampling

    points = campaign.gate_points(search_spaces)
    return campaign.run_gate_attempt(
        CASE_ID,
        "P0",
        1,
        machine,
        fixed,
        benchmark,
        points,
        host_admission_evidence={
            "purpose": QUALIFICATION_ID,
            "countable_as_formal_result": False,
            "global_formal_scheduler_touched": False,
            "lane": "mysql",
            "cpuset_cpus": cpuset_cpus,
            "cpuset_mems": cpuset_mems,
        },
        host_machine_readback={
            "qualification_only": True,
            "frozen_device_major_minor": machine["engines"]["mysql"]["observed_device"],
            "docker_io_device_path": str(MYSQL_QUALIFICATION_DEVICE),
        },
        measurement_seconds=180,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--cpuset-cpus", required=True)
    parser.add_argument("--cpuset-mems", required=True)
    parser.add_argument("--barrier-dir", type=Path)
    args = parser.parse_args()
    result = run_lane(
        args.output_root,
        cpuset_cpus=args.cpuset_cpus,
        cpuset_mems=args.cpuset_mems,
        barrier_dir=args.barrier_dir,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
