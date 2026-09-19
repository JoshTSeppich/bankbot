"""Render the Capability contract as JSON Schema for reviewers and other tools.

Owns: the one canonical rendering of the schema and the path it is committed
at. I commit the file because it is the contract a reviewer reads; a test
fails when it drifts from the models so it cannot go stale silently.

Does not own: the models themselves (schemas/artifact.py).

Governed by ADR-0001 (artifact schema).
"""

import json
from pathlib import Path

from bankbot.schemas.artifact import Capability

REPO_ROOT = Path(__file__).resolve().parents[2]
CAPABILITY_SCHEMA_PATH = REPO_ROOT / "docs" / "schema" / "capability.schema.json"


def render_capability_schema() -> str:
    """Produce byte-identical output on every run so the committed file is diffable."""
    return json.dumps(Capability.model_json_schema(), indent=2) + "\n"
