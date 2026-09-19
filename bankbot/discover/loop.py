"""The discovery loop: observe, ask the model for one action, check policy, act, repeat.

Owns: the loop, its stopping rules and the bookkeeping around them. The
model only ever proposes; this loop decides whether a proposal is allowed,
whether `done` is earned (an assert_state after the last action and every
declared output extracted), and when to stop because nothing is changing.
A blocked action goes back to the model as an observation, never silently
dropped; two blocks in a row ask a human.

Does not own: talking to the model (discover/model.py), the tool shape
(discover/tools.py), or compiling what it recorded (compile/).

Governed by ADR-0005 (policy model: discovery's enforcement point),
ADR-0004 (control transfer: stuck and risky both escalate) and ADR-0002.
"""

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import version

from anthropic.types import MessageParam

from bankbot.discover.model import (
    Decided,
    Decider,
    perception_message,
    tool_result_message,
    tool_use_message,
)
from bankbot.discover.spec import GoalSpec
from bankbot.discover.tools import ProposedAction, ToolAction, system_prompt
from bankbot.discover.transcript import StepStatus, StopReason, Transcript, TranscriptStep
from bankbot.evidence import EvidenceWriter, RunDir, read_events
from bankbot.policy import Decision, Policy
from bankbot.replay import StepRunner, Unattended
from bankbot.replay.escalation import Escalation
from bankbot.replay.values import check_params, check_secrets, parse_output
from bankbot.schemas import (
    ActionType,
    AppFingerprint,
    Candidate,
    InterventionDecision,
    InterventionReason,
    InterventionRequest,
    LocatorStrategy,
    OutputSpec,
    StateAssertion,
    TargetRef,
)
from bankbot.surface import (
    ActionFailed,
    ElementFacts,
    FrameNotFound,
    Observation,
    Surface,
    TargetNotFound,
)

MAX_STEPS = 25
MAX_SECONDS = 180.0
STUCK_AFTER_UNCHANGED_ACTIONS = 3
BLOCKS_BEFORE_ESCALATION = 2
LOCATE_TIMEOUT_MS = 700
ASSERT_TIMEOUT_MS = 2000
ACT_TIMEOUT_MS = 5000
LOG_TAIL_LINES = 12


class DiscoveryCouldNotStart(Exception):
    """The start screen was not reachable, usually because the declared login recovery failed."""


@dataclass
class Applied:
    """What the loop did with one proposal, in the words the model gets back."""

    status: StepStatus
    detail: str
    policy: Decision | None = None
    element: ElementFacts | None = None
    extracted: dict[str, str] = field(default_factory=dict)


