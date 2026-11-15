from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema.validators import validator_for


SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"


class JsonSchemaContractTests(unittest.TestCase):
    def test_every_repository_schema_passes_its_declared_meta_schema(self):
        schema_paths = sorted(SCHEMA_DIR.glob("*.schema.json"))
        self.assertTrue(schema_paths, "No JSON Schema files were found.")

        for schema_path in schema_paths:
            with self.subTest(schema=schema_path.name):
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                validator_type = validator_for(schema)
                validator_type.check_schema(schema)


if __name__ == "__main__":
    unittest.main()
