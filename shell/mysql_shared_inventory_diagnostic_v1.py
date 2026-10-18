#!/usr/bin/env python3
"""Run one read-only shared rolling-inventory sample for diagnosis."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--database-container", required=True)
    parser.add_argument("--client-container", required=True)
    parser.add_argument("--major-minor", required=True)
    parser.add_argument("--mysql-cpuset", required=True)
    parser.add_argument("--client-cpuset", required=True)
    parser.add_argument("--client-mems", required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("shared_inventory_diag", str(args.entry))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._verify_target_bundle(args.bundle)
    database = module.inspect_container(args.database_container)
    client = module.inspect_container(args.client_container)
    containers, root_evidence = module.core._docker_container_inventory()
    value = module.core.rolling_process_inventory(
        int(database["State"]["Pid"]),
        int(client["State"]["Pid"]),
        os.getpid(),
        args.major_minor,
        args.mysql_cpuset,
        args.client_cpuset,
        args.client_mems,
        containers,
        root_evidence,
    )
    mysql_cpus = module.core.expand_cpuset(args.mysql_cpuset)
    client_cpus = module.core.expand_cpuset(args.client_cpuset)
    value["dangerous_co_resident"] = [
        row
        for row in value["processes"]
        if row.get("role") == "co_resident"
        and (
            not module.core.expand_cpuset(
                row.get("cpus_allowed_list", "")
            ).isdisjoint(mysql_cpus | client_cpus)
            or int(row.get("declared_device_writable_fds") or 0) > 0
        )
    ]
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
