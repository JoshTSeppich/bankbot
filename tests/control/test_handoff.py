"""End to end: replay pauses on the unknown dialog and a person answers it.

Two answers: dismiss the dialog and hand back, or close the window and end
the run as session_lost.
"""

import threading
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from playwright.sync_api import Page

from bankbot.control import ControlState, RunController, RunRegistry, create_operator_app
from bankbot.control.state import SESSION_LOST
from bankbot.evidence import EvidenceWriter, RunDir, new_run_id, read_events
from bankbot.policy import Policy
from bankbot.replay import Replay
from bankbot.schemas import Capability, Failure, Success
from bankbot.surface import PlaywrightSurface
from tests.control.conftest import current, wait_until
from tests.replay.conftest import TEST_SECRETS, arm_faults


def test_a_person_can_take_over_dismiss_the_dialog_and_hand_back_to_a_successful_run(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    arm_faults(base_url, unknown_dialog_at_step=2)
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


def test_closing_the_page_while_a_person_holds_control_ends_the_run_as_session_lost(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    arm_faults(base_url, unknown_dialog_at_step=2)
    capability = Capability.model_validate(capability_json)
    params = {"member_id": "M-100"}
    run_dir = RunDir.create(tmp_path / "runs", new_run_id())
    writer = EvidenceWriter(run_dir, policy.redactor(extra_values=params.values()))
    surface = PlaywrightSurface(page, policy.mask_selectors)
    person_should_close = threading.Event()

    def pump(ms: int) -> None:
        # The person closes the window instead of helping; it has to happen on this thread.
        if person_should_close.is_set():
            person_should_close.clear()
            page.close()
        surface.idle(ms)

    controller = RunController(
        run_dir, capability, sorted(params), surface=surface, writer=writer, pump=pump
    )
    registry = RunRegistry()
    registry.add(controller)

    def operator() -> None:
        with TestClient(create_operator_app(registry)) as client:
            wait_until(controller, ControlState.INTERVENTION_REQUESTED)
            client.post(f"/operator/{controller.run_id}/take", follow_redirects=False)
            person_should_close.set()
            wait_until(controller, ControlState.ABORTED)

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

    assert isinstance(result, Failure)
    assert result.step_id == "submit_search"
    assert result.observed.startswith("session_lost")
    assert result.intervention is not None, "the ask a person never answered"
    assert result.intervention.step_id == "submit_search"
    assert result.evidence.screenshot is None
    assert controller.abort_reason == SESSION_LOST
    events = [event["event"] for event in read_events(run_dir)]
    assert events[-1] == "run_finished"


def test_a_person_can_reach_the_guarded_record_around_the_confirm_and_mark_the_step_done(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    """A limit, not a feature: the guard is an onclick and the href beside it is open.

    ADR-0004 says the listener answers for the operator too, so nobody can
    say yes to a confirm. What nobody can do through the guarded control, a
    person can still do around it: the address bar never sees the onclick.
    The step's wait_for then holds and the run reports Success, with the
    vendor's question dismissed and the guard never satisfied. This is the
    argument for the single-use accept in ADR-0004.
    """
    arm_faults(base_url, native_confirm_at_step=3)
    capability = Capability.model_validate(capability_json)
    params = {"member_id": "M-100"}
    run_dir = RunDir.create(tmp_path / "runs", new_run_id())
    writer = EvidenceWriter(run_dir, policy.redactor(extra_values=params.values()))
    surface = PlaywrightSurface(page, policy.mask_selectors)
    person_should_act = threading.Event()

    def pump(ms: int) -> None:
        if person_should_act.is_set():
            person_should_act.clear()
            # Not the guarded link. The address bar, which the onclick never sees.
            page.goto(f"{base_url}/members/M-100")
            page.wait_for_timeout(300)
            controller.mark_step_done()
        surface.idle(ms)

    controller = RunController(
        run_dir, capability, sorted(params), surface=surface, writer=writer, pump=pump
    )
    registry = RunRegistry()
    registry.add(controller)

    def operator() -> None:
        with TestClient(create_operator_app(registry)) as client:
            wait_until(controller, ControlState.INTERVENTION_REQUESTED)
            client.post(f"/operator/{controller.run_id}/take", follow_redirects=False)
            person_should_act.set()
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

    assert isinstance(result, Success), result
    assert result.outputs == {"savings_balance": "4242.00"}
    dialogs = [e for e in read_events(run_dir) if e["event"] == "native_dialog"]
    assert dialogs and dialogs[0]["answer"] == "dismissed", "nobody ever said yes"
