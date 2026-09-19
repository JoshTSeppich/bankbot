"""Fixtures: a controller over a real page, and the operator app in a test client."""

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import Page

from bankbot.control import ControlState, RunController, RunRegistry, create_operator_app
from bankbot.evidence import EvidenceWriter, RunDir, new_run_id
from bankbot.policy import Policy
from bankbot.schemas import Capability, InterventionReason, InterventionRequest
from bankbot.surface import PlaywrightSurface


def current(controller: RunController) -> ControlState:
    """Read the state through a call, so mypy does not narrow a value another thread changes."""
    return controller.state


def wait_until(controller: RunController, state: ControlState) -> None:
    while controller.state is not state:
        time.sleep(0.01)


def a_request(
    run_id: str, reason: InterventionReason = InterventionReason.UNKNOWN_DIALOG
) -> InterventionRequest:
    return InterventionRequest(
        run_id=run_id,
        capability_id="lookup_savings_balance",
        capability_version="1.0.0",
        goal="Look up a member's savings balance",
        step_id="submit_search",
        step_index=2,
        step_count=5,
        expected="click on 'Search' to complete",
        observed="a dialog: 'System notice'",
        reason=reason,
        screenshot="screenshots/intervention_submit_search.png",
        log_tail=["event=step_failed step_id=submit_search"],
        param_names=["member_id"],
    )


@pytest.fixture
def controller(
    page: Page, policy: Policy, tmp_path: Path, capability_json: dict[str, Any]
) -> RunController:
    run_dir = RunDir.create(tmp_path / "runs", new_run_id())
    writer = EvidenceWriter(run_dir, policy.redactor())
    surface = PlaywrightSurface(page, policy.mask_selectors)
    return RunController(
        run_dir,
        Capability.model_validate(capability_json),
        ["member_id"],
        surface=surface,
        writer=writer,
    )


@pytest.fixture
def registry(controller: RunController) -> RunRegistry:
    registry = RunRegistry()
    registry.add(controller)
    return registry


@pytest.fixture
def client(registry: RunRegistry, tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_operator_app(registry, tmp_path / "runs")) as client:
        yield client
