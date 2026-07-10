"""
export_schema.py — Export Pydantic models as JSON Schema files.
"""
from __future__ import annotations

import json
from pathlib import Path

from shanghai_policy_crawler.models import ProjectItem, PolicyItem


def export() -> None:
    output_dir = Path(__file__).resolve().parents[2] / "schemas"
    output_dir.mkdir(exist_ok=True)

    # Export ProjectItem
    project_schema = ProjectItem.model_json_schema()
    (output_dir / "project.json").write_text(
        json.dumps(project_schema, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # Export PolicyItem
    policy_schema = PolicyItem.model_json_schema()
    (output_dir / "policy.json").write_text(
        json.dumps(policy_schema, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print("✓ JSON Schemas exported to schemas/")


if __name__ == "__main__":
    export()
