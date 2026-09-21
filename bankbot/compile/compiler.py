"""Transcript plus goal spec in, Capability out. No model involved.

Owns: the rules that turn what the model did once into something that can
run many times. Typed values that equal a parameter become ParamRefs.
Every acted element becomes a ranked TargetRef. Intermediate assert_states
become the preceding step's wait_for; the last one becomes the checkpoint.
URL changes the model caused become wait_for patterns with the parameter
values generalised. Known outcomes and recoveries come from the spec,
because one run shows the model only the happy path.

Generalising a URL is string replacement over the recorded path, so a
parameter value that appears anywhere in it becomes a wildcard, not only in
the segment that is the record. On this app M-100 appears once; on an app
that numbers branches the same way, a member id inside a branch segment would
be generalised too. The result is a wait_for that is looser than it should
be, never one that is tighter, so it costs a failed assertion that would have
caught something rather than a wrong answer.

Does not own: talking to the model (discover/) or executing the result
(replay/).

Governed by ADR-0001 (artifact schema) and ADR-0002 (locator strategy).
"""

import re
from collections.abc import Mapping
from urllib.parse import urlparse

from bankbot.compile.candidates import names_a_recorded_value, target_from_facts
from bankbot.discover import GoalSpec, ProposedAction, StopReason, ToolAction, Transcript
from bankbot.discover.transcript import TranscriptStep
from bankbot.schemas import (
    ActionType,
    AppRef,
    Candidate,
    Capability,
    LiteralValue,
    LocatorStrategy,
    OutputRef,
    OutputSpec,
    ParamRef,
    Recovery,
    Retry,
    RunProvenance,
    StateAssertion,
    Step,
    TargetRef,
    inputs_never_used,
)
from bankbot.surface import ElementFacts

FIRST_VERSION = "1.0.0"
NAVIGATION_RETRIES = 2
OPEN_STEP_ID = "open_start"
SENTENCE_WITHHELD = "The model's sentence for this step named a recorded value and was dropped."
ACTED = (ToolAction.CLICK, ToolAction.TYPE, ToolAction.SELECT, ToolAction.NAVIGATE)


class TranscriptNotCompilable(Exception):
    """The run did not end with `done`; there is no happy path to compile."""


class NoCheckpointAsserted(Exception):
    """The model never asserted a final state, so replay would have nothing to verify."""


class InputNeverUsed(Exception):
    """A declared input no step reads. The capability would ignore its caller."""


class SecretLeakedIntoTranscript(Exception):
    """A typed value equals a secret. The transcript is not compiled, and should be destroyed."""


class InputNamedControlHasNoRole(Exception):
    """A control named by an input's value has no role, so no candidate can name that input."""


def compile_capability(
    transcript: Transcript, spec: GoalSpec, secrets: Mapping[str, str] | None = None
) -> Capability:
    """Build the artifact. Raises rather than guessing when the run cannot be trusted."""
    if transcript.stop_reason is not StopReason.DONE:
        raise TranscriptNotCompilable(f"run stopped with {transcript.stop_reason.value}")
    secret_values = {value for value in (secrets or {}).values() if value}
    steps = [OpenStart(spec).step()]
    outputs: dict[str, OutputSpec] = {}
    checkpoint: StateAssertion | None = None
    kept = [step for step in transcript.steps if step.status in ("ok", "holds")]
    recorded_values = _recorded_values(kept, transcript)

    for position, recorded in enumerate(kept):
        action = recorded.action
        following = kept[position + 1] if position + 1 < len(kept) else None
        if (
            action.action in ACTED and recorded.element is not None
        ) or action.action is ToolAction.NAVIGATE:
            step = _acted_step(
                recorded, following, transcript, secret_values, steps, recorded_values
            )
            steps.append(step)
        elif action.action is ToolAction.EXTRACT and recorded.element is not None:
            name = action.output_name or ""
            outputs[name] = OutputSpec(
                type=spec.outputs[name],
                extract=target_from_facts(
                    recorded.element,
                    _model_sentence(action.reasoning, recorded_values),
                    for_output=True,
                    recorded_values=recorded_values,
                ),
            )
            steps.append(
                Step(
                    id=_unique(f"read_{name}", steps),
                    action=ActionType.EXTRACT,
                    value=OutputRef(output=name),
                )
            )
        elif action.action is ToolAction.ASSERT_STATE:
            checkpoint = _assertion(action, recorded, recorded_values)

    if checkpoint is None:
        raise NoCheckpointAsserted("no assert_state held during the run")
    recovery_steps = [step for recovery in spec.recoveries for step in recovery.steps]
    unused = inputs_never_used(spec.inputs, steps + recovery_steps)
    if unused:
        raise InputNeverUsed(f"input {', '.join(unused)} was never used")
    first_step_id = steps[0].id
    return Capability(
        id=spec.capability_id,
        name=spec.name,
        version=FIRST_VERSION,
        app=AppRef(
            vendor=spec.app.vendor,
            app_id=spec.app.app_id,
            variant=spec.app.variant,
            fingerprint=transcript.fingerprint,
        ),
        created_from_run=RunProvenance(
            run_id=transcript.run_id,
            model=transcript.model,
            sdk_versions=transcript.sdk_versions,
            started_at=transcript.started_at,
            finished_at=transcript.finished_at,
            request_ids=transcript.request_ids,
        ),
        inputs=dict(spec.inputs),
        outputs=outputs,
        preconditions=list(spec.preconditions),
        steps=steps,
        checkpoint=checkpoint,
        outcomes=list(spec.outcomes),
        recoveries=[_resuming_from(recovery, first_step_id) for recovery in spec.recoveries],
    )


