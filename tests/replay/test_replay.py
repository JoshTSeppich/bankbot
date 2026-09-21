import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page

from bankbot.evidence import read_events
from bankbot.policy import Policy
from bankbot.replay import ParamInvalid, SecretMissing
from bankbot.schemas import (
    ActionType,
    AppRef,
    Candidate,
    Capability,
    Failure,
    InterventionDecision,
    InterventionReason,
    InterventionRequest,
    LiteralValue,
    LocatorStrategy,
    Outcome,
    OutputRef,
    OutputSpec,
    StateAssertion,
    Step,
    Success,
    TargetRef,
    WarningCode,
)
from bankbot.surface import screen_fingerprint
from tests.discover.conftest import directory_spec
from tests.replay.conftest import TEST_SECRETS, arm_faults, make_replay


class RecordingEscalation:
    """Answers with a fixed decision and remembers what it was asked."""

    def __init__(self, decision: InterventionDecision, before: Any = None) -> None:
        self.decision = decision
        self.before = before
        self.requests: list[InterventionRequest] = []

    def aborted(self) -> bool:
        return False

    def request(self, request: InterventionRequest) -> InterventionDecision:
        self.requests.append(request)
        if self.before is not None:
            self.before()
        return self.decision


def load(capability_json: dict[str, Any]) -> Capability:
    return Capability.model_validate(capability_json)


def test_replay_success_returns_the_parsed_balance_and_a_complete_run_directory(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    replay, run_dir = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
    )
    result = replay.run()
    assert isinstance(result, Success)
    assert result.outputs == {"savings_balance": "4242.00"}
    assert result.warnings == []
    # A fresh browser is an expired session: the same recovery logs in.
    assert result.recoveries_used == ["session_expired"]
    assert run_dir.result_path.exists()
    assert result.evidence.screenshot is not None
    assert (run_dir.path / result.evidence.screenshot).exists()
    assert not run_dir.trace_path.exists(), "trace is deleted on success"
    assert result.evidence.trace is None
    events = [event["event"] for event in read_events(run_dir)]
    assert events[0] == "run_started" and events[-1] == "run_finished"
    assert "checkpoint_passed" in events


