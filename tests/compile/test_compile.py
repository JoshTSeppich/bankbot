import json
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page

from bankbot.compile import (
    NoCheckpointAsserted,
    SecretLeakedIntoTranscript,
    TranscriptNotCompilable,
    compile_capability,
)
from bankbot.discover import GoalSpec, StopReason, ToolAction, Transcript
from bankbot.policy import Policy
from bankbot.schemas import (
    Capability,
    Fail,
    LocatorStrategy,
    Outcome,
    ParamRef,
    Retry,
    Success,
)
from tests.discover.conftest import (
    HAPPY_PATH,
    ScriptedDecider,
    act,
    lookup_spec,
    make_discovery,
)
from tests.replay.conftest import make_replay

Recorded = tuple[Transcript, GoalSpec]


def compiled(recorded: Recorded) -> Capability:
    transcript, spec = recorded
    return compile_capability(transcript, spec)


def test_compiled_capability_validates_and_carries_provenance(recorded: Recorded) -> None:
    capability = compiled(recorded)
    assert Capability.model_validate_json(capability.model_dump_json()) == capability
    assert capability.created_from_run is not None
    assert capability.created_from_run.model == "scripted"
    assert capability.created_from_run.request_ids == [f"req_{n}" for n in range(1, 7)]
    assert capability.app.fingerprint is not None
    assert capability.app.fingerprint.title == "Legacy Core Teller"
    assert capability.app.fingerprint.version == "7.2.1"
    assert list(capability.app.fingerprint.screen_fingerprints) == ["/members/search"]
    assert capability.app.fingerprint.screen_fingerprints["/members/search"], "has controls"


def test_the_typed_param_becomes_a_param_ref_and_its_value_is_nowhere_in_the_artifact(
    recorded: Recorded,
) -> None:
    capability = compiled(recorded)
    typed = next(step for step in capability.steps if step.id == "type_member_id")
    assert typed.value == ParamRef(param="member_id")
    recorded_part = capability.model_dump_json(include={"steps", "outputs", "checkpoint"})
    assert "M-100" not in recorded_part


def test_steps_follow_what_the_model_did_after_an_opening_navigate(recorded: Recorded) -> None:
    capability = compiled(recorded)
    assert [step.id for step in capability.steps] == [
        "open_start",
        "type_member_id",
        "click_search",
        "click_dana_whitfield",
        "read_savings_balance",
    ]
    assert capability.steps[0].on_fail == Fail()
    assert capability.preconditions[0].text_visible == "Signed in as"
    assert capability.recoveries[0].resume_from_step == "open_start"


def test_candidates_are_ranked_role_and_name_first_and_bbox_last(recorded: Recorded) -> None:
    capability = compiled(recorded)
    search = next(step for step in capability.steps if step.id == "click_search")
    assert search.target is not None
    strategies = [candidate.strategy for candidate in search.target.candidates]
    assert strategies[0] is LocatorStrategy.ROLE_NAME
    assert search.target.candidates[0].value == "button:Search"
    assert strategies[-1] is LocatorStrategy.BBOX
    assert search.target.frame_path == ["main"]
    confidences = [candidate.confidence for candidate in search.target.candidates]
    assert confidences == sorted(confidences, reverse=True)
    assert search.target.candidates[0].reasoning == "scripted click"


def test_extract_target_is_anchored_on_the_row_header_never_on_the_amount(
    recorded: Recorded,
) -> None:
    capability = compiled(recorded)
    extract = capability.outputs["savings_balance"].extract
    assert extract.candidates[0].value == 'th:text-is("Savings balance") + td'
    assert "$4,242.00" not in [candidate.value for candidate in extract.candidates]
    assert LocatorStrategy.BBOX not in [c.strategy for c in extract.candidates], (
        "a point has no text"
    )
    assert capability.outputs["savings_balance"].type == "money"


def test_url_changes_become_waits_and_the_last_assert_becomes_the_checkpoint(
    recorded: Recorded,
) -> None:
    capability = compiled(recorded)
    search = next(step for step in capability.steps if step.id == "click_search")
    opened = next(step for step in capability.steps if step.id == "click_dana_whitfield")
    assert search.wait_for is not None and search.wait_for.url_pattern == "/members/results$"
    assert search.on_fail == Retry(retries=2)
    assert opened.wait_for is not None and opened.wait_for.url_pattern == "/members/[^/]+$"
    assert capability.checkpoint.text_visible == "Savings balance"


