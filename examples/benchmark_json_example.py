"""Protocol example only; replace with a real benchmark or mysql_resource_monitor.py."""

import json
import os


params = json.loads(os.environ.get("MYSQL_COST_PARAMS_JSON", "{}"))
io_cost = float(params.get("io_block_read_cost", 1.0))
row_cost = float(params.get("row_evaluate_cost", 0.1))
resource = {
    "cpu": max(0.1, 15.0 - row_cost * 2.0),
    "memory": 3.0,
    "io": max(0.1, 150.0 - io_cost * 10.0),
}
print("RESOURCE_METRICS=" + json.dumps(resource, sort_keys=True), flush=True)
