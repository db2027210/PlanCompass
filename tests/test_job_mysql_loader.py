from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from job_mysql_loader import Endpoint, load_sql, mysql_schema_sql, parse_postgres_schema, prepare


SCHEMA = """CREATE TABLE thing (
 id integer NOT NULL PRIMARY KEY,
 label text NOT NULL,
 code character varying(12),
 parent_id integer
);
"""


class JobLoaderTest(unittest.TestCase):
    def test_schema_conversion_and_load_null_mapping(self):
        tables = parse_postgres_schema(SCHEMA)
        schema = mysql_schema_sql(tables)
        self.assertIn("`id` INT NOT NULL", schema)
        self.assertIn("`label` LONGTEXT NOT NULL", schema)
        self.assertIn("`code` VARCHAR(12) NULL", schema)
        load = load_sql(tables, Path("/tmp/data"))
        self.assertIn("LOAD DATA LOCAL INFILE", load)
        self.assertIn("`code`=NULLIF(@c2, '')", load)
        self.assertIn("`label`=@c1", load)

    def test_prepare_is_dry_run_and_requires_new_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); data = root / "data"; data.mkdir()
            (data / "thing.csv").write_text('1,"hello, world",,\n', encoding="utf-8")
            schema = root / "schema.sql"; schema.write_text(SCHEMA, encoding="utf-8")
            indexes = root / "indexes.sql"; indexes.write_text("CREATE INDEX x ON thing(parent_id);", encoding="utf-8")
            output = root / "plan"
            result = prepare(Endpoint("127.0.0.1", 3307, "imdb", "runner"), output,
                             data_dir=data, schema_path=schema, indexes_path=indexes)
            self.assertEqual(result["mode"], "dry_run_only_no_database_connection")
            self.assertEqual(result["table_count"], 1)
            self.assertTrue((output / "02_load.sql").is_file())
            with self.assertRaises(FileExistsError):
                prepare(Endpoint("127.0.0.1", 3307, "imdb", "runner"), output,
                        data_dir=data, schema_path=schema, indexes_path=indexes)

    def test_endpoint_validation(self):
        with self.assertRaises(ValueError):
            Endpoint("", 3306, "imdb", "u")
        with self.assertRaises(ValueError):
            Endpoint("host", 3306, "imdb;drop", "u")


if __name__ == "__main__":
    unittest.main()
