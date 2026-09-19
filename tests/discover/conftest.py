"""A scripted stand-in for the model, and a Discovery wired to the shared app and browser."""

from pathlib import Path

from anthropic.types import MessageParam
from playwright.sync_api import Page

from bankbot.discover import (
    Decided,
    Discovery,
    GoalSpec,
    ProposedAction,
    ToolAction,
    load_goal_spec,
)
from bankbot.evidence import EvidenceWriter, RunDir, new_run_id
from bankbot.policy import Policy
from bankbot.replay.escalation import Escalation
from bankbot.surface import PlaywrightSurface

GOALS = Path(__file__).parent.parent.parent / "bankbot" / "discover" / "goals"
TEST_SECRETS = {"BANKBOT_USERNAME": "teller", "BANKBOT_PASSWORD": "teller-demo-password"}


class ScriptedDecider:
    """Plays back a fixed list of actions; the loop cannot tell it from a model."""

    def __init__(self, actions: list[ProposedAction]) -> None:
        self._actions = list(actions)
        self.calls = 0
        self.systems: list[str] = []
        self.conversations: list[list[MessageParam]] = []

    @property
    def model(self) -> str:
        return "scripted"

    def decide(self, system: str, messages: list[MessageParam]) -> Decided:
        self.systems.append(system)
        self.conversations.append(list(messages))
        action = self._actions[self.calls]
        self.calls += 1
        return Decided(
            action=action,
            tool_use_id=f"toolu_{self.calls}",
            request_id=f"req_{self.calls}",
            input_tokens=100,
            output_tokens=10,
        )


def act(kind: ToolAction, **fields: str) -> ProposedAction:
    return ProposedAction(reasoning=f"scripted {kind.value}", action=kind, **fields)


HAPPY_PATH = [
    act(ToolAction.TYPE, role="textbox", name="Member ID", text="M-100"),
    act(ToolAction.CLICK, role="button", name="Search"),
    act(ToolAction.CLICK, role="link", name="Dana Whitfield"),
    act(ToolAction.EXTRACT, role="cell", name="$4,242.00", output_name="savings_balance"),
    act(ToolAction.ASSERT_STATE, text="Savings balance", description="The accounts table shows"),
    act(ToolAction.DONE, summary="Savings balance is $4,242.00"),
]


def lookup_spec() -> GoalSpec:
    return load_goal_spec(GOALS / "lookup_savings_balance.json")


def close_spec() -> GoalSpec:
    return load_goal_spec(GOALS / "close_member_account.json")


def make_discovery(
    spec: GoalSpec,
    params: dict[str, str],
    decider: ScriptedDecider,
    *,
    page: Page,
    policy: Policy,
    base_url: str,
    tmp_path: Path,
    escalation: Escalation | None = None,
    max_steps: int = 12,
) -> tuple[Discovery, RunDir]:
    run_dir = RunDir.create(tmp_path / "runs", new_run_id())
    writer = EvidenceWriter(run_dir, policy.redactor(extra_values=params.values()))
    discovery = Discovery(
        spec,
        params,
        surface=PlaywrightSurface(page, policy.mask_selectors),
        policy=policy,
        run_dir=run_dir,
        writer=writer,
        decider=decider,
        base_url=base_url,
        secrets=TEST_SECRETS,
        escalation=escalation,
        max_steps=max_steps,
    )
    return discovery, run_dir