class OpenStart:
    """The one step the model never took: replay begins from a fresh browser, discovery did not."""

    def __init__(self, spec: GoalSpec) -> None:
        self.spec = spec

    def step(self) -> Step:
        """Navigate to the start path. Being signed in is a precondition, checked before this."""
        return Step(
            id=OPEN_STEP_ID,
            action=ActionType.NAVIGATE,
            value=LiteralValue(literal=self.spec.start_path),
            wait_for=StateAssertion(
                description=f"The start screen {self.spec.start_path} is shown",
                url_pattern=re.escape(self.spec.start_path) + "$",
            ),
        )


def _acted_step(
    recorded: TranscriptStep,
    following: TranscriptStep | None,
    transcript: Transcript,
    secret_values: set[str],
    so_far: list[Step],
    recorded_values: list[str],
) -> Step:
    action = recorded.action
    kind = ActionType(action.action.value)
    element = recorded.element
    input_name = _input_named(element, transcript) if element is not None else None
    if element is not None and input_name is not None and not element.role:
        # The templated candidate is spelled `role:{input:name}`, so with no
        # role there is nothing to spell it with. The general ranking would
        # hand back a structural path to the recorded record and say nothing
        # about the input being dropped, and a quiet wrong answer on the next
        # caller is worse than refusing the transcript.
        raise InputNamedControlHasNoRole(
            f"the control named by input {input_name} has no role; it cannot be located"
        )
    name_is_data = (
        element is not None and input_name is None and _names_a_record(element, transcript)
    )
    target = (
        target_from_facts(
            element,
            _model_sentence(action.reasoning, recorded_values),
            input_name=input_name,
            name_is_data=name_is_data,
            recorded_values=recorded_values,
        )
        if element is not None
        else None
    )
    wait_for = _wait_after(recorded, following, transcript, recorded_values)
    step_id = _unique(
        f"{kind.value}_{_slug(_step_words(action, element, input_name, name_is_data, kind))}",
        so_far,
    )
    step = Step(id=step_id, action=kind, target=target, wait_for=wait_for)
    if kind is ActionType.TYPE:
        step = step.model_copy(
            update={"value": _typed_value(action.text or "", transcript, secret_values)}
        )
    elif kind is ActionType.SELECT:
        step = step.model_copy(
            update={"value": _typed_value(action.value or "", transcript, secret_values)}
        )
    elif kind is ActionType.NAVIGATE:
        step = step.model_copy(
            update={"value": LiteralValue(literal=_path_of(action.url or "", transcript))}
        )
    if kind is ActionType.CLICK and wait_for is not None and wait_for.url_pattern is not None:
        # A click that navigates is where slow loads bite; a bounded retry covers them.
        step = step.model_copy(update={"on_fail": Retry(retries=NAVIGATION_RETRIES)})
    return step


def _step_words(
    action: ProposedAction,
    element: ElementFacts | None,
    input_name: str | None,
    name_is_data: bool,
    kind: ActionType,
) -> str:
    """The words a step is named after: the application's own, never a record's.

    A step id is read in the artifact, in every log line and in the event
    sequence hash, so a control named after a member puts that member in all
    three. The two cases where the name is data are the two the compiler
    already tells apart for the locator. An input-named control takes the
    input's name, which says the same thing about what was clicked and
    belongs to the capability rather than to the caller. A record-named
    control takes its role, because what it is survives the recording and
    what it says does not; a second one of those gets a number from _unique.
    """
    if input_name is not None:
        return input_name
    if name_is_data:
        return (element.role if element is not None and element.role else "") or kind.value
    return action.name or action.url or kind.value


def _input_named(element: ElementFacts, transcript: Transcript) -> str | None:
    """The input whose value is this control's accessible name, if there is one.

    Asked before _names_a_record because on ParaBank the record id was also
    printed on the start screen, so "it was there before, it is part of the
    app" was the wrong answer and every caller got the recorded account.
    """
    if not element.name:
        return None
    for name, value in transcript.params.items():
        if value and element.name == value:
            return name
    return None


def _names_a_record(element: ElementFacts, transcript: Transcript) -> bool:
    """A link whose text was not on the start screen is a record the search brought up.

    Links are where records live in a table-based app; buttons and fields
    keep their names between visits. Anything that was on the start screen
    before a parameter was typed is part of the app, not of the data.
    """
    if element.role != "link" or not element.name:
        return False
    start = transcript.steps[0].observation if transcript.steps else None
    if start is None:
        return False
    return not any(element.name in frame.aria for frame in start.frames)


