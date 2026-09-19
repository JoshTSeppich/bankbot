"""The replay executor: walk the steps, apply the rules in order, return one result.

Owns: the step loop and the classification order. After every attempt the
same four questions are asked in the same order: did a known outcome match
(the run is over, and that is an answer); did a known recovery match (run
it, then retry); does the step's own on_fail allow another go; and if none
of those, a human is asked. Unattended runs answer that last question with
ABORT and the run ends as a Failure that carries the request.

Does not own: doing a step (replay/steps.py), perception (surface/), what
is allowed (policy/), how results reach disk (evidence/), or how a human
answers (control/).

Governed by ADR-0003 (error taxonomy), ADR-0002 (drift signal) and
ADR-0004 (control transfer).
"""

import os
from collections.abc import Mapping
from urllib.parse import urlparse

from bankbot.evidence import Event, EvidenceWriter, RunDir, read_events
from bankbot.policy import Policy
from bankbot.replay.escalation import Escalation, Unattended
from bankbot.replay.steps import STEP_TIMEOUT_MS, PolicyBlocked, StepFailed, StepRunner
from bankbot.replay.values import check_params, check_secrets
from bankbot.schemas import (
    ActionType,
    Capability,
    Evidence,
    Failure,
    InterventionDecision,
    InterventionReason,
    InterventionRequest,
    Outcome,
    Recover,
    Recovery,
    ReplayResult,
    ReplayWarning,
    Retry,
    Step,
    Success,
    WarningCode,
)
from bankbot.surface import Surface, distance

# Outcome and recovery matchers are a quick look at the page, not a wait:
# on the happy path they run after every step and must not slow it down.
MATCH_TIMEOUT_MS = 300
LOG_TAIL_LINES = 12
TRACE_FILE = "trace.zip"