def test_compiled_artifact_replays_to_the_same_results_as_the_hand_written_one(
    recorded: Recorded,
    page: Page,
    policy: Policy,
    base_url: str,
    tmp_path: Path,
    capability_json: dict[str, Any],
) -> None:
    hand_written = Capability.model_validate(capability_json)
    machine_made = compiled(recorded)
    for params, expected in ({"member_id": "M-100"}, Success), ({"member_id": "M-999"}, Outcome):
        results = []
        for capability in (hand_written, machine_made):
            context = page.context.browser.new_context() if page.context.browser else None
            assert context is not None
            replay, _ = make_replay(
                capability,
                params,
                page=context.new_page(),
                policy=policy,
                base_url=base_url,
                tmp_path=tmp_path / capability.created_from_run.run_id
                if capability.created_from_run
                else tmp_path,
            )
            results.append(replay.run())
            context.close()
        first, second = results
        assert isinstance(first, expected) and isinstance(second, expected)
        if isinstance(first, Success) and isinstance(second, Success):
            assert first.outputs == second.outputs == {"savings_balance": "4242.00"}
        if isinstance(first, Outcome) and isinstance(second, Outcome):
            assert first.code == second.code == "member_not_found"


def test_compiled_artifact_generalises_to_another_member_without_a_drift_warning(
    recorded: Recorded, page: Page, policy: Policy, base_url: str, tmp_path: Path
) -> None:
    replay, _ = make_replay(
        compiled(recorded),
        {"member_id": "M-101"},
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path,
    )
    result = replay.run()
    assert isinstance(result, Success)
    assert result.outputs == {"savings_balance": "1050.25"}
    # The link carries the recorded member's name, which is page data: the results row
    # position ranks first, so another member resolves without any drift.
    assert result.warnings == []


def test_a_typed_secret_refuses_to_compile(recorded: Recorded) -> None:
    transcript, spec = recorded
    with pytest.raises(SecretLeakedIntoTranscript):
        compile_capability(transcript, spec, secrets={"BANKBOT_PASSWORD": "M-100"})


def test_a_run_without_a_held_assert_refuses_to_compile(recorded: Recorded) -> None:
    transcript, spec = recorded
    without_assert = transcript.model_copy(
        update={
            "steps": [
                step
                for step in transcript.steps
                if step.action.action is not ToolAction.ASSERT_STATE
            ]
        }
    )
    with pytest.raises(NoCheckpointAsserted):
        compile_capability(without_assert, spec)


def test_a_run_that_did_not_finish_refuses_to_compile(recorded: Recorded) -> None:
    transcript, spec = recorded
    unfinished = transcript.model_copy(update={"stop_reason": StopReason.MAX_STEPS})
    with pytest.raises(TranscriptNotCompilable):
        compile_capability(unfinished, spec)


def test_the_artifact_round_trips_through_json_on_disk(recorded: Recorded, tmp_path: Path) -> None:
    capability = compiled(recorded)
    path = tmp_path / "capability.json"
    path.write_text(json.dumps(capability.model_dump(mode="json"), indent=2))
    assert Capability.model_validate_json(path.read_text()) == capability


def test_an_assert_that_names_the_output_value_is_generalised_to_the_text_around_it(
    page: Page, policy: Policy, base_url: str, tmp_path: Path
) -> None:
    valued_assert = act(
        ToolAction.ASSERT_STATE,
        role="row",
        name="Savings balance $4,242.00",
        description="The savings row shows",
    )
    script = [*HAPPY_PATH[:4], valued_assert, HAPPY_PATH[5]]
    spec = lookup_spec()
    discovery, _ = make_discovery(
        spec,
        {"member_id": "M-100"},
        ScriptedDecider(script),
        page=page,
        policy=policy,
        base_url=base_url,
        tmp_path=tmp_path / "discover",
    )
    capability = compile_capability(discovery.run(), spec)
    assert capability.checkpoint.target_visible is None
    assert capability.checkpoint.text_visible == "Savings balance"
    replay, _ = make_replay(
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


def test_a_link_named_after_a_record_ranks_its_position_first_and_its_name_last(
    recorded: Recorded,
) -> None:
    capability = compiled(recorded)
    link = next(step for step in capability.steps if step.id == "click_dana_whitfield")
    assert link.target is not None
    strategies = [candidate.strategy for candidate in link.target.candidates]
    assert strategies[0] is LocatorStrategy.CSS_STRUCTURAL
    assert strategies[-2:] == [LocatorStrategy.ROLE_NAME, LocatorStrategy.BBOX]
    search = next(step for step in capability.steps if step.id == "click_search")
    assert search.target is not None
    assert search.target.candidates[0].strategy is LocatorStrategy.ROLE_NAME, "buttons keep theirs"
