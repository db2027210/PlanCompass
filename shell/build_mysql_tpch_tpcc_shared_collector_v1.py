#!/usr/bin/env python3
"""Publish the byte-identical node04 TPC-H/TPC-C four-flow collector."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).resolve()
TPCH = ROOT / "artifacts/mysql_tpch_memswap_consistency_successor_v1/87ed507ed689c103781db5f96332ae7875e4773d04d69b98a7e63e3d4097b257"
TPCC = ROOT / "artifacts/mysql_tpcc_v13_raw_all_subset_successor_v3/22bf40890070a2bf147e593d8bd163ea2e16c13123d063583e52ff563461721d"
OUTPUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v1"


def canonical(value):
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def checked_target(bundle, schema):
    manifest = json.loads((bundle / "bundle_manifest.json").read_text())
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != bundle.name
        or manifest.get("schema_version") != schema
        or digest_bytes(canonical(identity)) != bundle.name
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
    ):
        raise RuntimeError("target bundle identity differs: %s" % bundle.name)
    return manifest


def main():
    tpch_manifest = checked_target(TPCH, "mysql_tpch_v23_recalibrated_successor_v3")
    tpcc_manifest = checked_target(
        TPCC, "mysql_tpcc_v13_dedicated_daemon_successor_bundle_v3"
    )
    paths = {
        "qualification/mysql_distributed_live_readback_qualification_v1.py":
            TPCH / "qualification/mysql_distributed_live_readback_qualification_v1.py",
        "qualification/runtime_tree_content_identity_v3.py":
            TPCH / "qualification/runtime_tree_content_identity_v3.py",
        "qualification/mysql_distributed_workload_metric_adapter_v1.py":
            TPCH / "qualification/mysql_distributed_workload_metric_adapter_v1.py",
        "qualification/mysql_tpch_client_immutable_identity_v1.py":
            TPCH / "qualification/mysql_tpch_client_immutable_identity_v1.py",
        "qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py":
            ROOT / "shell/mysql_tpch_tpcc_shared_collector_entry_v1.py",
        "qualification/mysql_shared_tpcc_counter_transport_v1.py":
            ROOT / "shell/mysql_shared_tpcc_counter_transport_v1.py",
        "qualification/mysql_shared_docker_mount_inventory_v1.py":
            ROOT / "shell/mysql_shared_docker_mount_inventory_v1.py",
        "authority/mysql_remediate_dedicated_daemon_affinity_v1.py":
            ROOT / "shell/mysql_remediate_dedicated_daemon_affinity_v1.py",
        "authority/build_mysql_tpch_tpcc_shared_collector_v1.py": SOURCE,
        "authority/tpch_target_manifest.json": TPCH / "bundle_manifest.json",
        "authority/tpcc_target_manifest.json": TPCC / "bundle_manifest.json",
    }
    items = {}
    for relative, path in paths.items():
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("source file absent or symlinked: %s" % path)
        items[relative] = path.read_bytes()
    core_relative = "qualification/mysql_distributed_live_readback_qualification_v1.py"
    old_shutdown = b'''        finally:\n            if process is not None and process.poll() is None:\n                try:\n                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)\n                    process.wait(timeout=120)\n                except (OSError, subprocess.TimeoutExpired):\n                    try:\n                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)\n                    except OSError:\n                        pass\n                    process.wait()\n'''
    new_shutdown = b'''        finally:\n            if process is not None and process.poll() is None:\n                stop_error = None\n                if native_adapter_kind == "benchbase_tpcc":\n                    try:\n                        container_root = str(\n                            spec["measurement_output"][\n                                "container_tmpfs_destination"\n                            ]\n                        ).rstrip("/")\n                        stop_container = str(\n                            (spec.get("environment") or {}).get(\n                                "BENCHBASE_FORMAL_STOP_FILE"\n                            ) or ""\n                        )\n                        required_prefix = container_root + "/"\n                        if (\n                            not stop_container.startswith(required_prefix)\n                            or "/" in stop_container[len(required_prefix):]\n                            or stop_container.endswith("/")\n                        ):\n                            raise QualificationError(\n                                "BenchBase stop-file binding differs"\n                            )\n                        stop_host = Path(\n                            spec["measurement_output"][\n                                "host_tmpfs_directory"\n                            ]\n                        ) / stop_container[len(required_prefix):]\n                        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL\n                        if hasattr(os, "O_NOFOLLOW"):\n                            flags |= os.O_NOFOLLOW\n                        descriptor = os.open(str(stop_host), flags, 0o600)\n                        try:\n                            os.write(descriptor, b"180\\n")\n                            os.fsync(descriptor)\n                        finally:\n                            os.close(descriptor)\n                        qualification_stop_request = {\n                            "container_path": stop_container,\n                            "host_path": str(stop_host),\n                            "payload_utf8": "180\\n",\n                            "sha256": sha256(stop_host),\n                            "atomic_exclusive_create": True,\n                        }\n                    except (OSError, KeyError, TypeError, ValueError) as exc:\n                        stop_error = str(exc)\n                timed_out = False\n                try:\n                    # SQL runs for 240 seconds.  BenchBase receives the\n                    # manifest-bound atomic 180-second stop request above.\n                    # Both must exit naturally before immutable log hashing.\n                    if stop_error is not None:\n                        raise subprocess.TimeoutExpired(process.args, 0)\n                    process.wait(timeout=75)\n                except subprocess.TimeoutExpired:\n                    timed_out = True\n                    try:\n                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)\n                        process.wait(timeout=120)\n                    except (OSError, subprocess.TimeoutExpired):\n                        try:\n                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)\n                        except OSError:\n                            pass\n                        process.wait()\n                if stop_error is not None:\n                    raise QualificationError(\n                        "BenchBase atomic stop request failed: %s" % stop_error\n                    )\n                if timed_out:\n                    raise QualificationError(\n                        "debug qualification workload did not exit gracefully "\n                        "within 75s"\n                    )\n'''
    if items[core_relative].count(old_shutdown) != 1:
        raise RuntimeError("shared collector shutdown patch point differs")
    items[core_relative] = items[core_relative].replace(old_shutdown, new_shutdown)
    old_container_timeout_cleanup = b'''                except subprocess.TimeoutExpired:
                    timed_out = True
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                        process.wait(timeout=120)
                    except (OSError, subprocess.TimeoutExpired):
                        try:
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        except OSError:
                            pass
                        process.wait()
'''
    new_container_timeout_cleanup = b'''                except subprocess.TimeoutExpired:
                    timed_out = True
                    # Killing the host-side docker-exec CLI does not kill its
                    # process in the client container.  Bind the exact sealed
                    # argv and client cgroup before terminating one process
                    # group; never use a name-wide or container-wide kill.
                    expected_cmdline = [
                        os.fsencode(str(value)) for value in spec["argv"]
                    ]
                    workload_roots = []
                    for proc_entry in Path("/proc").iterdir():
                        if not proc_entry.name.isdigit():
                            continue
                        try:
                            raw_cmdline = (
                                proc_entry / "cmdline"
                            ).read_bytes().split(b"\\0")
                            if raw_cmdline and raw_cmdline[-1] == b"":
                                raw_cmdline.pop()
                            cgroup_text = (
                                proc_entry / "cgroup"
                            ).read_text(encoding="ascii", errors="strict")
                            if (
                                raw_cmdline == expected_cmdline
                                and ("/docker/%s" % client_id) in cgroup_text
                            ):
                                workload_roots.append(
                                    (
                                        int(proc_entry.name),
                                        process_start_ticks(int(proc_entry.name)),
                                    )
                                )
                        except (OSError, UnicodeError, ValueError):
                            continue
                    container_cleanup_ok = len(workload_roots) == 1
                    if container_cleanup_ok:
                        workload_pid, workload_start_ticks = workload_roots[0]
                        try:
                            os.killpg(os.getpgid(workload_pid), signal.SIGTERM)
                        except OSError:
                            try:
                                os.kill(workload_pid, signal.SIGTERM)
                            except OSError:
                                pass
                        cleanup_deadline = time.monotonic() + 10.0
                        while time.monotonic() < cleanup_deadline:
                            try:
                                if (
                                    process_start_ticks(workload_pid)
                                    != workload_start_ticks
                                ):
                                    break
                            except (OSError, QualificationError):
                                break
                            time.sleep(0.05)
                        try:
                            still_same = (
                                process_start_ticks(workload_pid)
                                == workload_start_ticks
                            )
                        except (OSError, QualificationError):
                            still_same = False
                        if still_same:
                            try:
                                os.killpg(
                                    os.getpgid(workload_pid), signal.SIGKILL
                                )
                            except OSError:
                                try:
                                    os.kill(workload_pid, signal.SIGKILL)
                                except OSError:
                                    pass
                            time.sleep(0.1)
                        try:
                            container_cleanup_ok = (
                                process_start_ticks(workload_pid)
                                != workload_start_ticks
                            )
                        except (OSError, QualificationError):
                            container_cleanup_ok = True
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                        process.wait(timeout=120)
                    except (OSError, subprocess.TimeoutExpired):
                        try:
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        except OSError:
                            pass
                        process.wait()
'''
    if items[core_relative].count(old_container_timeout_cleanup) != 1:
        raise RuntimeError("shared collector container cleanup patch point differs")
    items[core_relative] = items[core_relative].replace(
        old_container_timeout_cleanup, new_container_timeout_cleanup
    )
    old_cleanup_result = b'''                if timed_out:
                    raise QualificationError(
                        "debug qualification workload did not exit gracefully "
                        "within 75s"
                    )
'''
    new_cleanup_result = b'''                if timed_out and not container_cleanup_ok:
                    raise QualificationError(
                        "timed-out workload exact container cleanup failed"
                    )
                if timed_out:
                    raise QualificationError(
                        "debug qualification workload did not exit gracefully "
                        "within 75s"
                    )
'''
    if items[core_relative].count(old_cleanup_result) != 1:
        raise RuntimeError("shared collector cleanup result patch point differs")
    items[core_relative] = items[core_relative].replace(
        old_cleanup_result, new_cleanup_result
    )
    old_stop_state = b'''    docker_exec_argv.extend([client_container_name] + spec["argv"])\n    with stdout_path.open("wb") as stdout_stream, stderr_path.open(\n'''
    new_stop_state = b'''    docker_exec_argv.extend([client_container_name] + spec["argv"])\n    qualification_stop_request = None\n    with stdout_path.open("wb") as stdout_stream, stderr_path.open(\n'''
    if items[core_relative].count(old_stop_state) != 1:
        raise RuntimeError("shared collector stop-state patch point differs")
    items[core_relative] = items[core_relative].replace(
        old_stop_state, new_stop_state
    )
    old_evidence = b'''        "workload_stderr": str(stderr_path),\n        "workload_stderr_sha256": sha256(stderr_path),\n        "debug_only": True,\n'''
    new_evidence = b'''        "workload_stderr": str(stderr_path),\n        "workload_stderr_sha256": sha256(stderr_path),\n        "qualification_stop_request": qualification_stop_request,\n        "workload_exit": {\n            "returncode": process.returncode,\n            "graceful_within_seconds": 75,\n            "forced_termination": False,\n        },\n        "debug_only": True,\n'''
    if items[core_relative].count(old_evidence) != 1:
        raise RuntimeError("shared collector exit-evidence patch point differs")
    items[core_relative] = items[core_relative].replace(old_evidence, new_evidence)
    old_stop_schema = b'''                        qualification_stop_request = {\n                            "container_path": stop_container,\n'''
    new_stop_schema = b'''                        qualification_stop_request = {\n                            "schema_version":\n                                "mysql_algorithm_c_benchbase_stop_request_evidence_v1",\n                            "container_path": stop_container,\n'''
    if items[core_relative].count(old_stop_schema) != 1:
        raise RuntimeError("shared collector stop-schema patch point differs")
    items[core_relative] = items[core_relative].replace(
        old_stop_schema, new_stop_schema
    )
    old_stable_projection = b'''    def stable_process_projection(rows):\n        return [\n            {key: row.get(key) for key in stable_keys}\n            for row in rows\n        ]\n\n    baseline_processes = stable_process_projection(\n        first_inventory["processes"]\n    )\n'''
    new_stable_projection = b'''    def stable_process_projection(inventory):\n        # The sealed workload root must remain stable, but its short-lived\n        # children are part of the authorized workload implementation.  SQL\n        # qualification intentionally invokes one mysql CLI child per query,\n        # so child PID churn is not co-resident interference.  Continue to\n        # compare every target, control-plane, and co-resident process.\n        workload_root_pid = _integer(\n            inventory.get("workload_root_pid"), "workload root PID", 1\n        )\n        return [\n            {key: row.get(key) for key in stable_keys}\n            for row in inventory["processes"]\n            if (\n                row.get("role") != "qualification_workload"\n                or row.get("pid") == workload_root_pid\n            )\n        ]\n\n    baseline_processes = stable_process_projection(first_inventory)\n'''
    if items[core_relative].count(old_stable_projection) != 1:
        raise RuntimeError("shared collector stable projection patch point differs")
    items[core_relative] = items[core_relative].replace(
        old_stable_projection, new_stable_projection
    )
    old_inventory_failure = b'''        if (\n            not isinstance(rolling, dict)\n            or rolling.get("inventory_complete") is not True\n            or rolling.get("conflicts") != []\n            or not isinstance(rolling.get("processes"), list)\n            or rolling.get("permission_failure_count") != 0\n            or rolling.get("race_count") != 0\n            or rolling.get("foreign_writer_count") != 0\n        ):\n            raise QualificationError(\n                "rolling co-resident process inventory conflicts/incomplete"\n            )\n'''
    new_inventory_failure = b'''        if (\n            not isinstance(rolling, dict)\n            or rolling.get("inventory_complete") is not True\n            or rolling.get("conflicts") != []\n            or not isinstance(rolling.get("processes"), list)\n            or rolling.get("permission_failure_count") != 0\n            or rolling.get("race_count") != 0\n            or rolling.get("foreign_writer_count") != 0\n        ):\n            diagnostic = {\n                "sample_index": expected_index,\n                "inventory_complete": (\n                    rolling.get("inventory_complete")\n                    if isinstance(rolling, dict) else None\n                ),\n                "conflicts": (\n                    rolling.get("conflicts")\n                    if isinstance(rolling, dict) else None\n                ),\n                "permission_failure_count": (\n                    rolling.get("permission_failure_count")\n                    if isinstance(rolling, dict) else None\n                ),\n                "race_count": (\n                    rolling.get("race_count")\n                    if isinstance(rolling, dict) else None\n                ),\n                "foreign_writer_count": (\n                    rolling.get("foreign_writer_count")\n                    if isinstance(rolling, dict) else None\n                ),\n                "relevant_processes": (\n                    [\n                        row for row in rolling.get("processes", [])\n                        if row.get("role") in (\n                            "qualification_workload", "co_resident"\n                        )\n                    ][:24]\n                    if isinstance(rolling, dict) else []\n                ),\n            }\n            raise QualificationError(\n                "rolling co-resident process inventory conflicts/incomplete: %s"\n                % json.dumps(diagnostic, sort_keys=True, separators=(",", ":"))\n            )\n'''
    if items[core_relative].count(old_inventory_failure) != 1:
        raise RuntimeError("shared collector inventory diagnostic patch point differs")
    items[core_relative] = items[core_relative].replace(
        old_inventory_failure, new_inventory_failure
    )
    old_validate_exit = b'''    if expected_mode == "qualification_solo":\n'''
    new_validate_exit = b'''    workload_exit = evidence.get("workload_exit")\n    stop_request = evidence.get("qualification_stop_request")\n    if (\n        not isinstance(workload_exit, dict)\n        or workload_exit.get("returncode") != 0\n        or workload_exit.get("graceful_within_seconds") != 75\n        or workload_exit.get("forced_termination") is not False\n    ):\n        raise QualificationError("qualification workload exit proof differs")\n    if scenario == "tpcc_cpu":\n        if (\n            not isinstance(stop_request, dict)\n            or stop_request.get("schema_version")\n            != "mysql_algorithm_c_benchbase_stop_request_evidence_v1"\n            or stop_request.get("payload_utf8") != "180\\n"\n            or stop_request.get("atomic_exclusive_create") is not True\n            or stop_request.get("sha256") != sha_bytes(b"180\\n")\n            or not str(stop_request.get("container_path") or "").startswith(\n                "/measurement/"\n            )\n            or not isinstance(stop_request.get("host_path"), str)\n        ):\n            raise QualificationError("BenchBase stop-request proof differs")\n        stop_path = Path(stop_request["host_path"])\n        if (\n            stop_path.is_symlink()\n            or not stop_path.is_file()\n            or sha256(stop_path) != stop_request["sha256"]\n        ):\n            raise QualificationError("BenchBase stop-request artifact differs")\n    elif stop_request is not None:\n        raise QualificationError("non-BenchBase evidence has a stop request")\n    if expected_mode == "qualification_solo":\n'''
    if items[core_relative].count(old_validate_exit) != 1:
        raise RuntimeError("shared collector exit-validation patch point differs")
    items[core_relative] = items[core_relative].replace(
        old_validate_exit, new_validate_exit
    )
    old_stability = b'''    for sample in samples[1:]:\n        current_inventory = sample["required_streams"][\n            "process_cpu_affinity_and_device_inventory"\n        ]["rolling_full_host_inventory"]\n        if (\n            stable_process_projection(current_inventory["processes"])\n            != baseline_processes\n            or current_inventory["target_cgroup_paths_sha256"]\n            != baseline_target_cgroup\n            or current_inventory["workload_cgroup_paths_sha256"]\n            != baseline_workload_cgroup\n            or current_inventory["docker_inventory_sha256"]\n            != baseline_docker_inventory\n        ):\n            raise QualificationError(\n                "co-resident process appeared/disappeared/changed"\n            )\n'''
    new_stability = b'''    for sample_index, sample in enumerate(samples[1:], 1):\n        current_inventory = sample["required_streams"][\n            "process_cpu_affinity_and_device_inventory"\n        ]["rolling_full_host_inventory"]\n        current_processes = stable_process_projection(current_inventory)\n        if (\n            current_processes != baseline_processes\n            or current_inventory["target_cgroup_paths_sha256"]\n            != baseline_target_cgroup\n            or current_inventory["workload_cgroup_paths_sha256"]\n            != baseline_workload_cgroup\n            or current_inventory["docker_inventory_sha256"]\n            != baseline_docker_inventory\n        ):\n            baseline_by_pid = {\n                (row.get("pid"), row.get("starttime_ticks")): row\n                for row in baseline_processes\n            }\n            current_by_pid = {\n                (row.get("pid"), row.get("starttime_ticks")): row\n                for row in current_processes\n            }\n            diagnostics = {\n                "sample_index": sample_index,\n                "added_processes": [\n                    current_by_pid[key]\n                    for key in sorted(set(current_by_pid) - set(baseline_by_pid))\n                ][:16],\n                "removed_processes": [\n                    baseline_by_pid[key]\n                    for key in sorted(set(baseline_by_pid) - set(current_by_pid))\n                ][:16],\n                "changed_processes": [\n                    {\n                        "before": baseline_by_pid[key],\n                        "after": current_by_pid[key],\n                    }\n                    for key in sorted(set(baseline_by_pid) & set(current_by_pid))\n                    if baseline_by_pid[key] != current_by_pid[key]\n                ][:16],\n                "target_cgroup_changed": (\n                    current_inventory["target_cgroup_paths_sha256"]\n                    != baseline_target_cgroup\n                ),\n                "workload_cgroup_changed": (\n                    current_inventory["workload_cgroup_paths_sha256"]\n                    != baseline_workload_cgroup\n                ),\n                "docker_inventory_changed": (\n                    current_inventory["docker_inventory_sha256"]\n                    != baseline_docker_inventory\n                ),\n            }\n            raise QualificationError(\n                "co-resident process appeared/disappeared/changed: %s"\n                % json.dumps(diagnostics, sort_keys=True, separators=(",", ":"))\n            )\n'''
    if items[core_relative].count(old_stability) != 1:
        raise RuntimeError("shared collector stability patch point differs")
    items[core_relative] = items[core_relative].replace(
        old_stability, new_stability
    )
    old_v1_swap_sample = b'''        memsw_current = _integer(
            _read_required(
                roots["memory"] / "memory.memsw.usage_in_bytes",
                "cgroup memory memsw usage",
            ).strip(),
            "memory memsw usage",
        )
        swap_current = max(0, memsw_current - memory_current)
'''
    new_v1_swap_sample = b'''        # memory.usage_in_bytes and memory.memsw.usage_in_bytes are
        # independently changing counters.  Subtracting two non-atomic reads
        # creates false positive swap usage under allocation churn even when
        # the cgroup has a zero-swap envelope.  cgroup v1 memory.stat exposes
        # the hierarchical swap charge directly and is the fail-closed source
        # for the target container and all of its descendants.
        memory_stat = _key_value_rows(
            _read_required(
                roots["memory"] / "memory.stat",
                "cgroup v1 memory.stat",
            ),
            "cgroup v1 memory.stat",
        )
        swap_current = _integer(
            memory_stat.get("total_swap"),
            "cgroup v1 hierarchical swap",
        )
'''
    if items[core_relative].count(old_v1_swap_sample) != 1:
        raise RuntimeError("shared collector cgroup v1 swap patch point differs")
    items[core_relative] = items[core_relative].replace(
        old_v1_swap_sample, new_v1_swap_sample
    )
    old_client_shape = b'''        or set(client_container) != {
            "name",
            "container_id",
            "image_id",
            "created",
            "inspect_config_sha256",
            "execution_user",
            "required_read_only_mounts",
        }
        or not isinstance(client_container.get("name"), str)
'''
    new_client_shape = b'''        or (
            expected_client_envelope.get("measurement_output_medium")
            == "host_tmpfs_bind"
            and set(client_container) != {
                "name", "container_id", "image_id", "created",
                "inspect_config_sha256", "execution_user",
                "required_read_only_mounts",
            }
        )
        or (
            expected_client_envelope.get("measurement_output_medium")
            == "tmpfs"
            and set(client_container) != {
                "name", "container_id", "image_id", "created",
                "inspect_config_sha256", "required_read_only_mounts",
            }
        )
        or expected_client_envelope.get("measurement_output_medium")
        not in ("host_tmpfs_bind", "tmpfs")
        or not isinstance(client_container.get("name"), str)
'''
    if items[core_relative].count(old_client_shape) != 1:
        raise RuntimeError("shared collector client-shape patch point differs")
    items[core_relative] = items[core_relative].replace(
        old_client_shape, new_client_shape
    )
    old_execution_user = b'''    if (
        expected_client_envelope.get("measurement_output_medium")
        != "host_tmpfs_bind"
        or client_container.get("execution_user")
        != expected_client_envelope.get("container_user")
    ):
        raise QualificationError("TPC-H client execution user differs")
'''
    new_execution_user = b'''    if (
        expected_client_envelope.get("measurement_output_medium")
        == "host_tmpfs_bind"
        and client_container.get("execution_user")
        != expected_client_envelope.get("container_user")
    ):
        raise QualificationError("TPC-H client execution user differs")
'''
    if items[core_relative].count(old_execution_user) != 1:
        raise RuntimeError("shared collector execution-user patch point differs")
    items[core_relative] = items[core_relative].replace(
        old_execution_user, new_execution_user
    )
    old_measurement_shape = b'''    if (
        set(measurement_output) != {
            "host_tmpfs_directory",
            "host_bind_source",
            "container_tmpfs_destination",
        }
        or not all(
            isinstance(measurement_output.get(key), str)
            and Path(measurement_output[key]).is_absolute()
            for key in measurement_output
        )
    ):
        raise QualificationError("measurement tmpfs binding differs")
    host_bind_source = Path(measurement_output["host_bind_source"])
    if (
        host_bind_source.parent != Path(measurement_output["host_tmpfs_directory"])
        or host_bind_source.name != "client_measurement"
    ):
        raise QualificationError("TPC-H command measurement bind differs")
'''
    new_measurement_shape = b'''    measurement_medium = expected_client_envelope.get(
        "measurement_output_medium"
    )
    expected_measurement_keys = (
        {"host_tmpfs_directory", "host_bind_source",
         "container_tmpfs_destination"}
        if measurement_medium == "host_tmpfs_bind"
        else {"host_tmpfs_directory", "container_tmpfs_destination"}
    )
    if (
        set(measurement_output) != expected_measurement_keys
        or not all(
            isinstance(measurement_output.get(key), str)
            and Path(measurement_output[key]).is_absolute()
            for key in measurement_output
        )
    ):
        raise QualificationError("measurement tmpfs binding differs")
    if measurement_medium == "host_tmpfs_bind":
        host_bind_source = Path(measurement_output["host_bind_source"])
        if (
            host_bind_source.parent
            != Path(measurement_output["host_tmpfs_directory"])
            or host_bind_source.name != "client_measurement"
        ):
            raise QualificationError("TPC-H command measurement bind differs")
'''
    if items[core_relative].count(old_measurement_shape) != 1:
        raise RuntimeError("shared collector measurement-shape patch point differs")
    items[core_relative] = items[core_relative].replace(
        old_measurement_shape, new_measurement_shape
    )
    old_client_validator_start = b'''def validate_client_container_readback(
    container_row, expected, binding, tmpfs_destination
):
'''
    new_client_validator_start = b'''def _validate_tpcc_host_tmpfs_bind(
    container_row, expected, destination, expected_source
):
    if expected.get("measurement_output_medium") != "tmpfs":
        return False
    host = container_row.get("HostConfig") or {}
    if host.get("Tmpfs") not in ({}, None):
        return False
    mounts = [
        row for row in (container_row.get("Mounts") or [])
        if row.get("Destination") == destination
    ]
    if len(mounts) != 1:
        raise QualificationError("TPC-C measurement bind cardinality differs")
    mount = mounts[0]
    source = mount.get("Source")
    if (
        mount.get("Type") != "bind"
        or mount.get("RW") is not True
        or not isinstance(source, str)
        or Path(source).resolve() != Path(expected_source).resolve()
    ):
        raise QualificationError("TPC-C measurement bind source differs")
    configured_user = str((container_row.get("Config") or {}).get("User") or "")
    if re.fullmatch(r"[0-9]+:[0-9]+", configured_user) is None:
        raise QualificationError("TPC-C client numeric execution user differs")
    expected_uid, expected_gid = [int(value) for value in configured_user.split(":")]
    state = Path(source).stat()
    if (
        state.st_uid != expected_uid
        or state.st_gid != expected_gid
        or stat.S_IMODE(state.st_mode) != 0o700
    ):
        raise QualificationError("TPC-C measurement bind ownership differs")
    raw = run(
        ["findmnt", "-n", "-o", "FSTYPE,OPTIONS", "--target", source],
        timeout=5,
        label="TPC-C measurement bind tmpfs readback",
    ).strip().split(None, 1)
    if len(raw) != 2 or raw[0] != "tmpfs":
        raise QualificationError("TPC-C measurement bind is not tmpfs")
    sizes = [
        _parse_size_option(value.split("=", 1)[1])
        for value in raw[1].split(",") if value.startswith("size=")
    ]
    if len(sizes) != 1 or sizes[0] < int(expected["tmpfs_bytes"]):
        raise QualificationError("TPC-C measurement bind capacity differs")
    return True


def validate_client_container_readback(
    container_row, expected, binding, tmpfs_destination,
    host_tmpfs_directory, target_major_minor
):
'''
    if items[core_relative].count(old_client_validator_start) != 1:
        raise RuntimeError("shared collector client-validator patch point differs")
    items[core_relative] = items[core_relative].replace(
        old_client_validator_start, new_client_validator_start
    )
    old_hardening = b'''        or (
            expected.get("measurement_output_medium") != "host_tmpfs_bind"
            and _tmpfs_size_bytes(host_config, tmpfs_destination)
            != int(expected["tmpfs_bytes"])
        )
    ):
        raise QualificationError("client container hardening differs")
    raw_client_mounts = container_row.get("Mounts") or []
    if any(mount.get("Type") == "volume" for mount in raw_client_mounts):
        raise QualificationError(
            "client anonymous or named volume is prohibited"
        )
    validate_tpch_secure_measurement_bind(
        container_row, expected, tmpfs_destination
    )
'''
    new_hardening = b'''        or (
            expected.get("measurement_output_medium") != "host_tmpfs_bind"
            and host_config.get("Tmpfs") not in ({}, None)
            and _tmpfs_size_bytes(host_config, tmpfs_destination)
            != int(expected["tmpfs_bytes"])
        )
    ):
        raise QualificationError("client container hardening differs")
    raw_client_mounts = container_row.get("Mounts") or []
    tpcc_host_bind = _validate_tpcc_host_tmpfs_bind(
        container_row, expected, tmpfs_destination, host_tmpfs_directory
    )
    if (
        expected.get("measurement_output_medium") == "tmpfs"
        and not tpcc_host_bind
        and _tmpfs_size_bytes(host_config, tmpfs_destination)
        != int(expected["tmpfs_bytes"])
    ):
        raise QualificationError("TPC-C measurement tmpfs differs")
    validate_tpch_secure_measurement_bind(
        container_row, expected, tmpfs_destination
    )
    allowed_tpcc_volume_sources = set()
    for mount in raw_client_mounts:
        if (
            expected.get("measurement_output_medium") == "tmpfs"
            and expected.get("target_mysql_device_write_prohibited") is True
            and mount.get("Type") == "volume"
            and mount.get("RW") is True
            and mount.get("Destination") == "/var/lib/mysql"
            and isinstance(mount.get("Source"), str)
        ):
            source_state = Path(mount["Source"]).stat()
            source_device = "%d:%d" % (
                os.major(source_state.st_dev), os.minor(source_state.st_dev)
            )
            if source_device != target_major_minor:
                raise QualificationError("TPC-C client volume device differs")
            allowed_tpcc_volume_sources.add(mount["Source"])
        elif mount.get("Type") == "volume":
            raise QualificationError("unexpected client volume is prohibited")
'''
    if items[core_relative].count(old_hardening) != 1:
        raise RuntimeError("shared collector hardening patch point differs")
    items[core_relative] = items[core_relative].replace(
        old_hardening, new_hardening
    )
    old_mount_allowance = b'''            and mount.get("Destination") == "/measurement"
            and expected.get("measurement_output_medium")
            == "host_tmpfs_bind"
        ):
            continue
        if mount.get("Type") in ("bind", "volume") and mount.get("RW") is not False:
            if not _is_docker_managed_mutable_bind(
                mount, container_row.get("Id")
            ):
                raise QualificationError("client container mutable mount differs")
'''
    new_mount_allowance = b'''            and mount.get("Destination") == "/measurement"
            and (
                expected.get("measurement_output_medium") == "host_tmpfs_bind"
                or tpcc_host_bind
            )
        ):
            continue
        if (
            mount.get("Type") == "volume"
            and mount.get("Source") in allowed_tpcc_volume_sources
        ):
            continue
        if mount.get("Type") in ("bind", "volume") and mount.get("RW") is not False:
            if not _is_docker_managed_mutable_bind(
                mount, container_row.get("Id")
            ):
                raise QualificationError("client container mutable mount differs")
'''
    if items[core_relative].count(old_mount_allowance) != 1:
        raise RuntimeError("shared collector mount-allowance patch point differs")
    items[core_relative] = items[core_relative].replace(
        old_mount_allowance, new_mount_allowance
    )
    old_client_call = b'''        spec["client_container"],
        spec["measurement_output"]["container_tmpfs_destination"],
    )
'''
    new_client_call = b'''        spec["client_container"],
        spec["measurement_output"]["container_tmpfs_destination"],
        spec["measurement_output"]["host_tmpfs_directory"],
        own["storage"]["major_minor"],
    )
'''
    if items[core_relative].count(old_client_call) != 1:
        raise RuntimeError("shared collector client-call patch point differs")
    items[core_relative] = items[core_relative].replace(
        old_client_call, new_client_call
    )
    old_benchbase_stop_binding = b'''            "BENCHBASE_FORMAL_STOP_FILE":
                str(
                    Path(
                        measurement_output[
                            "container_tmpfs_destination"
                        ]
                    )
                    / "benchbase-stop-request"
                ),
'''
    new_benchbase_stop_binding = b'''            "BENCHBASE_FORMAL_STOP_FILE":
                str(
                    Path(
                        measurement_output[
                            "container_tmpfs_destination"
                        ]
                    )
                    / (attempt_id + ".benchbase-stop-request")
                ),
'''
    if items[core_relative].count(old_benchbase_stop_binding) != 1:
        raise RuntimeError("shared collector BenchBase stop binding differs")
    items[core_relative] = items[core_relative].replace(
        old_benchbase_stop_binding, new_benchbase_stop_binding
    )
    files = {relative: digest_bytes(payload) for relative, payload in sorted(items.items())}
    entry_relative = "qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py"
    manifest = {
        "schema_version": "mysql_tpch_tpcc_shared_collector_bundle_v1",
        "status": "SEALED_BYTE_IDENTICAL_FOUR_FLOW_COLLECTOR_NOT_LAUNCH_AUTHORITY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "target_bundles": {
            "tpch_memory": {
                "bundle_id": TPCH.name,
                "manifest_sha256": digest(TPCH / "bundle_manifest.json"),
                "schema_version": tpch_manifest["schema_version"],
            },
            "tpcc_cpu": {
                "bundle_id": TPCC.name,
                "manifest_sha256": digest(TPCC / "bundle_manifest.json"),
                "schema_version": tpcc_manifest["schema_version"],
            },
        },
        "entrypoint": entry_relative,
        "entrypoint_sha256": files[entry_relative],
        "byte_identical_core": core_relative,
        "collector_sha256_expected_in_all_four_raw_flows": files[core_relative],
        "supported_scenarios": ["tpch_memory", "tpcc_cpu"],
        "required_raw_flows": ["A_solo", "A_paired", "B_solo", "B_paired"],
        "docker_socket_selected_only_after_target_bundle_verification": True,
        "missing_database_counter_or_timeout_behavior": "FAIL_CLOSED",
        "workload_process_stability_policy": {
            "sealed_workload_root_must_remain_stable": True,
            "authorized_workload_descendant_pid_churn_exempt": True,
            "target_control_plane_and_co_resident_processes_must_remain_stable": True,
        },
        "inventory_failure_diagnostics_fail_closed": True,
        "cgroup_v1_swap_usage_source": "memory.stat.total_swap",
        "cgroup_v1_memsw_minus_memory_non_atomic_subtraction_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": files,
    }
    bundle_id = digest_bytes(canonical(manifest))
    manifest["bundle_id"] = bundle_id
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / bundle_id
    if target.exists():
        if json.loads((target / "bundle_manifest.json").read_text()) != manifest:
            raise RuntimeError("existing shared collector bundle differs")
    else:
        stage = OUTPUT / (".stage-%s-%s" % (bundle_id, secrets.token_hex(6)))
        stage.mkdir(mode=0o700)
        try:
            for relative, payload in items.items():
                path = stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                os.chmod(path, 0o444)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                digest_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
            )
            for relative, expected in files.items():
                if digest(stage / relative) != expected:
                    raise RuntimeError("staged file verification failed: %s" % relative)
            for path in sorted(stage.rglob("*"), reverse=True):
                if path.is_symlink():
                    raise RuntimeError("symlink in staged shared collector")
                os.chmod(path, 0o444 if path.is_file() else 0o555)
            os.chmod(stage, 0o555)
            os.rename(stage, target)
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            raise
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "bundle": str(target),
                "bundle_id": bundle_id,
                "manifest_sha256": digest(target / "bundle_manifest.json"),
                "collector_sha256": files[core_relative],
                "formal_launch_authorized": False,
                "accepted_round_authorized": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