class Discovery:
    """One discovery run. Build it, call run(), hand the Transcript to the compiler."""

    def __init__(
        self,
        spec: GoalSpec,
        params: Mapping[str, str],
        *,
        surface: Surface,
        policy: Policy,
        run_dir: RunDir,
        writer: EvidenceWriter,
        decider: Decider,
        base_url: str,
        secrets: Mapping[str, str] | None = None,
        escalation: Escalation | None = None,
        max_steps: int = MAX_STEPS,
        max_seconds: float = MAX_SECONDS,
    ) -> None:
        self.spec = spec
        self.params = dict(params)
        self.surface = surface
        self.policy = policy
        self.run_dir = run_dir
        self.writer = writer
        self.decider = decider
        self.base_url = base_url.rstrip("/")
        self.secrets = dict(os.environ) if secrets is None else dict(secrets)
        self.escalation: Escalation = Unattended() if escalation is None else escalation
        self.max_steps = max_steps
        self.max_seconds = max_seconds

        self._steps: list[TranscriptStep] = []
        self._extracted: dict[str, str] = {}
        self._asserted_since_last_action = False
        self._consecutive_blocks = 0
        self._unchanged_actions = 0
        self._last_screen: tuple[str, str] | None = None
        self._input_tokens = 0
        self._output_tokens = 0
        self._fingerprint: AppFingerprint | None = None

    # --- the run ---------------------------------------------------------

    def run(self) -> Transcript:
        """Reach the start screen, then loop until done, stuck, blocked, or out of budget."""
        check_params(self.spec.inputs, self.params)
        recovery_steps = [step for recovery in self.spec.recoveries for step in recovery.steps]
        check_secrets(recovery_steps, self.secrets)
        started_at = datetime.now(UTC)
        self.writer.event(
            "discovery_started",
            goal=self.spec.goal,
            params=sorted(self.params),
            model=self.decider.model,
        )
        self.surface.start_trace()
        try:
            self._reach_start_screen()
            stop_reason, summary = self._loop()
        finally:
            self.surface.stop_trace(self.run_dir.trace_path)
        transcript = Transcript(
            run_id=self.run_dir.run_id,
            goal=self.spec.goal,
            params=dict(self.params),
            base_url=self.base_url,
            fingerprint=self._fingerprint,
            model=self.decider.model,
            sdk_versions={"anthropic": version("anthropic"), "playwright": version("playwright")},
            started_at=started_at,
            finished_at=datetime.now(UTC),
            steps=list(self._steps),
            stop_reason=stop_reason,
            summary=summary,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
        )
        self.writer.save_model("transcript", transcript)
        self.writer.keep_trace(True)
        self.writer.event(
            "discovery_finished",
            stop_reason=stop_reason.value,
            steps=len(self._steps),
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
        )
        return transcript

    def _reach_start_screen(self) -> None:
        """Log in with the declared recovery so the model never sees or types a credential."""
        runner = StepRunner(
            surface=self.surface,
            policy=self.policy,
            writer=self.writer,
            params=self.params,
            secrets=self.secrets,
            base_url=self.base_url,
            outputs={},
        )
        start_url = self.base_url + self.spec.start_path
        self.surface.act(ActionType.NAVIGATE, None, start_url, ACT_TIMEOUT_MS)
        for recovery in self.spec.recoveries:
            if self.surface.holds(recovery.matches, LOCATE_TIMEOUT_MS):
                failed = runner.run_recovery(recovery)
                if failed is not None:
                    raise DiscoveryCouldNotStart(f"{recovery.id}: {failed}")
                self.surface.act(ActionType.NAVIGATE, None, start_url, ACT_TIMEOUT_MS)
        # Recorded here, on the start screen, so replay compares like with like.
        actual = self.surface.fingerprint()
        self._fingerprint = AppFingerprint(
            title=actual.title,
            version=actual.version,
            screen_hashes={self.spec.start_path: actual.screen_hashes["current"]},
        )

    def _loop(self) -> tuple[StopReason, str]:
        deadline = time.monotonic() + self.max_seconds
        system = system_prompt(self.spec, self.params)
        messages: list[MessageParam] = []
        for index in range(self.max_steps):
            if time.monotonic() > deadline:
                return StopReason.TIMEOUT, "wall-clock budget exhausted"
            screenshot = self.run_dir.screenshot_path(f"step_{index}")
            observation = self.surface.observe(screenshot_to=screenshot)
            prefix = "Begin." if index == 0 else "Current page after that action."
            messages.append(perception_message(prefix, observation, screenshot))

            decided = self.decider.decide(system, messages)
            self._input_tokens += decided.input_tokens
            self._output_tokens += decided.output_tokens
            messages.append(tool_use_message(decided))

            applied = self._apply(decided.action, observation)
            messages.append(tool_result_message(decided, applied.detail))
            self._record(index, observation, decided, applied)

            if decided.action.action is ToolAction.DONE and applied.status == "ok":
                return StopReason.DONE, decided.action.summary or ""
            stop = self._escalate_if_needed(index, applied, screenshot)
            if stop is not None:
                return stop, applied.detail
        return StopReason.MAX_STEPS, "step budget exhausted"

    # --- applying one proposal -------------------------------------------

    def _apply(self, action: ProposedAction, observation: Observation) -> Applied:
        if action.action is ToolAction.DONE:
            return self._apply_done()
        if action.action is ToolAction.ASSERT_STATE:
            return self._apply_assert(action)
        if action.action is ToolAction.EXTRACT:
            return self._apply_extract(action)
        return self._apply_act(action, observation)

    def _apply_done(self) -> Applied:
        missing = sorted(set(self.spec.outputs) - set(self._extracted))
        if missing:
            return Applied("rejected", f"done rejected: outputs not extracted yet: {missing}")
        if not self._asserted_since_last_action:
            return Applied("rejected", "done rejected: call assert_state first")
        return Applied("ok", "done accepted")

    def _apply_assert(self, action: ProposedAction) -> Applied:
        target = self._locate(action) if action.role and action.name else None
        if target is None and not action.text:
            return Applied("rejected", "assert_state needs a role and name that exist, or text")
        assertion = StateAssertion(
            description=action.description or action.reasoning,
            text_visible=action.text,
            target_visible=target,
        )
        if self.surface.holds(assertion, ASSERT_TIMEOUT_MS):
            self._asserted_since_last_action = True
            return Applied("holds", f"holds: {assertion.description}")
        return Applied("does_not_hold", f"does not hold: {assertion.description}")

    def _apply_extract(self, action: ProposedAction) -> Applied:
        name = action.output_name or ""
        if name not in self.spec.outputs:
            return Applied("rejected", f"extract rejected: {name!r} is not a declared output")
        target = self._locate(action)
        if target is None:
            return Applied(
                "failed", f"no element with role {action.role!r} and name {action.name!r}"
            )
        read = self.surface.read(target, ACT_TIMEOUT_MS)
        spec = OutputSpec(type=self.spec.outputs[name], extract=target)
        value = parse_output(name, spec, read.text)
        self._extracted[name] = value
        return Applied(
            "ok", f"extracted {name} = {value}", element=read.element, extracted={name: value}
        )

    def _apply_act(self, action: ProposedAction, observation: Observation) -> Applied:
        kind = ActionType(action.action.value)
        checked_url = (
            action.url or observation.url if kind is ActionType.NAVIGATE else observation.url
        )
        decision = self.policy.check(checked_url, kind, action.name)
        if not decision.allowed:
            self._consecutive_blocks += 1
            return Applied("blocked", f"blocked: {decision.reason}", policy=decision)
        if decision.risky:
            self._consecutive_blocks += 1
            return Applied(
                "blocked",
                f"blocked: {action.name!r} is classed as a risky action; a human must approve it",
                policy=decision,
            )
        self._consecutive_blocks = 0
        target: TargetRef | None = None
        if kind is not ActionType.NAVIGATE:
            target = self._locate(action)
            if target is None:
                return Applied(
                    "failed",
                    f"no element with role {action.role!r} and name {action.name!r} in any frame",
                    policy=decision,
                )
        value = action.text if kind is ActionType.TYPE else action.value
        if kind is ActionType.NAVIGATE:
            value = action.url
        try:
            result = self.surface.act(kind, target, value, ACT_TIMEOUT_MS)
        except ActionFailed as failed:
            return Applied("failed", f"failed: {failed.reason}", policy=decision)
        self._asserted_since_last_action = False
        return Applied(
            "ok", f"{kind.value} done on {action.name or action.url!r}", decision, result.element
        )

    def _locate(self, action: ProposedAction) -> TargetRef | None:
        """Find the model's role+name in whichever frame holds it; the model never names frames."""
        if not action.role or not action.name:
            return None
        candidate = Candidate(
            strategy=LocatorStrategy.ROLE_NAME,
            value=f"{action.role}:{action.name}",
            confidence=0.9,
            reasoning=action.reasoning,
        )
        for frame in self.surface.observe().frames:
            target = TargetRef(candidates=[candidate], frame_path=frame.frame_path)
            try:
                self.surface.resolve(target, LOCATE_TIMEOUT_MS)
            except (TargetNotFound, FrameNotFound):
                continue
            return target
        return None

    # --- bookkeeping -----------------------------------------------------

    def _record(
        self, index: int, observation: Observation, decided: Decided, applied: Applied
    ) -> None:
        self._steps.append(
            TranscriptStep(
                index=index,
                observation=observation,
                action=decided.action,
                policy=applied.policy,
                status=applied.status,
                detail=applied.detail,
                element=applied.element,
                extracted=applied.extracted,
                request_id=decided.request_id,
            )
        )
        self.writer.event(
            "discovery_step",
            index=index,
            action=decided.action.action.value,
            role=decided.action.role,
            name=decided.action.name,
            reasoning=decided.action.reasoning,
            status=applied.status,
            detail=applied.detail,
            request_id=decided.request_id,
        )
        # A screen that does not change across acted steps means the model is going in circles.
        if applied.status == "ok" and decided.action.action in (
            ToolAction.CLICK,
            ToolAction.TYPE,
            ToolAction.SELECT,
            ToolAction.NAVIGATE,
        ):
            after = self.surface.observe()
            screen = (after.url, after.frames[0].aria if after.frames else "")
            self._unchanged_actions = (
                self._unchanged_actions + 1 if screen == self._last_screen else 0
            )
            self._last_screen = screen

    def _escalate_if_needed(
        self, index: int, applied: Applied, screenshot: object
    ) -> StopReason | None:
        reason: InterventionReason | None = None
        if self._consecutive_blocks >= BLOCKS_BEFORE_ESCALATION:
            risky = applied.policy is not None and applied.policy.risky
            reason = (
                InterventionReason.RISKY_NEEDS_APPROVAL
                if risky
                else InterventionReason.STUCK_IN_DISCOVERY
            )
        elif self._unchanged_actions >= STUCK_AFTER_UNCHANGED_ACTIONS:
            reason = InterventionReason.STUCK_IN_DISCOVERY
        if reason is None:
            return None
        request = InterventionRequest(
            run_id=self.run_dir.run_id,
            capability_id=self.spec.capability_id,
            capability_version="0.0.0",
            goal=self.spec.goal,
            step_id=f"step_{index}",
            step_index=index,
            step_count=self.max_steps,
            expected="the model to make progress within policy",
            observed=applied.detail,
            reason=reason,
            screenshot=f"screenshots/step_{index}.png",
            log_tail=self._log_tail(),
            param_names=sorted(self.params),
        )
        self.writer.event("intervention_requested", step_id=request.step_id, reason=reason.value)
        decision = self.escalation.request(request)
        self.writer.event("intervention_answered", step_id=request.step_id, decision=decision.value)
        if decision is InterventionDecision.ABORT:
            return StopReason.INTERVENTION_ABORTED
        self._consecutive_blocks = 0
        self._unchanged_actions = 0
        return None

    def _log_tail(self) -> list[str]:
        events = read_events(self.run_dir)[-LOG_TAIL_LINES:]
        return [
            " ".join(f"{key}={value}" for key, value in event.items() if key != "ts")
            for event in events
        ]