class Replay:
    """One replay of one capability. Build it, call run(), read the result.

    The collaborators (surface, policy, evidence, escalation) are passed in
    so the CLI, the tests and the operator process all run the same loop
    with different edges.
    """

    def __init__(
        self,
        capability: Capability,
        params: Mapping[str, str],
        *,
        surface: Surface,
        policy: Policy,
        run_dir: RunDir,
        writer: EvidenceWriter,
        base_url: str,
        secrets: Mapping[str, str] | None = None,
        escalation: Escalation | None = None,
        keep_trace: bool = False,
        step_timeout_ms: int = STEP_TIMEOUT_MS,
    ) -> None:
        self.capability = capability
        self.params = dict(params)
        self.surface = surface
        self.policy = policy
        self.run_dir = run_dir
        self.writer = writer
        self.base_url = base_url.rstrip("/")
        self.secrets = dict(os.environ) if secrets is None else dict(secrets)
        self.escalation: Escalation = Unattended() if escalation is None else escalation
        self.keep_trace = keep_trace
        self.step_timeout_ms = step_timeout_ms
        self.steps = StepRunner(
            surface=surface,
            policy=policy,
            writer=writer,
            params=self.params,
            secrets=self.secrets,
            base_url=self.base_url,
            outputs=capability.outputs,
            approval=capability.approval,
            step_timeout_ms=step_timeout_ms,
        )
        self._outputs: dict[str, str] = {}
        self._warnings: list[ReplayWarning] = []
        self._recoveries_used: list[str] = []
        self._recovery_attempts: dict[str, int] = {}
        self._screens_checked: set[str] = set()

    # --- the run ---------------------------------------------------------

    def run(self) -> ReplayResult:
        """Validate inputs, drive the steps, and leave a complete evidence directory behind."""
        check_params(self.capability.inputs, self.params)
        recovery_steps = [
            step for recovery in self.capability.recoveries for step in recovery.steps
        ]
        check_secrets(self.capability.steps + recovery_steps, self.secrets)
        self.writer.event(
            Event.RUN_STARTED,
            capability=self.capability.id,
            version=self.capability.version,
            params=sorted(self.params),
            base_url=self.base_url,
        )
        self.surface.start_trace()
        try:
            result = self._execute()
        finally:
            self.surface.stop_trace(self.run_dir.trace_path)
        self.writer.save_result(result)
        self.writer.keep_trace(self.keep_trace or not isinstance(result, Success))
        self.writer.event(Event.RUN_FINISHED, kind=result.kind)
        return result

    def _execute(self) -> ReplayResult:
        self.surface.act(ActionType.NAVIGATE, None, self.base_url, self.step_timeout_ms)
        self._check_fingerprint()
        steps = self.capability.steps
        index = 0
        while index < len(steps):
            try:
                verdict = self._settle(steps[index], index)
            except PolicyBlocked as blocked:
                return self._failure(steps[index], "an action the policy allows", str(blocked))
            if not isinstance(verdict, int):
                return verdict
            index = verdict
        return self._finish()

    def _finish(self) -> ReplayResult:
        last = self.capability.steps[-1]
        checkpoint = self.capability.checkpoint
        if not self.surface.holds(checkpoint, self.step_timeout_ms):
            failed = StepFailed(
                checkpoint.description, self.steps.observed(), InterventionReason.CHECKPOINT_UNMET
            )
            decision, request = self._escalate(last, len(self.capability.steps) - 1, failed)
            if decision is InterventionDecision.ABORT:
                return self._failure(last, failed.expected, failed.observed, request)
            if not self.surface.holds(checkpoint, self.step_timeout_ms):
                return self._failure(last, failed.expected, self.steps.observed(), request)
        self.writer.event(Event.CHECKPOINT_PASSED, description=checkpoint.description)
        missing = sorted(set(self.capability.outputs) - set(self._outputs))
        if missing:
            return self._failure(last, f"outputs {missing} extracted", "no extract step read them")
        return Success(
            outputs=dict(self._outputs),
            evidence=self._evidence(trace_kept=self.keep_trace),
            recoveries_used=list(self._recoveries_used),
            warnings=list(self._warnings),
        )

    # --- one step, until it is settled -----------------------------------

    def _settle(self, step: Step, index: int) -> int | Outcome | Failure:
        """Apply the rules to one step until it passed, an outcome ended the run, or nobody helped.

        Returns the index of the next step to run when the step passed
        (a recovery may point elsewhere), otherwise the result that ends
        the run.
        """
        retries_left = step.on_fail.attempts if isinstance(step.on_fail, Retry) else 0
        while True:
            failure = self._try(step)
            outcome = self._matching_outcome()
            if outcome is not None:
                return outcome
            if failure is None:
                return index + 1

            recovery = self._matching_recovery(step)
            if recovery is not None:
                recovery_failure = self._recover(recovery)
                if recovery_failure is not None:
                    return recovery_failure
                if recovery.resume_from_step is not None:
                    return self._index_of(recovery.resume_from_step)
                continue

            if retries_left > 0:
                retries_left -= 1
                self.writer.event(Event.RETRY, step_id=step.id, remaining=retries_left)
                continue

            resolution = self._ask_human(step, index, failure)
            if resolution is None:
                continue
            return resolution

    def _try(self, step: Step) -> StepFailed | None:
        try:
            attempted = self.steps.attempt(step)
        except StepFailed as failed:
            self.writer.event(
                Event.STEP_FAILED,
                step_id=step.id,
                expected=failed.expected,
                observed=failed.observed,
            )
            return failed
        if attempted.output_name is not None and attempted.output_value is not None:
            self._outputs[attempted.output_name] = attempted.output_value
        self._note_drift(step, attempted.candidate_index)
        self._check_screen_fingerprint()
        return None

    def _ask_human(self, step: Step, index: int, failure: StepFailed) -> int | Failure | None:
        """Escalate and act on the answer. None means "try the step again"."""
        while True:
            decision, request = self._escalate(step, index, failure)
            if decision is InterventionDecision.ABORT:
                return self._failure(step, failure.expected, failure.observed, request)
            if decision is InterventionDecision.RESUME:
                if failure.reason is InterventionReason.RISKY_NEEDS_APPROVAL:
                    self.steps.approved_steps.add(step.id)
                return None
            # STEP_DONE: the human did the step; trust it only if the page agrees.
            if step.wait_for is None or self.surface.holds(step.wait_for, self.step_timeout_ms):
                return index + 1
            failure = StepFailed(
                step.wait_for.description,
                self.steps.observed(),
                InterventionReason.CHECKPOINT_UNMET,
            )

    # --- the rules -------------------------------------------------------

    def _matching_outcome(self) -> Outcome | None:
        for known in self.capability.outcomes:
            if self.surface.holds(known.matches, MATCH_TIMEOUT_MS):
                self.writer.event(Event.OUTCOME_MATCHED, code=known.code)
                return Outcome(
                    code=known.code,
                    message=known.message,
                    evidence=self._evidence(trace_kept=True),
                    recoveries_used=list(self._recoveries_used),
                    warnings=list(self._warnings),
                )
        return None

    def _matching_recovery(self, step: Step) -> Recovery | None:
        """A recovery applies when the page shows its condition, or the step names it on_fail."""
        for recovery in self.capability.recoveries:
            if self._attempts_left(recovery) and self.surface.holds(
                recovery.matches, MATCH_TIMEOUT_MS
            ):
                return recovery
        if isinstance(step.on_fail, Recover):
            for recovery in self.capability.recoveries:
                if recovery.id == step.on_fail.recovery_id and self._attempts_left(recovery):
                    return recovery
        return None

    def _attempts_left(self, recovery: Recovery) -> bool:
        return self._recovery_attempts.get(recovery.id, 0) < recovery.max_attempts

    def _recover(self, recovery: Recovery) -> Failure | None:
        self._recovery_attempts[recovery.id] = self._recovery_attempts.get(recovery.id, 0) + 1
        failed = self.steps.run_recovery(recovery)
        if failed is not None:
            return self._failure(recovery.steps[0], failed.expected, failed.observed)
        self._recoveries_used.append(recovery.id)
        return None

    def _escalate(
        self, step: Step, index: int, failure: StepFailed
    ) -> tuple[InterventionDecision, InterventionRequest]:
        request = InterventionRequest(
            run_id=self.run_dir.run_id,
            capability_id=self.capability.id,
            capability_version=self.capability.version,
            goal=self.capability.name,
            step_id=step.id,
            step_index=index,
            step_count=len(self.capability.steps),
            expected=failure.expected,
            observed=failure.observed,
            reason=failure.reason,
            screenshot=self._screenshot(f"intervention_{step.id}"),
            log_tail=self._log_tail(),
            param_names=sorted(self.params),
        )
        self.writer.event(
            Event.INTERVENTION_REQUESTED,
            step_id=step.id,
            reason=failure.reason.value,
            expected=failure.expected,
            observed=failure.observed,
        )
        decision = self.escalation.request(request)
        self.writer.event(Event.INTERVENTION_ANSWERED, step_id=step.id, decision=decision.value)
        return decision, request

    # --- signals that do not stop the run --------------------------------

    def _check_fingerprint(self) -> None:
        recorded = self.capability.app.fingerprint
        if recorded is None:
            return
        actual = self.surface.fingerprint()
        differences: list[str] = []
        if actual.title != recorded.title:
            differences.append(f"title {actual.title!r} != {recorded.title!r}")
        if actual.version != recorded.version:
            differences.append(f"version {actual.version!r} != {recorded.version!r}")
        if differences:
            self._warn(WarningCode.VARIANT_MISMATCH, None, "; ".join(differences))

    def _check_screen_fingerprint(self) -> None:
        """Measure a screen's tab sequence against the recording the first time replay lands on it.

        The number is logged every time; the warning is raised only when
        the policy's share of the sequence has changed.
        """
        recorded = self.capability.app.fingerprint
        if recorded is None or not recorded.screen_fingerprints:
            return
        path = self._screen_path(self.steps.url())
        if path not in recorded.screen_fingerprints or path in self._screens_checked:
            return
        self._screens_checked.add(path)
        expected = recorded.screen_fingerprints[path]
        actual = self.surface.fingerprint().screen_fingerprints["current"]
        edits = distance(expected, actual)
        length = max(len(expected), len(actual))
        self.writer.event(Event.SCREEN_COMPARED, screen=path, distance=edits, length=length)
        if edits > self.policy.fingerprint.max_distance_ratio * length:
            self._warn(
                WarningCode.VARIANT_MISMATCH,
                None,
                f"screen {path}: {edits} of {length} controls differ from the recording",
                distance=edits,
                length=length,
            )

    def _screen_path(self, url: str) -> str:
        """The path as the artifact names it: relative to the deployment, so /b/x is /x on B."""
        path = urlparse(url).path or "/"
        prefix = urlparse(self.base_url).path.rstrip("/")
        if prefix and path.startswith(prefix):
            path = path[len(prefix) :] or "/"
        return path

    def _note_drift(self, step: Step, candidate_index: int | None) -> None:
        if candidate_index is not None and candidate_index > 0:
            self._warn(
                WarningCode.DRIFT,
                step.id,
                f"candidate {candidate_index} resolved; earlier candidates did not",
            )

    def _warn(
        self,
        code: WarningCode,
        step_id: str | None,
        detail: str,
        distance: int | None = None,
        length: int | None = None,
    ) -> None:
        warning = ReplayWarning(
            code=code, step_id=step_id, detail=detail, distance=distance, length=length
        )
        self._warnings.append(warning)
        self.writer.event(
            Event.WARNING,
            code=code.value,
            step_id=step_id,
            detail=detail,
            distance=distance,
            length=length,
        )

    # --- small helpers ---------------------------------------------------

    def _failure(
        self, step: Step, expected: str, observed: str, request: InterventionRequest | None = None
    ) -> Failure:
        return Failure(
            step_id=step.id,
            expected=expected,
            observed=observed,
            intervention=request,
            evidence=self._evidence(trace_kept=True),
            recoveries_used=list(self._recoveries_used),
            warnings=list(self._warnings),
        )

    def _evidence(self, trace_kept: bool) -> Evidence:
        """Point at the run; the trace is named only when it will still be there afterwards."""
        return Evidence(
            run_id=self.run_dir.run_id,
            screenshot=self._screenshot("final"),
            trace=TRACE_FILE if trace_kept else None,
        )

    def _screenshot(self, name: str) -> str:
        """Take a masked screenshot and return its path relative to the run directory."""
        path = self.run_dir.screenshot_path(name)
        self.surface.observe(screenshot_to=path)
        return str(path.relative_to(self.run_dir.path))

    def _log_tail(self) -> list[str]:
        events = read_events(self.run_dir)[-LOG_TAIL_LINES:]
        return [
            " ".join(f"{key}={value}" for key, value in event.items() if key != "ts")
            for event in events
        ]

    def _index_of(self, step_id: str) -> int:
        for index, step in enumerate(self.capability.steps):
            if step.id == step_id:
                return index
        raise KeyError(step_id)
