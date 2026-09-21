import time

import pytest
from fastapi.testclient import TestClient

from bankbot.target import Faults, create_app
from tests.target.conftest import sign_in

DIALOG_OPEN = '<div role="dialog" aria-modal="true" aria-label="System notice"'
# The en dash is deliberate: the page text uses it, and the test must match the page.
DIALOG_TEXT = "Scheduled maintenance tonight 02:00\u201303:00"
NATIVE_ALERT = '<script>alert("Your session will expire in 2 minutes.");</script>'
NATIVE_CONFIRM = "onclick=\"return confirm('Restricted member. Continue?')\""


def test_admin_endpoint_reports_replaces_and_resets_the_faults(signed_in: TestClient) -> None:
    assert signed_in.get("/admin/faults").json() == Faults().model_dump()

    armed = Faults(session_expiry_at_step=2, unknown_dialog_at_step=3, slow_load_ms=5)
    assert signed_in.post("/admin/faults", json=armed.model_dump()).json() == armed.model_dump()
    assert signed_in.get("/admin/faults").json() == armed.model_dump()

    assert signed_in.delete("/admin/faults").json() == Faults().model_dump()
    assert signed_in.get("/admin/faults").json() == Faults().model_dump()


def test_admin_endpoint_rejects_unknown_fault_names(signed_in: TestClient) -> None:
    response = signed_in.post("/admin/faults", json={"session_expiry_at_stp": 1})
    assert response.status_code == 422


def test_session_expiry_fault_clears_the_session_on_the_nth_counted_request_once(
    signed_in: TestClient,
) -> None:
    signed_in.post("/admin/faults", json={"session_expiry_at_step": 2})

    assert signed_in.get("/members/search").status_code == 200  # counted request 1
    expired = signed_in.get("/members/M-100")  # counted request 2
    assert expired.status_code == 303
    assert expired.headers["location"] == "/login"

    sign_in(signed_in)
    assert signed_in.get("/members/M-100").status_code == 200  # counted request 3
    assert signed_in.get("/members/M-101").status_code == 200  # counted request 4


def test_unknown_dialog_fault_injects_a_modal_on_the_nth_response_once(
    signed_in: TestClient,
) -> None:
    signed_in.post("/admin/faults", json={"unknown_dialog_at_step": 2})

    first = signed_in.get("/members/search").text
    assert DIALOG_OPEN not in first

    second = signed_in.post("/members/results", data={"member_id": "M-100"}).text
    assert DIALOG_OPEN in second
    assert DIALOG_TEXT in second
    assert "onclick=\"this.closest('[role=dialog]').remove()\">OK</button>" in second
    assert "position: fixed; inset: 0;" in second

    third = signed_in.get("/members/M-100").text
    assert DIALOG_OPEN not in third


def test_login_admin_and_the_search_form_iframe_requests_are_not_counted(
    signed_in: TestClient,
) -> None:
    signed_in.post("/admin/faults", json={"unknown_dialog_at_step": 1})

    signed_in.get("/login")
    signed_in.get("/admin/faults")
    signed_in.get("/members/search-form")
    signed_in.get("/")

    assert DIALOG_OPEN in signed_in.get("/members/search").text


def test_setting_faults_restarts_the_request_count(signed_in: TestClient) -> None:
    signed_in.post("/admin/faults", json={"unknown_dialog_at_step": 2})
    signed_in.get("/members/search")  # counted request 1 under the old count

    signed_in.post("/admin/faults", json={"unknown_dialog_at_step": 2})
    assert DIALOG_OPEN not in signed_in.get("/members/search").text  # counted request 1 again
    assert DIALOG_OPEN in signed_in.get("/members/M-100").text  # counted request 2


def test_requests_under_both_variants_share_one_count(signed_in: TestClient) -> None:
    signed_in.post("/admin/faults", json={"unknown_dialog_at_step": 2})
    signed_in.get("/members/search")
    assert DIALOG_OPEN in signed_in.get("/b/members/search").text


def test_slow_load_fault_delays_every_page(signed_in: TestClient) -> None:
    signed_in.post("/admin/faults", json={"slow_load_ms": 150})
    started = time.perf_counter()
    signed_in.get("/login")
    assert time.perf_counter() - started >= 0.15


def test_faults_are_read_from_the_environment_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BANKBOT_SESSION_EXPIRY_AT_STEP", "4")
    monkeypatch.setenv("BANKBOT_UNKNOWN_DIALOG_AT_STEP", "")
    monkeypatch.setenv("BANKBOT_NATIVE_CONFIRM_AT_STEP", "2")
    monkeypatch.setenv("BANKBOT_SLOW_LOAD_MS", "25")
    with TestClient(create_app()) as client:
        assert client.get("/admin/faults").json() == {
            "session_expiry_at_step": 4,
            "unknown_dialog_at_step": None,
            "native_alert_at_step": None,
            "native_confirm_at_step": 2,
            "slow_load_ms": 25,
        }


def test_faults_default_to_nothing_armed_when_the_environment_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in [
        "BANKBOT_SESSION_EXPIRY_AT_STEP",
        "BANKBOT_UNKNOWN_DIALOG_AT_STEP",
        "BANKBOT_SLOW_LOAD_MS",
    ]:
        monkeypatch.delenv(name, raising=False)
    assert Faults.from_environment() == Faults()


def test_native_alert_fault_puts_an_alert_script_on_the_nth_response_once(
    signed_in: TestClient,
) -> None:
    signed_in.post("/admin/faults", json={"native_alert_at_step": 2})

    assert NATIVE_ALERT not in signed_in.get("/members/search").text
    assert NATIVE_ALERT in signed_in.get("/members/M-100").text
    assert NATIVE_ALERT not in signed_in.get("/members/M-101").text


def test_native_confirm_fault_guards_the_results_row_link_on_the_nth_response_once(
    signed_in: TestClient,
) -> None:
    signed_in.post("/admin/faults", json={"native_confirm_at_step": 2})

    assert NATIVE_CONFIRM not in signed_in.get("/members/search").text
    guarded = signed_in.post("/members/results", data={"member_id": "M-100"}).text
    assert NATIVE_CONFIRM in guarded
    assert '<a href="/members/M-100"' in guarded
    assert (
        NATIVE_CONFIRM not in signed_in.post("/members/results", data={"member_id": "M-100"}).text
    )


def test_the_search_form_iframe_request_does_not_advance_the_native_fault_counter(
    signed_in: TestClient,
) -> None:
    signed_in.post("/admin/faults", json={"native_alert_at_step": 2})

    signed_in.get("/members/search")  # counted request 1
    signed_in.get("/members/search-form")  # the browser's own fetch; not counted
    assert NATIVE_ALERT in signed_in.get("/members/M-100").text  # counted request 2
