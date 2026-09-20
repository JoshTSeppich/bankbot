import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient
from playwright.sync_api import Page

from bankbot.control import ControlState, RunController, RunRegistry, create_operator_app
from bankbot.evidence import RunDir
from bankbot.schemas import InterventionDecision
from tests.control.conftest import a_request, current, wait_until


def asking(controller: RunController, page: Page, base_url: str) -> threading.Thread:
    """Put the controller into INTERVENTION_REQUESTED from the engine's point of view."""
    page.goto(f"{base_url}/login")
    results: list[InterventionDecision] = []

    def engine() -> None:
        # A pump that does not touch the browser, so this thread may block without Playwright.
        controller._pump = lambda ms: time.sleep(ms / 1000)
        controller._surface = _NoScreenshots()  # type: ignore[assignment]
        results.append(controller.request(a_request(controller.run_id)))

    thread = threading.Thread(target=engine)
    thread.start()
    wait_until(controller, ControlState.INTERVENTION_REQUESTED)
    return thread


class _NoScreenshots:
    def observe(self, screenshot_to: object = None) -> None:
        return None

    def watch_human(self, on_action: object) -> None:
        return None

    def unwatch_human(self) -> None:
        return None


def test_queue_lists_a_waiting_run_with_its_reason_and_step(
    client: TestClient, controller: RunController, page: Page, base_url: str
) -> None:
    thread = asking(controller, page, base_url)
    html = client.get("/operator").text
    assert "One run is waiting for a person" in html
    assert controller.run_id in html
    assert "Step 3 of 5" in html
    assert "A dialog the automation has never seen is covering the page." in html
    controller.abort()
    thread.join()


def test_detail_shows_expected_observed_and_only_param_names(
    client: TestClient, controller: RunController, page: Page, base_url: str
) -> None:
    thread = asking(controller, page, base_url)
    html = client.get(f"/operator/{controller.run_id}").text
    assert "Waiting for a person" in html
    assert (
        "click on &#39;Search&#39; to complete" in html or "click on 'Search' to complete" in html
    )
    assert "System notice" in html
    assert "member_id" in html and "M-100" not in html
    assert "BANKBOT_PASSWORD" in html and "teller-demo-password" not in html
    assert "Take control" in html
    controller.abort()
    thread.join()


def test_buttons_walk_the_state_machine_and_stale_buttons_are_harmless(
    client: TestClient, controller: RunController, page: Page, base_url: str
) -> None:
    thread = asking(controller, page, base_url)
    assert (
        client.post(f"/operator/{controller.run_id}/hand-back", follow_redirects=False).status_code
        == 303
    )
    assert current(controller) is ControlState.INTERVENTION_REQUESTED, (
        "hand back before take is ignored"
    )
    client.post(f"/operator/{controller.run_id}/take", follow_redirects=False)
    assert current(controller) is ControlState.HUMAN
    html = client.get(f"/operator/{controller.run_id}").text
    assert "You are driving this session" in html
    assert "Mark submit_search complete and resume" in html
    assert f"/operator/{controller.run_id}/heartbeat" in html, "the page pings while HUMAN"
    assert client.post(f"/operator/{controller.run_id}/heartbeat").status_code == 204
    client.post(f"/operator/{controller.run_id}/complete", follow_redirects=False)
    thread.join()
    assert current(controller) is ControlState.AUTOMATION


def test_unknown_run_is_a_404_and_files_outside_the_run_are_refused(
    client: TestClient, controller: RunController
) -> None:
    assert client.get("/operator/nope").status_code == 404
    assert client.get(f"/operator/{controller.run_id}/files/../../policy.yaml").status_code == 404
    assert client.get(f"/operator/{controller.run_id}/live.png").status_code == 404


FINISHED_RUN = "20260919T080000-abcd"


def finished_run_client(runs_dir: Path) -> TestClient:
    """The operator app over a run that only exists on disk: no controller, no registry entry."""
    run = RunDir.create(root=runs_dir, run_id=FINISHED_RUN)
    run.screenshot_path("final").write_bytes(b"\x89PNG\r\n\x1a\n")
    return TestClient(create_operator_app(RunRegistry(), runs_dir))


def test_a_finished_run_page_serves_its_screenshots(tmp_path: Path) -> None:
    with finished_run_client(tmp_path / "runs") as client:
        response = client.get(f"/operator/{FINISHED_RUN}/files/screenshots/final.png")
    assert response.status_code == 200


def test_a_nested_file_path_cannot_leave_the_run_directory(tmp_path: Path) -> None:
    # The dots are percent-encoded because httpx collapses a literal ../ before
    # it sends, and then the guard under test never sees the path.
    escape = f"/operator/{FINISHED_RUN}/files/screenshots/%2e%2e/%2e%2e/%2e%2e/policy.yaml"
    with finished_run_client(tmp_path / "runs") as client:
        response = client.get(escape)
    assert response.status_code == 404
