"""One scripted discovery run per module; the compiler is pure, so every test can share it."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser

from bankbot.discover import GoalSpec, Transcript
from bankbot.policy import load_policy
from tests.discover.conftest import HAPPY_PATH, ScriptedDecider, lookup_spec, make_discovery


@pytest.fixture(scope="module")
def recorded(
    browser: Browser, base_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[Transcript, GoalSpec]]:
    httpx.delete(f"{base_url}/admin/faults").raise_for_status()
    context = browser.new_context()
    spec = lookup_spec()
    discovery, _ = make_discovery(
        spec,
        {"member_id": "M-100"},
        ScriptedDecider(HAPPY_PATH),
        page=context.new_page(),
        policy=load_policy(),
        base_url=base_url,
        tmp_path=Path(tmp_path_factory.mktemp("compile")),
    )
    yield discovery.run(), spec
    context.close()
