from bankbot.schemas.export import CAPABILITY_SCHEMA_PATH, render_capability_schema


def test_committed_capability_json_schema_matches_the_models() -> None:
    assert CAPABILITY_SCHEMA_PATH.exists(), "run: uv run python -m bankbot.schemas"
    assert CAPABILITY_SCHEMA_PATH.read_text() == render_capability_schema(), (
        "docs/schema/capability.schema.json is stale; run: uv run python -m bankbot.schemas"
    )
