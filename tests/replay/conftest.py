"""Fixtures: a Replay wired to the shared demo app and browser from tests/conftest.py."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.sync_api import Page

from bankbot.evidence import EvidenceWriter, RunDir, new_run_id
from bankbot.policy import Policy, load_policy
from bankbot.replay import Replay
from bankbot.replay.escalation import Escalation
from bankbot.schemas import Capability
from bankbot.surface import PlaywrightSurface

FIXTURE = Path(__file__).parent.parent / "fixtures" / "lookup_savings_balance.json"
TEST_SECRETS = {"BANKBOT_USERNAME": "teller", "BANKBOT_PASSWORD": "teller-demo-password"}


@pytest.fixture
def policy() -> Policy:
    return load_policy()


@pytest.fixture
def capability_json() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text())
    return data


def arm_faults(base_url: str, **faults: int) -> None:
    httpx.post(f"{base_url}/admin/faults", json=faults).raise_for_status()


def make_replay(
    capability: Capability,
    params: dict[str, str],
    *,
    page: Page,
    policy: Policy,
    base_url: str,
    tmp_path: Path,
    escalation: Escalation | None = None,
    secrets: dict[str, str] | None = None,
    step_timeout_ms: int = 5000,
) -> tuple[Replay, RunDir]:
    run_dir = RunDir.create(tmp_path / "runs", new_run_id())
    writer = EvidenceWriter(run_dir, policy.redactor(extra_values=params.values()))
    replay = Replay(
        capability,
        params,
        surface=PlaywrightSurface(page, policy.mask_selectors),
        policy=policy,
        run_dir=run_dir,
        writer=writer,
        base_url=base_url,
        secrets=TEST_SECRETS if secrets is None else secrets,
        escalation=escalation,
        step_timeout_ms=step_timeout_ms,
    )
    return replay, run_dir
