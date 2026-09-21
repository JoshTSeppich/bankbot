from pathlib import Path

from playwright.sync_api import Page

from bankbot.compile import compile_capability
from bankbot.discover import ProposedAction, ToolAction
from bankbot.evidence import read_events
from bankbot.policy import Policy
from bankbot.schemas import Capability, Failure, LocatorStrategy, Outcome, Success
from tests.discover.conftest import ScriptedDecider, act, directory_spec, make_discovery
from tests.replay.conftest import make_replay

# The directory screens reach a record by clicking a link whose text is the member id,
# which is the shape ParaBank has and the shipped screens do not.
FROM_DIRECTORY = [
    act(ToolAction.CLICK, role="link", name="M-100"),
    act(ToolAction.EXTRACT, role="cell", name="$4,242.00", output_name="savings_balance"),
    act(ToolAction.ASSERT_STATE, text="Savings balance", description="The profile shows"),
    act(ToolAction.DONE, summary="Savings balance is $4,242.00"),
]


def record_on_m100(page: Page, policy: Policy, base_url: str, tmp_path: Path) -> Capability:
    spec = directory_spec()
    discovery, _ = make_discovery(
        spec,
        {"member_id": "M-100"},
        ScriptedDecider(FROM_DIRECTORY),
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path / "discover",
    )
    return compile_capability(discovery.run(), spec)


def test_a_clicked_control_named_by_an_input_is_compiled_as_that_input(
    page: Page, policy: Policy, base_url: str, tmp_path: Path
) -> None:
    capability = record_on_m100(page, policy, base_url, tmp_path)
    click = next(step for step in capability.steps if step.action.value == "click")
    assert click.target is not None
    assert [candidate.value for candidate in click.target.candidates] == ["link:{input:member_id}"]
    assert click.target.candidates[0].strategy is LocatorStrategy.ROLE_NAME
    assert "M-100" not in capability.model_dump_json()


def test_a_capability_recorded_on_one_member_returns_the_balance_of_the_member_asked_for(
    page: Page, policy: Policy, base_url: str, tmp_path: Path
) -> None:
    capability = record_on_m100(page, policy, base_url, tmp_path)
    replay, run_dir = make_replay(
        capability,
        {"member_id": "M-101"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path / "replay",
    )
    result = replay.run()
    assert isinstance(result, Success)
    assert result.outputs == {"savings_balance": "1050.25"}
    assert result.warnings == []
    resolved = [event for event in read_events(run_dir) if event["event"] == "target_resolved"]
    assert resolved and all(event["candidate_index"] == 0 for event in resolved), resolved


def test_an_output_on_a_labelled_row_is_anchored_on_its_label_cell_first(
    page: Page, policy: Policy, base_url: str, tmp_path: Path
) -> None:
    capability = record_on_m100(page, policy, base_url, tmp_path)
    candidates = capability.outputs["savings_balance"].extract.candidates
    assert candidates[0].value == 'td:text-is("Savings balance:") + td'
    assert candidates[1].strategy is LocatorStrategy.CSS_STRUCTURAL
    assert candidates[1].value.startswith("body >")


def test_a_member_whose_profile_has_an_extra_row_still_returns_their_own_balance(
    page: Page, policy: Policy, base_url: str, tmp_path: Path
) -> None:
    capability = record_on_m100(page, policy, base_url, tmp_path)
    replay, _ = make_replay(
        capability,
        {"member_id": "M-103"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path / "replay",
    )
    result = replay.run()
    assert isinstance(result, Success), result
    assert result.outputs == {"savings_balance": "987.65"}


def test_the_models_own_sentence_is_dropped_when_it_quotes_a_recorded_value(
    page: Page, policy: Policy, base_url: str, tmp_path: Path
) -> None:
    quoting = ProposedAction(
        reasoning="M-100 is the row the caller asked for.",
        action=ToolAction.CLICK,
        role="link",
        name="M-100",
    )
    spec = directory_spec()
    discovery, _ = make_discovery(
        spec,
        {"member_id": "M-100"},
        ScriptedDecider([quoting, *FROM_DIRECTORY[1:]]),
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path / "discover",
    )
    capability = compile_capability(discovery.run(), spec)
    click = next(step for step in capability.steps if step.action.value == "click")
    assert click.target is not None
    assert "M-100" not in click.target.candidates[0].reasoning
    assert "M-100" not in capability.model_dump_json()


def test_a_member_who_is_not_listed_is_never_a_success(
    page: Page, policy: Policy, base_url: str, tmp_path: Path
) -> None:
    capability = record_on_m100(page, policy, base_url, tmp_path)
    replay, _ = make_replay(
        capability,
        {"member_id": "M-999"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path / "replay",
    )
    result = replay.run()
    assert isinstance(result, Failure | Outcome), result
    assert result.kind in ("failure", "outcome")
