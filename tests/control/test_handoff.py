"""End to end: replay pauses on the unknown dialog, a person dismisses it, replay finishes."""

import threading
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from playwright.sync_api import Page

from bankbot.control import ControlState, RunController, RunRegistry, create_operator_app
from bankbot.evidence import EvidenceWriter, RunDir, new_run_id, read_events
from bankbot.policy import Policy
from bankbot.replay import Replay
from bankbot.schemas import Capability, Success
from bankbot.surface import PlaywrightSurface
from tests.control.conftest import current, wait_until
from tests.replay.conftest import TEST_SECRETS, arm_faults


def test_a_person_can_take_over_dismiss_the_dialog_and_hand_back_to_a_successful_run(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    arm_faults(base_url, unknown_dialog_at_step=3)
    capability = Capability.model_validate(capability_json)
    params = {"member_id": "M-100"}
    run_dir = RunDir.create(tmp_path / "runs", new_run_id())
    writer = EvidenceWriter(run_dir, policy.redactor(extra_values=params.values()))
    surface = PlaywrightSurface(page, policy.mask_selectors)
    person_should_click = threading.Event()

    def pump(ms: int) -> None:
        # The person's click has to happen on this thread; the operator thread only asks for it.
        if person_should_click.is_set():
            person_should_click.clear()
            page.get_by_role("button", name="OK").click()
            page.wait_for_timeout(300)
            controller.hand_back()
        surface.idle(ms)

    controller = RunController(
        run_dir, capability, sorted(params), surface=surface, writer=writer, pump=pump
    )
    registry = RunRegistry()
    registry.add(controller)

    def operator() -> None:
        with TestClient(create_operator_app(registry)) as client:
            wait_until(controller, ControlState.INTERVENTION_REQUESTED)
            assert "Take control" in client.get(f"/operator/{controller.run_id}").text
            client.post(f"/operator/{controller.run_id}/take", follow_redirects=False)
            person_should_click.set()
            wait_until(controller, ControlState.AUTOMATION)

    thread = threading.Thread(target=operator)
    thread.start()
    result = Replay(
        capability,
        params,
        surface=surface,
        policy=policy,
        run_dir=run_dir,
        writer=writer,
        base_url=base_url,
        secrets=TEST_SECRETS,
        escalation=controller,
        step_timeout_ms=1500,
    ).run()
    thread.join()
    controller.finish(result.kind)

    assert isinstance(result, Success)
    assert result.outputs == {"savings_balance": "4242.00"}
    events = [event["event"] for event in read_events(run_dir)]
    assert "intervention_requested" in events
    assert "human_action" in events
    assert events.index("human_action") < events.index("intervention_answered")
    clicked = [
        e for e in read_events(run_dir) if e["event"] == "human_action" and e["kind"] == "click"
    ]
    assert any("OK" in str(e["description"]) for e in clicked)
    assert current(controller) is ControlState.FINISHED
