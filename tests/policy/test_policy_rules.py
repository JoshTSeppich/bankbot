from pathlib import Path
from typing import Any

import pytest
import yaml

from bankbot.policy import (
    DEFAULT_POLICY_PATH,
    Policy,
    PolicyFileInvalid,
    PolicyFileMissing,
    load_policy,
)
from bankbot.schemas import ActionType

SEARCH_URL = "http://127.0.0.1:8000/members/search"
MEMBER_URL = "http://127.0.0.1:8000/members/M-100"
CLOSE_URL = "http://127.0.0.1:8000/members/M-100/close"


@pytest.fixture(scope="module")
def policy() -> Policy:
    return load_policy()


def write_policy(tmp_path: Path, mutate: dict[str, Any]) -> Path:
    data: dict[str, Any] = yaml.safe_load(DEFAULT_POLICY_PATH.read_text())
    data.update(mutate)
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


# --- Loading ---------------------------------------------------------------


def test_the_committed_policy_file_loads() -> None:
    loaded = load_policy()
    assert "127.0.0.1" in loaded.allowed_hosts
    assert ActionType.CLICK in loaded.allowed_actions


def test_invalid_regex_in_policy_file_fails_at_load_naming_the_key(tmp_path: Path) -> None:
    path = write_policy(tmp_path, {"risky": {"routes": ["/close$"], "controls": ["("]}})
    with pytest.raises(PolicyFileInvalid, match=r"risky\.controls\[0\]"):
        load_policy(path)


def test_unknown_key_in_policy_file_fails_at_load_naming_the_key(tmp_path: Path) -> None:
    path = write_policy(tmp_path, {"allowed_host": ["localhost"]})
    with pytest.raises(PolicyFileInvalid, match="allowed_host"):
        load_policy(path)


def test_unknown_action_name_in_policy_file_fails_at_load(tmp_path: Path) -> None:
    path = write_policy(tmp_path, {"allowed_actions": ["click", "hover"]})
    with pytest.raises(PolicyFileInvalid, match="allowed_actions"):
        load_policy(path)


def test_missing_policy_file_is_reported_as_missing(tmp_path: Path) -> None:
    with pytest.raises(PolicyFileMissing, match=r"nowhere\.yaml"):
        load_policy(tmp_path / "nowhere.yaml")


# --- Allowlist -------------------------------------------------------------


def test_search_click_on_the_demo_app_is_allowed(policy: Policy) -> None:
    decision = policy.check(url=SEARCH_URL, action=ActionType.CLICK, control_name="Search")
    assert decision.allowed is True
    assert decision.risky is False
    assert decision.reason == "host, route and action are allowed"


def test_host_outside_allowlist_is_blocked_with_the_host_named_in_the_reason(
    policy: Policy,
) -> None:
    decision = policy.check(
        url="http://evil.example/members/search", action=ActionType.CLICK, control_name="Search"
    )
    assert decision.allowed is False
    assert "evil.example" in decision.reason
    assert "allowed_hosts" in decision.reason


def test_port_is_ignored_when_comparing_hosts(policy: Policy) -> None:
    decision = policy.check(
        url="http://localhost:59999/members/search", action=ActionType.CLICK, control_name="Search"
    )
    assert decision.allowed is True


def test_route_outside_allowlist_is_blocked(policy: Policy) -> None:
    decision = policy.check(
        url="http://127.0.0.1:8000/admin/faults", action=ActionType.NAVIGATE, control_name=None
    )
    assert decision.allowed is False
    assert "/admin/faults" in decision.reason
    assert "allowed_routes" in decision.reason


def test_action_outside_allowlist_is_blocked(tmp_path: Path) -> None:
    narrow = load_policy(write_policy(tmp_path, {"allowed_actions": ["navigate", "click"]}))
    decision = narrow.check(url=SEARCH_URL, action=ActionType.TYPE, control_name="Member ID")
    assert decision.allowed is False
    assert "'type'" in decision.reason
    assert "allowed_actions" in decision.reason


def test_variant_b_routes_are_inside_the_allowlist(policy: Policy) -> None:
    decision = policy.check(
        url="http://127.0.0.1:8000/b/members/search", action=ActionType.CLICK, control_name="Find"
    )
    assert decision.allowed is True


# --- Risk ------------------------------------------------------------------


def test_close_account_button_is_classified_risky_by_control_name(policy: Policy) -> None:
    assert policy.is_risky(url=MEMBER_URL, control_name="Close account") is True


def test_control_name_matching_is_case_insensitive(policy: Policy) -> None:
    assert policy.is_risky(url=MEMBER_URL, control_name="CLOSE ACCOUNT") is True


def test_close_route_is_classified_risky_by_route(policy: Policy) -> None:
    assert policy.is_risky(url=CLOSE_URL, control_name=None) is True


def test_search_button_on_member_page_is_not_risky(policy: Policy) -> None:
    assert policy.is_risky(url=MEMBER_URL, control_name="Search") is False


def test_risky_action_can_still_be_allowed_by_the_allowlist(policy: Policy) -> None:
    decision = policy.check(url=MEMBER_URL, action=ActionType.CLICK, control_name="Close account")
    assert decision.allowed is True
    assert decision.risky is True


def test_blocked_decision_still_reports_risk_so_the_caller_can_log_both(policy: Policy) -> None:
    decision = policy.check(
        url="http://evil.example/accounts/close", action=ActionType.CLICK, control_name="Confirm"
    )
    assert decision.allowed is False
    assert decision.risky is True


# --- Redaction settings ----------------------------------------------------


def test_mask_selectors_come_from_the_redaction_section(policy: Policy) -> None:
    assert policy.mask_selectors == ["input[type=password]"]


def test_policy_redactor_uses_the_suffixes_from_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = load_policy(
        write_policy(
            tmp_path,
            {"redaction": {"secret_env_suffixes": ["_PIN"], "mask_selectors": []}},
        )
    )
    monkeypatch.setenv("FAKE_TELLER_PIN", "fake-pin-value-9x")
    assert custom.redactor().text("pin is fake-pin-value-9x") == "pin is [REDACTED]"
