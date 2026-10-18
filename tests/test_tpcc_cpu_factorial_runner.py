import json
import unittest

from tpcc_cpu_factorial_runner import _last_json


class TPCCCPUFactorialRunnerTests(unittest.TestCase):
    def test_resource_metrics_prefix_is_parsed(self):
        payload = {"cpu": .2, "memory": 3.0, "io": 1.0, "qps": 20.0}
        self.assertEqual(_last_json("noise\nRESOURCE_METRICS=" + json.dumps(payload)), payload)


if __name__ == "__main__":
    unittest.main()
