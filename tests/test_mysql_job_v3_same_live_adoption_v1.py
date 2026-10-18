#!/usr/bin/env python3
"""Focused offline tests for the JOB v3 same-live adoption successor."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shell/mysql_job_v3_same_live_adoption_v1.py"
if not SOURCE.is_file():
    SOURCE = ROOT / "runtime/mysql_job_v3_same_live_adoption_v1.py"
SPEC = importlib.util.spec_from_file_location("job_same_live", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PROJECT_ROOT = next(
    candidate for candidate in Path(__file__).resolve().parents
    if (candidate / "artifacts/mysql_job_v3_xp_v23_offline_materialization_v1").is_dir()
)
ORIGINAL_BINDING = PROJECT_ROOT / (
    "artifacts/mysql_job_v3_xp_v23_offline_materialization_v1/"
    "2ea99e7a257a1264315347ad9b6194212872197711573d11ebe25a164e205588/"
    "xp_successor_binding.json"
)
BOUND_CONTROL = PROJECT_ROOT / (
    "artifacts/mysql_job_v13_qualification_r0_closure_v3/"
    "6b55e29ef3c47cca437c94501511317c6c79dd1579be3dc6e3a4983587245f74"
)
STALE_CONTROL = PROJECT_ROOT / (
    "artifacts/mysql_job_v13_qualification_r0_closure_v3/"
    "7801df9f783c61fb5a72341c8bfc6ac04c28bd6f7729d12f7a5e987d28e3b1b3"
)


def proc(root: Path, pid: int, ticks: int, name: str, cpus: str, mems: str) -> None:
    directory = root / str(pid)
    directory.mkdir(parents=True)
    # fields after comm start at field 3; index 19 is Linux field 22.
    tail = ["S"] + ["0"] * 18 + [str(ticks)] + ["0"] * 4
    (directory / "stat").write_text(f"{pid} ({name}) " + " ".join(tail), encoding="ascii")
    (directory / "status").write_text(
        f"Name:\t{name}\nCpus_allowed_list:\t{cpus}\nMems_allowed_list:\t{mems}\n",
        encoding="utf-8",
    )


class SameLiveAdoptionTest(unittest.TestCase):
    def baseline(self) -> dict:
        return {
            "candidate_non_secret_adopted_service_identity": {
                "database": {"container_name": "current-db", "container_id": "a" * 64,
                             "pid": 44, "startticks": 101,
                             "started_at_utc": "2026-08-03T01:29:21.493621644Z",
                             "network_mode": "host"},
                "client": {"container_name": "current-client", "container_id": "b" * 64,
                           "pid": 45, "startticks": 102,
                           "started_at_utc": "2026-08-03T01:29:21.760024310Z",
                           "network_mode": "host"},
                "dedicated_docker_daemon": {"unit": "dedicated.service", "socket": "/run/dedicated.sock",
                                              "pid": 46, "startticks": 103, "process": "dockerd"},
            },
            "effective_resource_and_isolation_readback": {
                "database": {"cpu_period_us": 100000, "cpu_quota_us": 115000,
                             "cpuset_cpus": "2-3", "cpuset_mems": "1",
                             "process_cpus_allowed_list": "2-3", "process_mems_allowed_list": "1",
                             "memory_bytes": 4096, "memory_swap_bytes": 4096, "pids_max": 512,
                             "io_device": "/dev/test", "io_read_bps": 7, "io_write_bps": 8},
                "client": {"cpu_period_us": 100000, "cpu_quota_us": 200000,
                           "cpuset_cpus": "4-5", "cpuset_mems": "0",
                           "process_cpus_allowed_list": "4-5", "process_mems_allowed_list": "0",
                           "memory_bytes": 2048, "memory_swap_bytes": 2048, "pids_max": 256},
                "dedicated_daemon_cpuset_cpus": "6-7",
            },
        }

    def docker_rows(self) -> dict[str, dict]:
        common = {"running": True, "image_id": "sha256:" + "c" * 64,
                  "cpu_period_us": 100000, "network_mode": "host", "mounts": []}
        return {
            "current-db": dict(common, container_id="a" * 64, name="current-db", pid=44,
                               started_at="2026-08-03T01:29:21.493621644Z",
                               cpu_quota_us=115000, cpuset_cpus="2-3",
                               cpuset_mems="1", memory_bytes=4096, memory_swap_bytes=4096,
                               pids_max=512, root_filesystem_read_only=False,
                               io_read=[{"Path": "/dev/test", "Rate": 7}],
                               io_write=[{"Path": "/dev/test", "Rate": 8}]),
            "current-client": dict(common, container_id="b" * 64, name="current-client", pid=45,
                                   started_at="2026-08-03T01:29:21.76002431Z",
                                   cpu_quota_us=200000, cpuset_cpus="4-5",
                                   cpuset_mems="0", memory_bytes=2048, memory_swap_bytes=2048,
                                   pids_max=256, root_filesystem_read_only=True,
                                   io_read=[], io_write=[]),
        }

    def test_live_readback_is_double_read_and_resource_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proc(root, 44, 101, "mysqld", "2-3", "1")
            proc(root, 45, 102, "sleep", "4-5", "0")
            proc(root, 46, 103, "dockerd", "6-7", "0")
            rows = self.docker_rows()
            calls: list[str] = []
            def docker(_socket: Path, name: str) -> dict:
                calls.append(name)
                return dict(rows[name])
            with mock.patch.object(MODULE, "docker_readback", side_effect=docker), \
                 mock.patch.object(MODULE, "run_command", return_value="MainPID=46") as command:
                result = MODULE.validate_live(self.baseline(), Path("/run/dedicated.sock"), root)
            self.assertTrue(result["stable_across_two_selected_field_reads"])
            self.assertEqual(calls, ["current-db", "current-client", "current-db", "current-client"])
            command.assert_called_once_with([
                "systemctl", "show", "--property=MainPID", "dedicated.service",
            ])

    def test_changed_second_read_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for args in ((44, 101, "mysqld", "2-3", "1"), (45, 102, "sleep", "4-5", "0"),
                         (46, 103, "dockerd", "6-7", "0")):
                proc(root, *args)
            rows = self.docker_rows(); count = 0
            def docker(_socket: Path, name: str) -> dict:
                nonlocal count
                count += 1
                row = dict(rows[name])
                if count == 3:
                    row["pid"] = 99
                return row
            with mock.patch.object(MODULE, "docker_readback", side_effect=docker):
                with self.assertRaisesRegex(MODULE.AdoptionError, "not stable"):
                    MODULE.validate_live(self.baseline(), Path("/run/dedicated.sock"), root)

    def test_started_at_trailing_zero_is_the_same_nanosecond(self) -> None:
        baseline = "2026-08-03T01:29:21.760024310Z"
        docker = "2026-08-03T01:29:21.76002431Z"
        self.assertEqual(
            MODULE.rfc3339_utc_nanoseconds(baseline, "baseline"),
            MODULE.rfc3339_utc_nanoseconds(docker, "docker"),
        )
        rows = self.docker_rows()
        first = {"database": rows["current-db"], "client": rows["current-client"]}
        second = {role: dict(row) for role, row in first.items()}
        second["client"]["started_at"] = baseline
        self.assertEqual(MODULE.stable_readback_changes(first, second), [])

    def test_started_at_one_nanosecond_change_fails_closed(self) -> None:
        baseline = "2026-08-03T01:29:21.760024310Z"
        changed = "2026-08-03T01:29:21.760024311Z"
        self.assertEqual(
            MODULE.rfc3339_utc_nanoseconds(changed, "changed")
            - MODULE.rfc3339_utc_nanoseconds(baseline, "baseline"),
            1,
        )
        rows = self.docker_rows()
        first = {"database": rows["current-db"], "client": rows["current-client"]}
        second = {role: dict(row) for role, row in first.items()}
        second["client"]["started_at"] = changed
        self.assertEqual(MODULE.stable_readback_changes(first, second),
                         ["client.started_at"])

    def test_systemd219_mainpid_fixture_is_strict(self) -> None:
        self.assertEqual(MODULE.parse_systemd_main_pid("MainPID=46"), 46)
        for invalid in (
            "MainPID=0", "46", "MainPID=46\nMainPID=46", "MainPID=46\nOther=x",
            "Other=x\nMainPID=46", "MainPID=", "MainPID=+46", " MainPID=46",
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                    MODULE.AdoptionError, "systemd MainPID"):
                MODULE.parse_systemd_main_pid(invalid)

    def test_mount_and_io_order_and_irrelevant_fields_are_canonicalized(self) -> None:
        first = self.docker_rows()
        first["current-db"]["io_read"] = [
            {"Path": "/dev/z", "Rate": 9}, {"Path": "/dev/test", "Rate": 7},
        ]
        first["current-db"]["mounts"] = [
            {"Type": "bind", "Source": "/data", "Destination": "/var/lib/mysql",
             "RW": True, "Propagation": "rprivate", "DynamicDiagnostic": "first"},
            {"Type": "bind", "Source": "/bundle", "Destination": "/bundle",
             "RW": False, "Propagation": "rprivate", "DynamicDiagnostic": "first"},
        ]
        second = {"database": dict(first["current-db"]),
                  "client": dict(first["current-client"])}
        second["database"]["io_read"] = list(reversed(first["current-db"]["io_read"]))
        second["database"]["mounts"] = [dict(row) for row in reversed(first["current-db"]["mounts"])]
        for row in second["database"]["mounts"]:
            row["DynamicDiagnostic"] = "second"
        observed = {"database": first["current-db"], "client": first["current-client"]}
        self.assertEqual(MODULE.stable_readback_changes(observed, second), [])

    def test_real_resource_or_mount_change_remains_fail_closed(self) -> None:
        rows = self.docker_rows()
        first = {"database": rows["current-db"], "client": rows["current-client"]}
        changed_rate = {role: dict(row) for role, row in first.items()}
        changed_rate["database"]["io_read"] = [{"Path": "/dev/test", "Rate": 99}]
        self.assertEqual(MODULE.stable_readback_changes(first, changed_rate),
                         ["database.io_read"])
        first["database"]["mounts"] = [
            {"Type": "bind", "Source": "/data", "Destination": "/var/lib/mysql", "RW": True}
        ]
        changed_mount = {role: dict(row) for role, row in first.items()}
        changed_mount["database"]["mounts"] = [
            {"Type": "bind", "Source": "/other", "Destination": "/var/lib/mysql", "RW": True}
        ]
        self.assertEqual(MODULE.stable_readback_changes(first, changed_mount),
                         ["database.mounts"])

    def test_mount_closure_is_order_independent_but_set_and_rw_exact(self) -> None:
        mounts = [
            {"Type": "bind", "Source": "/bundle", "Destination": "/bundle", "RW": False},
            {"Type": "bind", "Source": "/data", "Destination": "/var/lib/mysql", "RW": True},
        ]
        source = {
            "database": {"inspect": {"Mounts": mounts}},
            "client": {"inspect": {"Mounts": []}},
        }
        live = {
            "database": {"mounts": list(reversed(mounts))},
            "client": {"mounts": []},
        }
        MODULE.validate_same_live_mount_closure(source, live)
        live["database"]["mounts"][0] = dict(live["database"]["mounts"][0], RW=False)
        with self.assertRaisesRegex(MODULE.AdoptionError, "mount closure"):
            MODULE.validate_same_live_mount_closure(source, live)

    def test_credential_safe_docker_projection_never_requests_env(self) -> None:
        joined = json.dumps(MODULE.DOCKER_FIELDS, sort_keys=True).lower()
        self.assertNotIn("env", joined)
        self.assertNotIn(".config", joined)

    def test_refreshed_bootstrap_projection_drops_config_env(self) -> None:
        evidence = {
            "database": {"pid": 44, "inspect": {
                "Id": "a" * 64, "Name": "/db", "Image": "image", "Created": "time",
                "State": {"Running": True, "Pid": 44, "StartedAt": "time", "Health": {"secret": "x"}},
                "Config": {"Image": "image", "User": "mysql", "Env": ["PASSWORD=must-not-copy"], "Labels": {}},
                "HostConfig": {"CpusetCpus": "2-3", "CpusetMems": "1"}, "Mounts": [],
            }},
            "client": {"pid": 45, "inspect": {"Config": {"Env": ["TOKEN=must-not-copy"]}, "HostConfig": {}, "Mounts": []}},
            "collect_only": {"document": {"status": "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY"}},
        }
        safe = MODULE.safe_bootstrap_evidence(evidence)
        serialized = json.dumps(safe, sort_keys=True)
        self.assertNotIn("must-not-copy", serialized)
        self.assertNotIn('"Env"', serialized)
        self.assertEqual(safe["database"]["pid"], 44)

    def test_no_current_pid_cid_or_name_is_static_in_production_source(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "98480", "98561", "53878", "7326cb375889", "a7776ecab52c",
            "ra_mysql_job_v2_job_v2_20260803t011839z",
        ):
            self.assertNotIn(forbidden, source)

    def test_proc_startticks_parses_comm_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proc(root, 70, 123456, "name with spaces", "1", "0")
            self.assertEqual(MODULE.proc_identity(root, 70)["startticks"], 123456)

    def test_cpuset_overlap_math(self) -> None:
        self.assertEqual(MODULE.cpuset("0-2,5,8-9"), {0, 1, 2, 5, 8, 9})

    def test_storage_readback_requires_tmpfs_xfs_mounts_and_listener(self) -> None:
        baseline = self.baseline()
        baseline["candidate_non_secret_adopted_service_identity"]["database"]["mysql_port"] = 65102
        baseline["effective_resource_and_isolation_readback"]["database"]["datadir_filesystem"] = "xfs"
        baseline["effective_resource_and_isolation_readback"]["client"]["measurement_filesystem"] = "tmpfs"
        spec = {"paths": {"datadir": "/data/job", "host_measurement_tmpfs": "/dev/shm/job"}}
        live = {"database": {"mounts": [{"Source": "/data/job", "Destination": "/var/lib/mysql", "RW": True}]},
                "client": {"mounts": [{"Source": "/dev/shm/job", "Destination": "/measurement", "RW": True}]}}
        with mock.patch.object(MODULE, "run_command", side_effect=["xfs", "tmpfs", "LISTEN 0 128 0.0.0.0:65102 0.0.0.0:*"]):
            MODULE.validate_storage_and_listener(spec, baseline, live)

    def test_node02_python36_is_rejected_before_remote_readback(self) -> None:
        with self.assertRaisesRegex(MODULE.AdoptionError, "Python 3.11.11"):
            MODULE.verify_running_python(executable=Path("/usr/bin/python3"),
                                         version=(3, 6, 8))

    def test_original_2ea99_binding_basename_and_sidecar_are_accepted(self) -> None:
        value = MODULE.verify_xp_binding_basename(ORIGINAL_BINDING)
        self.assertEqual(value["xp_bundle_id"], MODULE.XP_V23_ID)

    def test_renamed_binding_with_original_sidecar_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            renamed = Path(temporary) / "xp_v23_successor_binding.json"
            shutil.copyfile(ORIGINAL_BINDING, renamed)
            shutil.copyfile(Path(str(ORIGINAL_BINDING) + ".sha256"),
                            Path(str(renamed) + ".sha256"))
            with self.assertRaisesRegex(MODULE.AdoptionError, "basename"):
                MODULE.verify_xp_binding_basename(renamed)

    def test_all_authority_commands_bind_the_sealed_python(self) -> None:
        template = json.loads((ROOT / "templates/mysql_job_v3_same_live_adoption_commands_v1.json").read_text())
        commands = [value for key, value in template.items() if key.endswith("_argv")]
        self.assertTrue(commands)
        self.assertTrue(all(command[0] == str(MODULE.SEALED_PYTHON) for command in commands))
        self.assertNotIn("/usr/bin/python3", json.dumps(template))
        self.assertEqual(template["xp_successor_binding_basename"],
                         MODULE.XP_BINDING_BASENAME)
        self.assertEqual(template["bound_job_control_bundle_id"], MODULE.CONTROL_ID)
        self.assertEqual(template["bound_job_control_manifest_sha256"],
                         MODULE.CONTROL_MANIFEST_SHA256)
        self.assertEqual(
            template["bound_shared_dispatch_sha256"],
            json.loads((BOUND_CONTROL / "bundle_manifest.json").read_text())["files"]
            ["qualification/mysql_job_xp_shared_qualification_dispatch_v1.py"],
        )

    def test_adoption_accepts_only_the_6b55_control_closure(self) -> None:
        value = MODULE.verify_bundle(
            BOUND_CONTROL, MODULE.CONTROL_ID,
            MODULE.CONTROL_MANIFEST_SHA256, exact_closure=True,
        )
        self.assertEqual(value["bundle_id"], MODULE.CONTROL_ID)
        with self.assertRaisesRegex(MODULE.AdoptionError, "bundle directory identity"):
            MODULE.verify_bundle(
                STALE_CONTROL, MODULE.CONTROL_ID,
                MODULE.CONTROL_MANIFEST_SHA256, exact_closure=True,
            )

    def test_control_bundle_symlink_is_rejected_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            link = Path(temporary) / MODULE.CONTROL_ID
            link.symlink_to(BOUND_CONTROL, target_is_directory=True)
            with self.assertRaisesRegex(MODULE.AdoptionError,
                                        "bundle directory identity"):
                MODULE.verify_bundle(
                    link, MODULE.CONTROL_ID,
                    MODULE.CONTROL_MANIFEST_SHA256, exact_closure=True,
                )


if __name__ == "__main__":
    unittest.main()
