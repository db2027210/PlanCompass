import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import job_mysql_resume_indexes as resume


class ResumeIndexesTests(unittest.TestCase):
    def test_missing_indexes_group_by_table_and_skip_existing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "indexes.sql"
            path.write_text("create index i1 on t(a);\ncreate index i2 on t(b);\ncreate index i3 on u(c);\n")
            args = argparse.Namespace(indexes=path, database="d")
            with patch("job_mysql_resume_indexes.mysql", return_value="i1\nPRIMARY\n"):
                grouped = resume.build_missing(args)
        self.assertEqual(grouped, {"t": [("i2", "b")], "u": [("i3", "c")]})


if __name__ == "__main__":
    unittest.main()
