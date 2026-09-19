"""One attempt at one step: policy first, then the surface, then the step's wait_for.

Owns: the StepRunner, which is the only code that turns a Step into surface
calls. Replay uses it for every step and every recovery; discovery uses it
to reach the logged-in start screen before the model takes over. Two users
is why it is its own class rather than a method on Replay.

Does not own: what to do when an attempt fails (replay/run.py decides that
in a fixed order) or how a human is asked.

Governed by ADR-0003 (error taxonomy) and ADR-0005 (policy model: this is
replay's enforcement point).
"""

from collections.abc import Mapping
from dataclasses import dataclass

from bankbot.evidence import EvidenceWriter
from bankbot.policy import Policy
from bankbot.replay.values import OutputUnreadable, describe_value, parse_output, value_for
from bankbot.schemas import (
    ActionType,
    Approval,
    InterventionReason,
    LiteralValue,
    LocatorStrategy,
    OutputRef,
    OutputSpec,
    Recovery,
    Step,
    TargetRef,
)
from bankbot.surface import ActionFailed, FrameNotFound, Surface, TargetNotFound

STEP_TIMEOUT_MS = 5000


class StepFailed(Exception):
    """One attempt at a step did not end where the artifact says it should.

    `reason` is the intervention reason this becomes if no rule handles it.
    """

    def __init__(self, expected: str, observed: str, reason: InterventionReason) -> None:
        super().__init__(f"expected {expected}; observed {observed}")
        self.expected = expected
        self.observed = observed
        self.reason = reason


class PolicyBlocked(Exception):
    """The policy refused an action outright. Not recoverable, not escalated: the run stops."""


@dataclass(frozen=True)
class Attempted:
    """What one successful attempt reports back: which candidate won, and any value read."""

    candidate_index: int | None = None
    output_name: str | None = None
    output_value: str | None = None


