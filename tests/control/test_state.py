import threading
import time
from pathlib import Path

import pytest
from playwright.sync_api import Page

from bankbot.control import ControlState, IllegalTransition, RunController
from bankbot.control.state import HEARTBEAT_MISSES, OPERATOR_LOST
from bankbot.evidence import read_events
from bankbot.schemas import InterventionDecision
from tests.control.conftest import a_request, current, wait_until

FAST_HEARTBEAT_S = 0.05


def test_a_fresh_controller_belongs_to_the_automation(controller: RunController) -> None:
    assert current(controller) is ControlState.AUTOMATION


def test_take_control_is_only_legal_while_the_engine_is_asking(controller: RunController) -> None:
    with pytest.raises(IllegalTransition):
        controller.take_control()


def test_hand_back_and_mark_done_need_a_person_in_control(controller: RunController) -> None:
    with pytest.raises(IllegalTransition):
        controller.hand_back()
    with pytest.raises(IllegalTransition):
        controller.mark_step_done()


def test_abort_is_final_and_cannot_be_aborted_again(controller: RunController) -> None:
    controller.abort()
    assert current(controller) is ControlState.ABORTED
    with pytest.raises(IllegalTransition):
        controller.abort()
    controller.finish("failure")
    assert current(controller) is ControlState.ABORTED, "finish does not paper over an abort"


def test_request_blocks_until_a_person_takes_hands_back_and_returns_resume(
    controller: RunController, page: Page, base_url: str
) -> None:
    page.goto(f"{base_url}/login")
    seen: list[ControlState] = []

    def operator() -> None:
        # The person: take control once the engine asks, then hand back.
        wait_until(controller, ControlState.INTERVENTION_REQUESTED)
        seen.append(current(controller))
        controller.take_control()
        seen.append(current(controller))
        controller.hand_back()
        seen.append(current(controller))

    thread = threading.Thread(target=operator)
    thread.start()
    decision = controller.request(a_request(controller.run_id))
    thread.join()
    assert decision is InterventionDecision.RESUME
    assert seen == [
        ControlState.INTERVENTION_REQUESTED,
        ControlState.HUMAN,
        ControlState.RESUME_REQUESTED,
    ]
    assert current(controller) is ControlState.AUTOMATION
    assert controller.request_pending is None
    assert controller.run_dir.screenshot_path("live").exists(), "the live view was refreshed"
    states = [
        event["state"] for event in read_events(controller.run_dir) if event["event"] == "control"
    ]
    assert states == ["intervention_requested", "human", "automation"]


def test_mark_step_done_returns_step_done_and_abort_returns_abort(
    controller: RunController, page: Page, base_url: str
) -> None:
    page.goto(f"{base_url}/login")

    def operator(final: str) -> None:
        wait_until(controller, ControlState.INTERVENTION_REQUESTED)
        controller.take_control()
        if final == "done":
            controller.mark_step_done()
        else:
            controller.abort()

    thread = threading.Thread(target=operator, args=("done",))
    thread.start()
    assert controller.request(a_request(controller.run_id)) is InterventionDecision.STEP_DONE
    thread.join()
    thread = threading.Thread(target=operator, args=("abort",))
    thread.start()
    assert controller.request(a_request(controller.run_id)) is InterventionDecision.ABORT
    thread.join()
    assert current(controller) is ControlState.ABORTED


def test_what_a_person_does_while_in_control_is_recorded_with_values_masked(
    controller: RunController, page: Page, base_url: str, tmp_path: Path
) -> None:
    page.goto(f"{base_url}/login")

    def human_in_the_loop(ms: int) -> None:
        # Plays the person from the engine thread: Playwright's sync API is thread-bound.
        if current(controller) is ControlState.HUMAN and not controller.human_actions:
            page.get_by_label("Username").fill("teller")
            page.get_by_label("Password").fill("teller-demo-password")
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_url("**/members/search")
            controller.hand_back()
        page.wait_for_timeout(ms)

    controller._pump = human_in_the_loop

    def operator() -> None:
        wait_until(controller, ControlState.INTERVENTION_REQUESTED)
        controller.take_control()

    thread = threading.Thread(target=operator)
    thread.start()
    controller.request(a_request(controller.run_id))
    thread.join()
    kinds = [action.kind for action in controller.human_actions]
    assert "input" in kinds and "click" in kinds and "navigate" in kinds
    descriptions = " | ".join(action.description for action in controller.human_actions)
    assert "Sign in" in descriptions
    log = controller.run_dir.log_path.read_text()
    assert "teller-demo-password" not in log
    assert "human_action" in log


def test_a_person_whose_page_stops_pinging_loses_the_run_as_operator_lost(
    controller: RunController, page: Page, base_url: str
) -> None:
    page.goto(f"{base_url}/login")
    controller.heartbeat_every_s = FAST_HEARTBEAT_S

    def operator() -> None:
        wait_until(controller, ControlState.INTERVENTION_REQUESTED)
        controller.take_control()
        # ... and then walks away: no heartbeat ever arrives.

    thread = threading.Thread(target=operator)
    thread.start()
    started = time.monotonic()
    decision = controller.request(a_request(controller.run_id))
    thread.join()
    assert decision is InterventionDecision.ABORT
    assert current(controller) is ControlState.ABORTED
    assert controller.abort_reason == OPERATOR_LOST
    assert time.monotonic() - started >= FAST_HEARTBEAT_S * HEARTBEAT_MISSES
    aborted = [
        event for event in read_events(controller.run_dir) if event.get("state") == "aborted"
    ]
    assert aborted and aborted[0]["reason"] == OPERATOR_LOST


def test_heartbeats_keep_a_person_in_control_past_the_lease(
    controller: RunController, page: Page, base_url: str
) -> None:
    page.goto(f"{base_url}/login")
    controller.heartbeat_every_s = FAST_HEARTBEAT_S

    def operator() -> None:
        wait_until(controller, ControlState.INTERVENTION_REQUESTED)
        controller.take_control()
        # Ping at twice the rate the page would, for long enough that a silent
        # operator would have been dropped several times over.
        for _ in range(HEARTBEAT_MISSES * 8):
            time.sleep(FAST_HEARTBEAT_S / 2)
            controller.heartbeat()
        controller.hand_back()

    thread = threading.Thread(target=operator)
    thread.start()
    decision = controller.request(a_request(controller.run_id))
    thread.join()
    assert decision is InterventionDecision.RESUME
    assert controller.abort_reason is None


def test_a_heartbeat_outside_human_control_is_ignored(controller: RunController) -> None:
    controller.heartbeat()
    assert current(controller) is ControlState.AUTOMATION