def test_replay_reports_member_not_found_as_outcome_not_failure(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    replay, run_dir = make_replay(
        load(capability_json),
        {"member_id": "M-999"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
    )
    result = replay.run()
    assert isinstance(result, Outcome)
    assert result.code == "member_not_found"
    assert run_dir.trace_path.exists(), "trace is kept on an outcome"
    assert result.evidence.trace == "trace.zip"


def test_replay_keeps_a_trace_with_no_credential_in_it(
    page: Page,
    policy: Policy,
    base_url: str,
    tmp_path: Path,
    capability_json: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Playwright writes trace.zip, not the evidence writer, and the login
    # recovery types the password into it five different ways. The redactor
    # reads the credential from the environment the way the CLI's .env supplies it.
    monkeypatch.setenv("BANKBOT_PASSWORD", TEST_SECRETS["BANKBOT_PASSWORD"])
    replay, run_dir = make_replay(
        load(capability_json),
        {"member_id": "M-999"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
    )
    replay.run()
    assert run_dir.trace_path.exists(), "trace is kept on an outcome"
    with zipfile.ZipFile(run_dir.trace_path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    assert members, "an empty archive would prove nothing"
    secret = TEST_SECRETS["BANKBOT_PASSWORD"].encode()
    assert [name for name, data in members.items() if secret in data] == []


def test_session_expiry_mid_run_is_recovered_and_the_run_still_succeeds(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    # Counted requests: 1 search (unauthenticated), 2 the redirect after login, 3 search again.
    arm_faults(base_url, session_expiry_at_step=3)
    replay, _ = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
    )
    result = replay.run()
    assert isinstance(result, Success)
    assert result.outputs == {"savings_balance": "4242.00"}
    assert result.recoveries_used == ["session_expired", "session_expired"]


def test_unknown_dialog_raises_an_intervention_request_and_unattended_runs_fail_with_it(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    # Counted requests: 1 the redirect after the precondition login, 2 the start screen,
    # which now carries the dialog over the search form.
    arm_faults(base_url, unknown_dialog_at_step=2)
    escalation = RecordingEscalation(InterventionDecision.ABORT)
    replay, run_dir = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
        escalation=escalation,
        step_timeout_ms=1500,
    )
    result = replay.run()
    assert isinstance(result, Failure)
    assert result.step_id == "submit_search"
    assert result.intervention is not None
    assert result.intervention.reason is InterventionReason.UNKNOWN_DIALOG
    assert "System notice" in result.intervention.observed
    assert escalation.requests[0].param_names == ["member_id"]
    assert escalation.requests[0].screenshot is not None
    assert (run_dir.path / escalation.requests[0].screenshot).exists()
    assert run_dir.trace_path.exists(), "trace is kept on a failure"


def test_a_human_dismissing_the_dialog_and_answering_resume_lets_the_run_finish(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    arm_faults(base_url, unknown_dialog_at_step=2)

    def dismiss() -> None:
        page.get_by_role("button", name="OK").click()

    escalation = RecordingEscalation(InterventionDecision.RESUME, before=dismiss)
    replay, _ = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
        escalation=escalation,
        step_timeout_ms=1500,
    )
    result = replay.run()
    assert isinstance(result, Success)
    assert len(escalation.requests) == 1


def test_candidate_exhaustion_fails_with_expected_and_observed_after_the_declared_retries(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    capability_json["steps"][2]["target"]["candidates"] = [
        {
            "strategy": "role_name",
            "value": "button:Nonexistent",
            "confidence": 0.9,
            "reasoning": "a control that is not there",
        }
    ]
    replay, run_dir = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
        step_timeout_ms=800,
    )
    result = replay.run()
    assert isinstance(result, Failure)
    assert result.step_id == "submit_search"
    assert result.expected == "click on 'Nonexistent'"
    assert result.observed.startswith("no candidate resolved: ")
    assert "button:Nonexistent" in result.observed
    assert result.intervention is not None
    assert result.intervention.reason is InterventionReason.CANDIDATE_EXHAUSTED
    retries = [event for event in read_events(run_dir) if event["event"] == "retry"]
    assert len(retries) == 2


def test_a_later_candidate_winning_succeeds_with_a_drift_warning(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    capability_json["steps"][2]["target"]["candidates"][0]["value"] = "button:Look up"
    replay, _ = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
    )
    result = replay.run()
    assert isinstance(result, Success)
    assert [warning.code for warning in result.warnings] == [WarningCode.DRIFT]
    assert result.warnings[0].step_id == "submit_search"


def test_variant_b_produces_a_variant_mismatch_warning_and_still_replays_through_drift(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    # Record the search screen's shape on variant A, the way discovery would have.
    page.goto(f"{base_url}/login")
    page.get_by_label("Username").fill("teller")
    page.get_by_label("Password").fill("teller-demo-password")
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url("**/members/search")
    capability_json["app"]["fingerprint"]["screen_fingerprints"] = {
        "/members/search": [element.model_dump() for element in screen_fingerprint(page)]
    }
    page.context.clear_cookies()
    replay, run_dir = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url + "/b",
        tmp_path=tmp_path,
    )
    result = replay.run()
    assert isinstance(result, Success)
    assert result.outputs == {"savings_balance": "4242.00"}
    codes = [warning.code for warning in result.warnings]
    assert WarningCode.VARIANT_MISMATCH in codes
    assert WarningCode.DRIFT in codes
    mismatch = next(w for w in result.warnings if w.code is WarningCode.VARIANT_MISMATCH)
    assert "7.3.0" in mismatch.detail and "7.2.1" in mismatch.detail
    compared = [e for e in read_events(run_dir) if e["event"] == "screen_compared"]
    assert compared and compared[0]["screen"] == "/members/search"
    # A renamed button is the same shape: the number in the log is what says so.
    assert compared[0]["distance"] == 0 and compared[0]["length"] == 2


def test_a_risky_step_on_a_draft_capability_asks_a_human_before_acting(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    capability_json["steps"].insert(
        4,
        {
            "id": "close_account",
            "action": "click",
            "target": {
                "candidates": [
                    {
                        "strategy": "role_name",
                        "value": "button:Close account",
                        "confidence": 0.9,
                        "reasoning": "the irreversible one",
                    }
                ],
                "frame_path": [],
            },
            "value": None,
            "wait_for": None,
            "on_fail": {"kind": "fail"},
        },
    )
    escalation = RecordingEscalation(InterventionDecision.ABORT)
    replay, _ = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
        escalation=escalation,
    )
    result = replay.run()
    assert isinstance(result, Failure)
    assert result.step_id == "close_account"
    assert escalation.requests[0].reason is InterventionReason.RISKY_NEEDS_APPROVAL
    assert "Account closed" not in page.content()


def test_bad_params_and_missing_secrets_are_rejected_before_the_browser_moves(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    replay, _ = make_replay(
        load(capability_json),
        {"member_id": "100234"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
    )
    with pytest.raises(ParamInvalid):
        replay.run()
    replay, _ = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
        secrets={},
    )
    with pytest.raises(SecretMissing):
        replay.run()
    assert page.url == "about:blank"


def test_secret_values_never_appear_in_the_run_log(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    replay, run_dir = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
    )
    replay.run()
    log = run_dir.log_path.read_text()
    assert "teller-demo-password" not in log
    assert "secret:BANKBOT_PASSWORD" in log


def test_a_structural_candidate_that_lands_on_a_risky_control_is_caught_by_its_screen_name(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    # The artifact calls the control "Continue"; no such button exists, and the structural
    # fallback resolves to the only button on the detail page, which is "Close account".
    capability_json["steps"].insert(
        4,
        {
            "id": "continue",
            "action": "click",
            "target": {
                "candidates": [
                    {
                        "strategy": "role_name",
                        "value": "button:Continue",
                        "confidence": 0.9,
                        "reasoning": "recorded as a harmless continue button",
                    },
                    {
                        "strategy": "css_structural",
                        "value": "form button",
                        "confidence": 0.4,
                        "reasoning": "the button in the form",
                    },
                ],
                "frame_path": [],
            },
            "value": None,
            "wait_for": None,
            "on_fail": {"kind": "fail"},
        },
    )
    escalation = RecordingEscalation(InterventionDecision.ABORT)
    replay, _ = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
        escalation=escalation,
    )
    result = replay.run()
    assert isinstance(result, Failure)
    assert result.step_id == "continue"
    assert escalation.requests[0].reason is InterventionReason.RISKY_NEEDS_APPROVAL
    assert "Close account" in result.observed
    assert "Account closed" not in page.content()


def test_a_navigate_is_checked_against_its_destination_not_the_page_it_leaves(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    capability_json["steps"].insert(
        1,
        {
            "id": "wander",
            "action": "navigate",
            "target": None,
            "value": {"literal": "/admin/faults"},
            "wait_for": None,
            "on_fail": {"kind": "fail"},
        },
    )
    replay, _ = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
    )
    result = replay.run()
    assert isinstance(result, Failure)
    assert result.step_id == "wander"
    assert "/admin/faults" in result.observed
    assert "/admin/faults" not in page.url


def test_an_approval_covers_one_attempt_and_a_rewind_asks_again(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    # A policy under which Search is risky, so the happy path has a step a person must approve.
    strict = Policy.model_validate(
        policy.model_dump() | {"risky": {"routes": [], "controls": ["^Search$"]}}
    )
    # Counted requests: 1 the redirect after the precondition login, 2 the start screen,
    # 3 the results page, so the session dies right after the approved click.
    arm_faults(base_url, session_expiry_at_step=3)
    escalation = RecordingEscalation(InterventionDecision.RESUME)
    replay, _ = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=strict,
        base_url=base_url,
        tmp_path=tmp_path,
        escalation=escalation,
    )
    result = replay.run()
    assert isinstance(result, Success)
    assert result.recoveries_used == ["session_expired", "session_expired"]
    assert [request.step_id for request in escalation.requests] == [
        "submit_search",
        "submit_search",
    ]
    assert all(r.reason is InterventionReason.RISKY_NEEDS_APPROVAL for r in escalation.requests)


def test_a_fresh_browser_meets_the_signed_in_precondition_without_a_failed_step(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    replay, run_dir = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
    )
    result = replay.run()
    assert isinstance(result, Success)
    assert result.recoveries_used == ["session_expired"], "logging in is the one recovery"
    events = [event["event"] for event in read_events(run_dir)]
    assert "step_failed" not in events, "nothing has to fail for the run to sign in"
    assert events[1] == "recovery_started", "the login runs before the first step is tried"


class AbortAfter:
    """An operator who presses Abort while the automation is running, after N steps."""

    def __init__(self, steps: int) -> None:
        self.steps = steps
        self.asked = 0

    def aborted(self) -> bool:
        self.asked += 1
        return self.asked > self.steps

    def request(self, request: InterventionRequest) -> InterventionDecision:
        return InterventionDecision.ABORT


def test_an_abort_pressed_while_the_automation_runs_stops_before_the_next_step(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    replay, run_dir = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
        escalation=AbortAfter(steps=2),
    )
    result = replay.run()
    assert isinstance(result, Failure)
    assert result.step_id == capability_json["steps"][2]["id"]
    assert result.observed == "aborted by the operator"
    started = [e["step_id"] for e in read_events(run_dir) if e["event"] == "step_started"]
    assert capability_json["steps"][2]["id"] not in started


def test_replay_reports_a_load_that_never_finishes_as_a_failure_not_a_crash(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    arm_faults(base_url, slow_load_ms=3000)
    replay, run_dir = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
        step_timeout_ms=500,
    )
    result = replay.run()
    assert isinstance(result, Failure)
    assert result.step_id == capability_json["steps"][0]["id"]
    assert result.expected == f"the app opens at {base_url}"
    assert "about:blank" in result.observed
    assert run_dir.result_path.exists()


def test_a_failed_run_still_ends_its_log_with_run_finished(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    arm_faults(base_url, slow_load_ms=3000)
    replay, run_dir = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
        step_timeout_ms=500,
    )
    replay.run()
    events = [event["event"] for event in read_events(run_dir)]
    assert events[0] == "run_started"
    assert events[-1] == "run_finished"
    assert run_dir.result_path.exists()


class ClosesTheBrowser:
    """A person who answers an intervention by closing the window and walking away."""

    def __init__(self, page: Page) -> None:
        self.page = page

    def aborted(self) -> bool:
        return False

    def request(self, request: InterventionRequest) -> InterventionDecision:
        self.page.close()
        return InterventionDecision.RESUME


def test_closing_the_page_mid_step_ends_the_run_as_session_lost(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    arm_faults(base_url, unknown_dialog_at_step=2)
    replay, run_dir = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
        escalation=ClosesTheBrowser(page),
        step_timeout_ms=1500,
    )
    result = replay.run()
    assert isinstance(result, Failure)
    assert result.step_id == "submit_search"
    assert result.observed.startswith("session_lost")
    events = [event["event"] for event in read_events(run_dir)]
    assert events[-1] == "run_finished"
    assert run_dir.result_path.exists()


def test_a_session_lost_failure_is_written_without_a_screenshot_or_a_trace(
    page: Page, policy: Policy, base_url: str, tmp_path: Path, capability_json: dict[str, Any]
) -> None:
    arm_faults(base_url, unknown_dialog_at_step=2)
    replay, run_dir = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
        escalation=ClosesTheBrowser(page),
        step_timeout_ms=1500,
    )
    result = replay.run()
    assert isinstance(result, Failure)
    assert result.evidence.screenshot is None
    assert result.evidence.trace is None
    assert not run_dir.trace_path.exists()
    written = json.loads(run_dir.result_path.read_text())
    assert written["evidence"]["screenshot"] is None


def directory_capability() -> Capability:
    """The directory flow by hand: the link to click is named by the caller's input."""
    spec = directory_spec()
    return Capability(
        id=spec.capability_id,
        name=spec.name,
        version="1.0.0",
        app=AppRef(vendor=spec.app.vendor, app_id=spec.app.app_id, variant=spec.app.variant),
        inputs=dict(spec.inputs),
        outputs={
            "savings_balance": OutputSpec(
                type="money",
                extract=TargetRef(
                    candidates=[
                        Candidate(
                            strategy=LocatorStrategy.CSS_STRUCTURAL,
                            value='td:text-is("Savings balance:") + td',
                            confidence=0.85,
                            reasoning="The cell right after its label cell.",
                        )
                    ]
                ),
            )
        },
        preconditions=list(spec.preconditions),
        steps=[
            Step(
                id="open_start",
                action=ActionType.NAVIGATE,
                value=LiteralValue(literal=spec.start_path),
                wait_for=StateAssertion(
                    description="The directory is shown", url_pattern=r"/members/directory$"
                ),
            ),
            Step(
                id="open_profile",
                action=ActionType.CLICK,
                target=TargetRef(
                    candidates=[
                        Candidate(
                            strategy=LocatorStrategy.ROLE_NAME,
                            value="link:{input:member_id}",
                            confidence=0.95,
                            reasoning="The caller asked for this member by id.",
                        )
                    ]
                ),
                wait_for=StateAssertion(
                    description="The profile is open", url_pattern=r"/members/profile$"
                ),
            ),
            Step(
                id="read_savings_balance",
                action=ActionType.EXTRACT,
                value=OutputRef(output="savings_balance"),
            ),
        ],
        checkpoint=StateAssertion(
            description="The profile shows a savings balance", text_visible="Savings balance"
        ),
        recoveries=[
            recovery.model_copy(update={"resume_from_step": "open_start"})
            for recovery in spec.recoveries
        ],
    )


def test_a_locator_that_names_an_input_is_filled_in_before_the_first_step(
    page: Page, policy: Policy, base_url: str, tmp_path: Path
) -> None:
    replay, _ = make_replay(
        directory_capability(),
        {"member_id": "M-101"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
    )
    result = replay.run()
    assert isinstance(result, Success)
    assert result.outputs == {"savings_balance": "1050.25"}
    assert result.warnings == []


def test_a_failure_about_a_filled_locator_names_the_input_not_the_value(
    page: Page, policy: Policy, base_url: str, tmp_path: Path
) -> None:
    replay, run_dir = make_replay(
        directory_capability(),
        {"member_id": "M-999"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
    )
    result = replay.run()
    assert isinstance(result, Failure)
    assert result.intervention is not None
    assert result.intervention.reason is InterventionReason.CANDIDATE_EXHAUSTED
    assert "link:{input:member_id}" in result.observed
    assert "M-999" not in result.observed
    assert "M-999" not in run_dir.log_path.read_text()


def test_an_output_that_does_not_parse_is_described_and_never_quoted(
    page: Page, policy: Policy, base_url: str, tmp_path: Path
) -> None:
    capability = directory_capability()
    # Aimed at the branch cell, which is a member's data and is not money.
    misaimed = capability.outputs["savings_balance"].model_copy(
        update={
            "extract": TargetRef(
                candidates=[
                    Candidate(
                        strategy=LocatorStrategy.CSS_STRUCTURAL,
                        value='td:text-is("Branch:") + td',
                        confidence=0.85,
                        reasoning="The cell right after the one that labels its row.",
                    )
                ]
            )
        }
    )
    replay, run_dir = make_replay(
        capability.model_copy(update={"outputs": {"savings_balance": misaimed}}),
        {"member_id": "M-101"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
    )
    result = replay.run()
    assert isinstance(result, Failure)
    assert "7 characters" in result.observed
    assert "money" in result.observed
    assert "Hilltop" not in result.observed
    assert "Hilltop" not in run_dir.log_path.read_text()


def test_replay_reports_an_undeclared_confirm_as_unknown_dialog_not_checkpoint_unmet(
    page: Page,
    policy: Policy,
    base_url: str,
    tmp_path: Path,
    capability_json: dict[str, Any],
) -> None:
    arm_faults(base_url, native_confirm_at_step=3)
    replay, _ = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
    )
    result = replay.run()
    assert isinstance(result, Failure)
    assert result.intervention is not None
    assert result.intervention.reason is InterventionReason.UNKNOWN_DIALOG
    assert result.observed == "confirm: 'Restricted member. Continue?' (dismissed)"


def test_replay_does_not_retry_a_click_that_raised_a_confirm(
    page: Page,
    policy: Policy,
    base_url: str,
    tmp_path: Path,
    capability_json: dict[str, Any],
) -> None:
    arm_faults(base_url, native_confirm_at_step=3)
    replay, run_dir = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
    )
    replay.run()
    events = read_events(run_dir)
    attempts = [
        event
        for event in events
        if event["event"] == "step_started" and event["step_id"] == "open_member"
    ]
    assert len(attempts) == 1, "the step declares retries; a question is not a transient"
    assert not [event for event in events if event["event"] == "retry"]


def test_a_native_alert_is_logged_and_the_run_still_succeeds(
    page: Page,
    policy: Policy,
    base_url: str,
    tmp_path: Path,
    capability_json: dict[str, Any],
) -> None:
    arm_faults(base_url, native_alert_at_step=2)
    replay, run_dir = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
    )
    result = replay.run()
    assert isinstance(result, Success)
    assert result.outputs == {"savings_balance": "4242.00"}
    raised = [event for event in read_events(run_dir) if event["event"] == "native_dialog"]
    assert [(event["type"], event["answer"]) for event in raised] == [("alert", "accepted")]
    assert raised[0]["message"] == "Your session will expire in 2 minutes."


def test_an_undisturbed_run_logs_nothing_new_so_its_event_sequence_is_unchanged(
    page: Page,
    policy: Policy,
    base_url: str,
    tmp_path: Path,
    capability_json: dict[str, Any],
) -> None:
    replay, run_dir = make_replay(
        load(capability_json),
        {"member_id": "M-100"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
    )
    assert isinstance(replay.run(), Success)
    # The event sequence hash is over these lines, so one new name on the happy
    # path would silently invalidate every hash committed under evidence/. The
    # committed run 02 carries one more, screen_compared, because its capability
    # was compiled and so has screen fingerprints; this fixture is hand-written.
    assert sorted({str(event["event"]) for event in read_events(run_dir)}) == [
        "checkpoint_passed",
        "output_extracted",
        "recovery_finished",
        "recovery_started",
        "run_finished",
        "run_started",
        "step_done",
        "step_started",
        "target_resolved",
        "trace",
    ]
