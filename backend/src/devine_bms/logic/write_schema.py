"""Write the published JSON Schema for the logic format: python -m devine_bms.logic.write_schema"""

import json
from pathlib import Path

from devine_bms.logic.model import json_schema

TARGET = Path(__file__).resolve().parents[4] / "config" / "schema" / "devine-bms-logic-1.schema.json"

if __name__ == "__main__":
    TARGET.write_text(json.dumps(json_schema(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {TARGET}")
