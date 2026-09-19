"""Entry point: ``python -m bankbot.schemas`` rewrites docs/schema/capability.schema.json."""

from bankbot.schemas.export import CAPABILITY_SCHEMA_PATH, render_capability_schema

CAPABILITY_SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
CAPABILITY_SCHEMA_PATH.write_text(render_capability_schema())
print(f"wrote {CAPABILITY_SCHEMA_PATH}")
