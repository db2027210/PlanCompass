import unittest

from mysql_random_cost_preflight import latin_hypercube_multipliers, plan_fingerprints


class RandomCostPreflightTest(unittest.TestCase):
    def test_latin_hypercube_is_replayable_and_bounded(self):
        names = ["a", "b"]
        first = latin_hypercube_multipliers(names, 5, 17, 0.25, 4.0)
        second = latin_hypercube_multipliers(names, 5, 17, 0.25, 4.0)
        self.assertEqual(first, second)
        self.assertTrue(all(0.25 <= row[name] <= 4.0 for row in first for name in names))
        self.assertEqual(len({round(row["a"], 12) for row in first}), 5)

    def test_plan_fingerprint_ignores_cost_info(self):
        one = '{"query_block":{"table":{"access_type":"ALL","cost_info":{"read_cost":"1"}}}}'
        two = '{"query_block":{"table":{"access_type":"ALL","cost_info":{"read_cost":"999"}}}}'
        three = '{"query_block":{"table":{"access_type":"range","cost_info":{"read_cost":"1"}}}}'
        self.assertEqual(plan_fingerprints([one]), plan_fingerprints([two]))
        self.assertNotEqual(plan_fingerprints([one]), plan_fingerprints([three]))


if __name__ == "__main__":
    unittest.main()