def _typed_value(
    text: str, transcript: Transcript, secret_values: set[str]
) -> ParamRef | LiteralValue:
    """Parameters by name, never by value; a secret in the transcript is a hard stop."""
    if text in secret_values:
        raise SecretLeakedIntoTranscript("a typed value equals a secret; refusing to compile")
    for name, value in transcript.params.items():
        if text == value:
            return ParamRef(param=name)
    return LiteralValue(literal=text)


def _wait_after(
    recorded: TranscriptStep,
    following: TranscriptStep | None,
    transcript: Transcript,
    recorded_values: list[str],
) -> StateAssertion | None:
    """What the page looked like after the action: a URL change, an asserted state, or both."""
    url_pattern: str | None = None
    if following is not None:
        before = urlparse(recorded.observation.url).path
        after = urlparse(following.observation.url).path
        if after != before:
            url_pattern = _generalise(after, transcript) + "$"
    asserted = (
        _assertion(following.action, following, recorded_values)
        if following is not None and following.action.action is ToolAction.ASSERT_STATE
        else None
    )
    if asserted is None and url_pattern is None:
        return None
    if asserted is None:
        return StateAssertion(
            description=f"The page moved to {url_pattern}", url_pattern=url_pattern
        )
    return asserted.model_copy(update={"url_pattern": url_pattern or asserted.url_pattern})


def _assertion(
    action: ProposedAction, recorded: TranscriptStep, recorded_values: list[str]
) -> StateAssertion:
    """The model's claim as a checkable assertion, with any recorded value taken out of it.

    A checkpoint that names today's balance would only ever pass for the
    recorded member. The value is stripped and what remains ("Savings
    balance") is asserted as visible text instead.
    """
    target: TargetRef | None = None
    name = action.name or ""
    for value in recorded_values:
        if value and value in name:
            remaining = name.replace(value, "").strip() or action.text
            return StateAssertion(
                description=_claim(action, remaining, recorded_values),
                text_visible=remaining,
            )
    if action.role and action.name:
        target = TargetRef(
            candidates=[
                Candidate(
                    strategy=LocatorStrategy.ROLE_NAME,
                    value=f"{action.role}:{action.name}",
                    confidence=0.9,
                    reasoning=_model_sentence(action.reasoning, recorded_values),
                )
            ],
            frame_path=_frame_holding(recorded, action.name),
        )
    return StateAssertion(
        description=_claim(action, action.text, recorded_values),
        text_visible=action.text,
        target_visible=target,
    )


def _claim(action: ProposedAction, checked: str | None, recorded_values: list[str]) -> str:
    """What the assertion says it checks, in the model's words unless they carry a value.

    The model writes "Member M-100's profile shows Savings balance
    $4,242.00". Stripping the value out of what is checked and leaving it in
    the sentence beside it puts a member's balance in a reusable artifact.
    """
    described = action.description or action.reasoning
    if not names_a_recorded_value(described, recorded_values):
        return described
    return f"The page shows {checked!r}" if checked else "The page reached the recorded state"


def _model_sentence(reasoning: str, recorded_values: list[str]) -> str:
    """The model's reason for a step, unless it quotes a value this run was given or read."""
    if names_a_recorded_value(reasoning, recorded_values):
        return SENTENCE_WITHHELD
    return reasoning


def _recorded_values(kept: list[TranscriptStep], transcript: Transcript) -> list[str]:
    """Every value this run was given or read: the caller's parameters and the extracted text.

    One list, because the artifact must not carry either of them, and the
    rules that keep them out ask the same question of both.
    """
    extracted = [
        step.element.text
        for step in kept
        if step.action.action is ToolAction.EXTRACT
        and step.element is not None
        and step.element.text
    ]
    return [*(value for value in transcript.params.values() if value), *extracted]


def _frame_holding(recorded: TranscriptStep, name: str) -> list[str]:
    for frame in recorded.observation.frames:
        if name in frame.aria:
            return list(frame.frame_path)
    return []


def _generalise(path: str, transcript: Transcript) -> str:
    """Escape the path, then put a wildcard where a parameter value appeared."""
    pattern = re.escape(path)
    for value in sorted(transcript.params.values(), key=len, reverse=True):
        if value:
            pattern = pattern.replace(re.escape(value), "[^/]+")
    return pattern


def _path_of(url: str, transcript: Transcript) -> str:
    """Artifacts store paths; the base URL is a property of the deployment, not the recording."""
    if url.startswith(transcript.base_url):
        return url[len(transcript.base_url) :] or "/"
    return urlparse(url).path or "/"


def _resuming_from(recovery: Recovery, first_step_id: str) -> Recovery:
    """After a recovery the flow restarts from the top: every step here is a read, so it is safe."""
    if recovery.resume_from_step is not None:
        return recovery
    return recovery.model_copy(update={"resume_from_step": first_step_id})


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:32] or "step"


def _unique(step_id: str, so_far: list[Step]) -> str:
    taken = {step.id for step in so_far}
    if step_id not in taken:
        return step_id
    suffix = 2
    while f"{step_id}_{suffix}" in taken:
        suffix += 1
    return f"{step_id}_{suffix}"
