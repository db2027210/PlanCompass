#!/usr/bin/env python3
"""v111 rolling prestart inventory bound to the v28 kernel gate."""

from scripts import mysql_c_external_storage_inventory_only_v4 as prior
from scripts import mysql_cross_project_wave_coordination_v28 as kernel_gate


prior.kernel_gate = kernel_gate
WAIVER_ID = prior.WAIVER_ID
_process_inventory = prior._process_inventory
_docker_inventory = prior._docker_inventory

__all__ = ["_process_inventory", "_docker_inventory", "WAIVER_ID"]