class StepRunner:
    """Runs steps against a surface under a policy. Its only state is human-granted approvals."""

    def __init__(
        self,
        *,
        surface: Surface,
        policy: Policy,
        writer: EvidenceWriter,
        params: Mapping[str, str],
        secrets: Mapping[str, str],
        base_url: str,
        outputs: Mapping[str, OutputSpec],
        approval: Approval = Approval.DRAFT,
        step_timeout_ms: int = STEP_TIMEOUT_MS,
    ) -> None:
        self.surface = surface
        self.policy = policy
        self.writer = writer
        self.params = params
        self.secrets = secrets
        self.base_url = base_url.rstrip("/")
        self.outputs = outputs
        self.approval = approval
        self.step_timeout_ms = step_timeout_ms
        self.approved_steps: set[str] = set()

    def attempt(self, step: Step) -> Attempted:
        """Do the step once. Raises StepFailed with the reason a human would be given."""
        self.writer.event(
            "step_started", step_id=step.id, action=step.action.value, value=describe_value(step)
        )
        attempted = Attempted()
        if step.action in (
            ActionType.NAVIGATE,
            ActionType.CLICK,
            ActionType.TYPE,
            ActionType.SELECT,
        ):
            attempted = self._act(step)
        elif step.action is ActionType.EXTRACT:
            attempted = self._extract(step)
        elif step.wait_for is None:
            raise StepFailed(
                "a wait_for to check", "none declared", InterventionReason.CHECKPOINT_UNMET
            )
        if step.wait_for is not None and not self.surface.holds(
            step.wait_for, self.step_timeout_ms
        ):
            raise StepFailed(step.wait_for.description, self.observed(), self.reason_now())
        if attempted.candidate_index is not None:
            self.writer.event(
                "target_resolved", step_id=step.id, candidate_index=attempted.candidate_index
            )
        self.writer.event("step_done", step_id=step.id)
        return attempted

    def run_recovery(self, recovery: Recovery) -> StepFailed | None:
        """Run a recovery's steps once; the first failure is returned, not retried."""
        self.writer.event("recovery_started", recovery=recovery.id)
        for step in recovery.steps:
            try:
                self.attempt(step)
            except StepFailed as failed:
                self.writer.event(
                    "step_failed",
                    step_id=step.id,
                    expected=failed.expected,
                    observed=failed.observed,
                )
                return failed
        self.writer.event("recovery_finished", recovery=recovery.id)
        return None

    def _act(self, step: Step) -> Attempted:
        control = control_name(step.target)
        decision = self.policy.check(self.url(), step.action, control)
        if not decision.allowed:
            self.writer.event("policy_blocked", step_id=step.id, reason=decision.reason)
            raise PolicyBlocked(f"blocked: {decision.reason}")
        approved = self.approval is Approval.APPROVED or step.id in self.approved_steps
        if decision.risky and not approved:
            raise StepFailed(
                "a human approving this risky step",
                f"{step.action.value} on {control!r} is risky and the capability is a draft",
                InterventionReason.RISKY_NEEDS_APPROVAL,
            )
        value = value_for(step, self.params, self.secrets)
        if step.action is ActionType.NAVIGATE and isinstance(step.value, LiteralValue):
            # Artifacts store paths; the deployment they run against is a run-time fact.
            value = self.base_url + step.value.literal
        try:
            result = self.surface.act(step.action, step.target, value, self.step_timeout_ms)
        except TargetNotFound as missing:
            raise StepFailed(
                f"{step.action.value} on {control!r}",
                "no candidate resolved: " + "; ".join(missing.tried),
                InterventionReason.CANDIDATE_EXHAUSTED,
            ) from missing
        except FrameNotFound as missing:
            frame_path = step.target.frame_path if step.target else []
            raise StepFailed(
                f"frame {frame_path}", str(missing), InterventionReason.CANDIDATE_EXHAUSTED
            ) from missing
        except ActionFailed as failed:
            raise StepFailed(
                f"{failed.action} on {control!r} to complete",
                f"{failed.reason} at {self.observed()}",
                self.reason_now(),
            ) from failed
        return Attempted(candidate_index=result.candidate_index)

    def _extract(self, step: Step) -> Attempted:
        if not isinstance(step.value, OutputRef):
            raise StepFailed("an output name", "none", InterventionReason.CHECKPOINT_UNMET)
        name = step.value.output
        spec = self.outputs[name]
        try:
            read = self.surface.read(spec.extract, self.step_timeout_ms)
        except TargetNotFound as missing:
            raise StepFailed(
                f"output {name!r} on screen",
                "no candidate resolved: " + "; ".join(missing.tried),
                InterventionReason.CANDIDATE_EXHAUSTED,
            ) from missing
        try:
            value = parse_output(name, spec, read.text)
        except OutputUnreadable as unreadable:
            raise StepFailed(
                f"output {name!r} as {spec.type}", read.text, InterventionReason.CHECKPOINT_UNMET
            ) from unreadable
        self.writer.event("output_extracted", output=name, value=value)
        return Attempted(candidate_index=read.candidate_index, output_name=name, output_value=value)

    # --- what the page looks like right now, in words ----------------------

    def url(self) -> str:
        """The current URL, for policy checks and screen hashes."""
        return self.surface.observe().url

    def observed(self) -> str:
        """One line a human can read: where the page is and whether a dialog covers it."""
        observation = self.surface.observe()
        if observation.dialog_text:
            return f"{observation.url} with a dialog: {observation.dialog_text!r}"
        return f"{observation.url} ({observation.title})"

    def reason_now(self) -> InterventionReason:
        """A dialog on screen is the one unknown condition the page itself announces."""
        if self.surface.observe().dialog_text:
            return InterventionReason.UNKNOWN_DIALOG
        return InterventionReason.CHECKPOINT_UNMET


def control_name(target: TargetRef | None) -> str | None:
    """The human name of a control for the policy and the log, from the best named candidate."""
    if target is None:
        return None
    for candidate in target.candidates:
        if candidate.strategy is LocatorStrategy.ROLE_NAME:
            return candidate.value.split(":", 1)[1] if ":" in candidate.value else candidate.value
        if candidate.strategy in (LocatorStrategy.LABEL, LocatorStrategy.TEXT):
            return candidate.value
    return target.candidates[0].value
