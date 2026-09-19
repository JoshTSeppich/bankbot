"""The control-transfer state machine and the RunController that holds it.

Owns: ControlState, the legal transitions, and RunController, which is the
one object the engine thread and the operator app share. The engine calls
request() (it is replay's Escalation hook) and blocks inside it until a
person answers; the operator app calls take_control, heartbeat, hand_back,
mark_step_done and abort from its own thread. One lock guards the state.

While the engine waits it does not sleep: it calls surface.idle, which
keeps the browser talking, and takes a fresh masked screenshot every second
for the operator page. Everything that happens on the page between the ask
and the answer is recorded as a person's action, because the engine itself
is idle for exactly that span.

The lease: while a person holds control their page pings every
HEARTBEAT_EVERY_S seconds. HEARTBEAT_MISSES missed pings in a row and the
engine aborts the run with reason OPERATOR_LOST rather than hold a bank
session open for nobody.

Does not own: the pages a person sees (control/operator.py) or the rules
that decide a request is needed (replay/).

Governed by ADR-0004 (control transfer).
"""

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum

from bankbot.evidence import EvidenceWriter, RunDir
from bankbot.schemas import Capability, InterventionDecision, InterventionRequest
from bankbot.surface import HumanAction, Surface

IDLE_MS = 200
LIVE_SCREENSHOT_EVERY_S = 1.0
LIVE_SCREENSHOT_NAME = "live"
HEARTBEAT_EVERY_S = 10.0
HEARTBEAT_MISSES = 3
OPERATOR_LOST = "operator_lost"
OPERATOR_CHOSE = "operator"


class ControlState(StrEnum):
    """Who owns the browser right now. Small enough to draw on a whiteboard."""

    AUTOMATION = "automation"
    INTERVENTION_REQUESTED = "intervention_requested"
    HUMAN = "human"
    RESUME_REQUESTED = "resume_requested"
    ABORTED = "aborted"
    FINISHED = "finished"


class IllegalTransition(Exception):
    """A button was pressed in a state where it means nothing. The page shows the state instead."""


Pump = Callable[[int], None]


