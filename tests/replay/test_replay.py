from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page

from bankbot.evidence import read_events
from bankbot.policy import Policy
from bankbot.replay import ParamInvalid, SecretMissing
from bankbot.schemas import (
    Capability,
    Failure,
    InterventionDecision,
    InterventionReason,
    InterventionRequest,
    Outcome,
    Success,
    WarningCode,
)
from tests.replay.conftest import arm_faults, make_replay


class RecordingEscalation:
    """Answers with a fixed decision and remembers what it was asked."""

    def __init__(self, decision: InterventionDecision, before: Any = None) -> None:
        self.decision = decision
        self.before = before
        self.requests: list[InterventionRequest] = []

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
    # Counted requests: 1 search (unauthenticated), 2 the redirect after login, 3 search again.
    arm_faults(base_url, unknown_dialog_at_step=3)
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
    arm_faults(base_url, unknown_dialog_at_step=3)

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
    replay, _ = make_replay(
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
