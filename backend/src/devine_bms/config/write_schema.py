"""Write the published JSON Schema for the site config file: python -m devine_bms.config.write_schema"""

import json
from pathlib import Path

from devine_bms.config.model import json_schema

TARGET = Path(__file__).resolve().parents[4] / "config" / "schema" / "devine-bms-config-0.schema.json"

if __name__ == "__main__":
    TARGET.write_text(json.dumps(json_schema(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {TARGET}")
