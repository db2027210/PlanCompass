from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from formal_mysql_engine_gate_20260721 import (
    GateError,
    _canonicalize_postgres_csv,
)


class PostgreSQLCsvCanonicalizationTest(unittest.TestCase):
    def test_backslash_before_delimiter_quotes_and_embedded_newline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "person_info.csv"
            canonical = root / "person_info.canonical.csv"
            source.write_text(
                "2671662,42,7,value\\,\n"
                '2,43,7,"said \\"hello\\" and \\\\ path",note\n'
                '3,44,7,"line one\nline two",tail\n',
                encoding="utf-8",
                newline="",
            )

            audit = _canonicalize_postgres_csv(source, canonical, 5)

            with canonical.open("r", encoding="utf-8", newline="") as handle:
                rows = list(
                    csv.reader(
                        handle,
                        delimiter=",",
                        quotechar='"',
                        escapechar=None,
                        doublequote=True,
                        strict=True,
                    )
                )
            self.assertEqual(
                rows,
                [
                    ["2671662", "42", "7", "value\\", ""],
                    ["2", "43", "7", 'said "hello" and \\ path', "note"],
                    ["3", "44", "7", "line one\nline two", "tail"],
                ],
            )
            self.assertEqual(audit["source_records"], 3)
            self.assertEqual(audit["canonical_records"], 3)
            self.assertFalse(audit["arbitrary_missing_column_padding"])
            self.assertEqual(
                audit["adaptation_counts"],
                {
                    "literal_backslash_before_delimiter_count": 1,
                    "records_with_literal_backslash_before_delimiter": 1,
                    "backslash_quote_escape_count": 2,
                    "backslash_escape_escape_count": 1,
                    "doublequote_escape_count": 0,
                    "embedded_newline_record_count": 1,
                },
            )

    def test_arbitrary_four_column_record_is_rejected_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bad.csv"
            canonical = root / "bad.canonical.csv"
            source.write_text("1,2,3,value\n", encoding="utf-8", newline="")

            with self.assertRaisesRegex(GateError, r"expected=5 actual=4"):
                _canonicalize_postgres_csv(source, canonical, 5)

            self.assertFalse(canonical.exists())


if __name__ == "__main__":
    unittest.main()