class RunController:
    """One run's control state, shared between the engine thread and the operator app.

    `pump` is what the engine does while it waits; it defaults to
    surface.idle. Tests pass their own to play the person.
    `heartbeat_every_s` is the ping interval the operator page is told to
    keep; tests shrink it so a lost operator shows up in milliseconds.
    """

    def __init__(
        self,
        run_dir: RunDir,
        capability: Capability,
        param_names: list[str],
        *,
        surface: Surface,
        writer: EvidenceWriter,
        pump: Pump | None = None,
        heartbeat_every_s: float = HEARTBEAT_EVERY_S,
    ) -> None:
        self.run_dir = run_dir
        self.capability = capability
        self.param_names = param_names
        self.heartbeat_every_s = heartbeat_every_s
        self.started_at = datetime.now(UTC)
        self.request_pending: InterventionRequest | None = None
        self.human_actions: list[HumanAction] = []
        self.result_kind: str | None = None
        self.abort_reason: str | None = None
        self._surface = surface
        self._writer = writer
        self._pump: Pump = pump if pump is not None else surface.idle
        self._lock = threading.Lock()
        self._state = ControlState.AUTOMATION
        self._decision: InterventionDecision | None = None
        self._requested_at: float | None = None
        self._last_heartbeat: float | None = None

    @property
    def run_id(self) -> str:
        """The run this controller belongs to."""
        return self.run_dir.run_id

    @property
    def state(self) -> ControlState:
        """The current owner of the browser."""
        with self._lock:
            return self._state

    def waiting_seconds(self) -> float:
        """How long a person has been needed, for the queue's "Waiting" column."""
        with self._lock:
            if self._requested_at is None:
                return 0.0
            return time.monotonic() - self._requested_at

    # --- engine side (replay's Escalation protocol) ----------------------

    def request(self, request: InterventionRequest) -> InterventionDecision:
        """Hand the browser to a person and block until they hand it back or abort.

        Blocking here, inside the step loop, is what lets the engine continue
        from the same step afterwards.
        """
        with self._lock:
            self._state = ControlState.INTERVENTION_REQUESTED
            self._decision = None
            self._requested_at = time.monotonic()
            self.request_pending = request
        self._writer.event("control", state=ControlState.INTERVENTION_REQUESTED.value)
        self._surface.watch_human(self._record_human_action)
        try:
            decision = self._wait_for_decision()
        finally:
            self._surface.unwatch_human()
        with self._lock:
            if self._state is ControlState.RESUME_REQUESTED:
                self._state = ControlState.AUTOMATION
            self.request_pending = None
            self._requested_at = None
            final = self._state
        self._writer.event("control", state=final.value, decision=decision.value)
        return decision

    def finish(self, result_kind: str) -> None:
        """The run is over; the queue shows it as finished rather than dropping it."""
        with self._lock:
            self.result_kind = result_kind
            if self._state is not ControlState.ABORTED:
                self._state = ControlState.FINISHED

    # --- operator side ---------------------------------------------------

    def take_control(self) -> None:
        """A person takes the browser. Only meaningful while the engine is asking."""
        with self._lock:
            self._check(ControlState.INTERVENTION_REQUESTED, ControlState.HUMAN)
            self._state = ControlState.HUMAN
            self._last_heartbeat = time.monotonic()
        self._writer.event("control", state=ControlState.HUMAN.value)

    def heartbeat(self) -> None:
        """The person's page is still open. Ignored outside HUMAN: a stale ping is not an error."""
        with self._lock:
            if self._state is ControlState.HUMAN:
                self._last_heartbeat = time.monotonic()

    def hand_back(self) -> None:
        """The person fixed the page; the engine retries the step it stopped on."""
        self._move(ControlState.HUMAN, ControlState.RESUME_REQUESTED)
        self._decide(InterventionDecision.RESUME)

    def mark_step_done(self) -> None:
        """The person did the step themselves; the engine checks the page and moves on."""
        self._move(ControlState.HUMAN, ControlState.RESUME_REQUESTED)
        self._decide(InterventionDecision.STEP_DONE)

    def abort(self, reason: str = OPERATOR_CHOSE) -> None:
        """End the run as a Failure from any state that is not already final.

        The reason says who ended it: the operator, or the lease because the
        operator's page went away.
        """
        with self._lock:
            if self._state in (ControlState.ABORTED, ControlState.FINISHED):
                raise IllegalTransition(f"cannot abort a run that is {self._state.value}")
            self._state = ControlState.ABORTED
            self._decision = InterventionDecision.ABORT
            self.abort_reason = reason
        self._writer.event("control", state=ControlState.ABORTED.value, reason=reason)

    # --- private ---------------------------------------------------------

    def _wait_for_decision(self) -> InterventionDecision:
        last_screenshot = 0.0
        while True:
            state, decision = self._snapshot()
            if decision is not None:
                return decision
            if state is ControlState.HUMAN and self._lease_expired():
                self.abort(OPERATOR_LOST)
                continue
            if time.monotonic() - last_screenshot >= LIVE_SCREENSHOT_EVERY_S:
                self._surface.observe(
                    screenshot_to=self.run_dir.screenshot_path(LIVE_SCREENSHOT_NAME)
                )
                last_screenshot = time.monotonic()
            self._pump(IDLE_MS)

    def _lease_expired(self) -> bool:
        with self._lock:
            if self._last_heartbeat is None:
                return False
            silent_for = time.monotonic() - self._last_heartbeat
        return silent_for > self.heartbeat_every_s * HEARTBEAT_MISSES

    def _snapshot(self) -> tuple[ControlState, InterventionDecision | None]:
        with self._lock:
            return self._state, self._decision

    def _check(self, expected: ControlState, target: ControlState) -> None:
        # Caller holds the lock.
        if self._state is not expected:
            raise IllegalTransition(
                f"{target.value} needs {expected.value}, run is {self._state.value}"
            )

    def _move(self, expected: ControlState, target: ControlState) -> None:
        with self._lock:
            self._check(expected, target)
            self._state = target

    def _decide(self, decision: InterventionDecision) -> None:
        with self._lock:
            self._decision = decision

    def _record_human_action(self, action: HumanAction) -> None:
        self.human_actions.append(action)
        self._writer.event(
            "human_action",
            kind=action.kind,
            description=action.description,
            frame_path=action.frame_path,
            url=action.url,
        )


class RunRegistry:
    """The controllers the operator app can see, by run id. One process, one dict, one lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._controllers: dict[str, RunController] = {}

    def add(self, controller: RunController) -> None:
        """Make a run visible to the operator pages."""
        with self._lock:
            self._controllers[controller.run_id] = controller

    def get(self, run_id: str) -> RunController | None:
        """Look a run up; None is a page's 404."""
        with self._lock:
            return self._controllers.get(run_id)

    def all(self) -> list[RunController]:
        """Every run, oldest first, so the queue reads top to bottom."""
        with self._lock:
            return sorted(self._controllers.values(), key=lambda c: c.started_at)
