"""The operator pages: a queue of runs and one page per run to take over and hand back.

Owns: the FastAPI routes and the view-model they hand to two Jinja
templates. Every button is a POST that calls one RunController method and
redirects back; the page then shows whatever state the run is in. The
detail page refreshes itself every two seconds while a run is live, and
the "live view" is the masked screenshot the engine takes every second
while it waits. The only script is the heartbeat: while a person holds
control the page pings every HEARTBEAT_EVERY_S seconds so the engine knows
someone is still there.

Does not own: control state (control/state.py), the browser, or auth.
There is no login and one operator; both are named as mocked in the README.

Governed by ADR-0004 (control transfer).
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bankbot.control.state import (
    ControlState,
    IllegalTransition,
    RunController,
    RunRegistry,
)
from bankbot.evidence import RunDir, read_events
from bankbot.schemas import InterventionReason

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
LOG_TAIL = 12
SEE_OTHER = 303
NO_CONTENT = 204

STATE_LABELS = {
    ControlState.AUTOMATION: ("Automation running", "green"),
    ControlState.INTERVENTION_REQUESTED: ("Waiting for a person", "amber"),
    ControlState.HUMAN: ("You are in control", "blue"),
    ControlState.RESUME_REQUESTED: ("Handing back", "grey"),
    ControlState.ABORTED: ("Aborted", "red"),
    ControlState.FINISHED: ("Finished", "green"),
}

# One sentence for the queue, one paragraph for the detail page, per reason.
REASON_COPY = {
    InterventionReason.UNKNOWN_DIALOG: (
        "recoverable",
        "A dialog the automation has never seen is covering the page.",
        "The automation never dismisses a dialog it does not recognise. Dismiss it "
        "yourself, then hand back and it retries the step it stopped on.",
    ),
    InterventionReason.CANDIDATE_EXHAUSTED: (
        "needs a fix",
        "It could not find the control this step needs.",
        "None of the recorded ways of finding the control matched. Do the step "
        "yourself and mark it complete, or abort and re-record the capability.",
    ),
    InterventionReason.CHECKPOINT_UNMET: (
        "recoverable",
        "The page did not reach the state this step expects.",
        "Look at the page. If you can get it to the expected state, hand back and "
        "the automation retries; if you did the step yourself, mark it complete.",
    ),
    InterventionReason.RISKY_NEEDS_APPROVAL: (
        "needs approval",
        "The next step changes a member's account. Someone has to approve it.",
        "Hand back to approve this one step for this run only. Abort if it should not happen.",
    ),
    InterventionReason.STUCK_IN_DISCOVERY: (
        "stuck",
        "The model stopped making progress.",
        "Discovery was going in circles or kept proposing blocked actions. Abort, "
        "or hand back to let it try again.",
    ),
}


def create_operator_app(registry: RunRegistry, runs_dir: Path | None = None) -> FastAPI:
    """Build the pages over the live runs, plus finished runs on disk when given a directory."""
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    app = FastAPI(title="BankBot operator", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/operator/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(_routes(registry, runs_dir, templates))
    return app


def _routes(registry: RunRegistry, runs_dir: Path | None, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/operator")

    def controller_or_404(run_id: str) -> RunController:
        controller = registry.get(run_id)
        if controller is None:
            raise HTTPException(status_code=404, detail=f"no live run {run_id}")
        return controller

    def back_to(run_id: str) -> Response:
        return RedirectResponse(url=f"/operator/{run_id}", status_code=SEE_OTHER)

    @router.get("")
    def queue(request: Request) -> Response:
        rows = [_queue_row(controller) for controller in registry.all()]
        rows.extend(_finished_rows_on_disk(runs_dir, {row["run_id"] for row in rows}))
        waiting = sum(1 for row in rows if row["waiting"])
        return templates.TemplateResponse(
            request, "queue.html", {"rows": rows, "waiting": waiting, "now": _clock()}
        )

    @router.get("/{run_id}")
    def detail(request: Request, run_id: str) -> Response:
        controller = registry.get(run_id)
        if controller is None:
            return _finished_detail(request, run_id, runs_dir, templates)
        return templates.TemplateResponse(request, "detail.html", _detail_view(controller))

    @router.get("/{run_id}/live.png")
    def live(run_id: str) -> Response:
        controller = controller_or_404(run_id)
        path = controller.run_dir.screenshot_path("live")
        if not path.exists():
            raise HTTPException(status_code=404, detail="no screenshot yet")
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    # {name:path}, not {name}: the screenshots a finished run links to live in
    # a subdirectory, and a plain path parameter stops at the first slash.
    @router.get("/{run_id}/files/{name:path}")
    def run_file(run_id: str, name: str) -> Response:
        run_dir = _run_dir(registry, runs_dir, run_id)
        path = (run_dir.path / name).resolve()
        if run_dir.path.resolve() not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="no such file in this run")
        return FileResponse(path)

    @router.post("/{run_id}/take")
    def take(run_id: str) -> Response:
        _try(controller_or_404(run_id).take_control)
        return back_to(run_id)

    @router.post("/{run_id}/hand-back")
    def hand_back(run_id: str) -> Response:
        _try(controller_or_404(run_id).hand_back)
        return back_to(run_id)

    @router.post("/{run_id}/complete")
    def complete(run_id: str) -> Response:
        _try(controller_or_404(run_id).mark_step_done)
        return back_to(run_id)

    @router.post("/{run_id}/abort")
    def abort(run_id: str) -> Response:
        _try(controller_or_404(run_id).abort)
        return back_to(run_id)

    @router.post("/{run_id}/heartbeat")
    def heartbeat(run_id: str) -> Response:
        controller_or_404(run_id).heartbeat()
        return Response(status_code=NO_CONTENT)

    return router


def _try(transition: Any) -> None:
    # A stale button (two tabs, a double click) is not an error the operator can act on;
    # the redirected page shows the real state.
    try:
        transition()
    except IllegalTransition:
        return


def _queue_row(controller: RunController) -> dict[str, Any]:
    request = controller.request_pending
    label, colour = STATE_LABELS[controller.state]
    reason = REASON_COPY[request.reason][1] if request else label
    stopped = (
        f"Step {request.step_index + 1} of {request.step_count} · {request.step_id}"
        if request
        else "-"
    )
    return {
        "run_id": controller.run_id,
        "capability": controller.capability.name,
        "capability_id": f"{controller.capability.id} {controller.capability.version}",
        "app": f"{controller.capability.app.vendor} · {controller.capability.app.app_id}",
        "stopped_at": stopped,
        "why": reason,
        "waiting": controller.state in (ControlState.INTERVENTION_REQUESTED, ControlState.HUMAN),
        "waiting_for": _duration(controller.waiting_seconds()),
        "colour": colour,
        "live": True,
    }


def _detail_view(controller: RunController) -> dict[str, Any]:
    request = controller.request_pending
    state = controller.state
    label, colour = STATE_LABELS[state]
    kind, headline, explanation = REASON_COPY[request.reason] if request else ("", "", "")
    return {
        "run_id": controller.run_id,
        "state": state.value,
        "state_label": label,
        "state_colour": colour,
        "live": state not in (ControlState.ABORTED, ControlState.FINISHED),
        "can_take": state is ControlState.INTERVENTION_REQUESTED,
        "is_human": state is ControlState.HUMAN,
        "is_busy": state in (ControlState.AUTOMATION, ControlState.RESUME_REQUESTED),
        "capability": controller.capability,
        "app": f"{controller.capability.app.vendor} · {controller.capability.app.app_id}",
        # Named "intervention", not "request": Starlette owns "request" in every template context.
        "intervention": request,
        "step_label": f"Step {request.step_index + 1} of {request.step_count} · {request.step_id}"
        if request
        else "",
        "reason_kind": kind,
        "reason_headline": headline,
        "reason_explanation": explanation,
        "started": controller.started_at.strftime("%H:%M:%S"),
        "param_names": controller.param_names,
        "secret_names": sorted(_secret_names(controller)),
        "log": _log_rows(controller.run_dir),
        "human_actions": controller.human_actions,
        "result_kind": controller.result_kind,
        "abort_reason": controller.abort_reason,
        "heartbeat_ms": int(controller.heartbeat_every_s * 1000),
        "now": _clock(),
    }


def _finished_detail(
    request: Request, run_id: str, runs_dir: Path | None, templates: Jinja2Templates
) -> Response:
    """A run that is over and only exists on disk: the record, without buttons."""
    if runs_dir is None or not (runs_dir / run_id).is_dir():
        raise HTTPException(status_code=404, detail=f"no run {run_id}")
    run_dir = RunDir.open(runs_dir / run_id)
    return templates.TemplateResponse(
        request,
        "finished.html",
        {
            "run_id": run_id,
            "result": _result_on_disk(run_dir),
            "log": _log_rows(run_dir, limit=200),
            "files": sorted(p.name for p in run_dir.path.iterdir() if p.is_file()),
            "screenshots": sorted(p.name for p in run_dir.screenshots_dir.glob("*.png"))
            if run_dir.screenshots_dir.is_dir()
            else [],
        },
    )


def _finished_rows_on_disk(runs_dir: Path | None, skip: set[str]) -> list[dict[str, Any]]:
    if runs_dir is None or not runs_dir.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(p for p in runs_dir.iterdir() if p.is_dir() and p.name not in skip):
        result = _result_on_disk(RunDir.open(path))
        kind = str(result.get("kind", "no result")) if result else "no result"
        rows.append(
            {
                "run_id": path.name,
                "capability": "",
                "capability_id": "",
                "app": "",
                "stopped_at": "-",
                "why": f"finished: {kind}",
                "waiting": False,
                "waiting_for": "",
                "colour": "green" if kind == "success" else "grey",
                "live": False,
            }
        )
    return rows


def _run_dir(registry: RunRegistry, runs_dir: Path | None, run_id: str) -> RunDir:
    controller = registry.get(run_id)
    if controller is not None:
        return controller.run_dir
    if runs_dir is not None and (runs_dir / run_id).is_dir():
        return RunDir.open(runs_dir / run_id)
    raise HTTPException(status_code=404, detail=f"no run {run_id}")


def _result_on_disk(run_dir: RunDir) -> dict[str, Any]:
    if not run_dir.result_path.exists():
        return {}
    loaded: dict[str, Any] = json.loads(run_dir.result_path.read_text())
    return loaded


def _log_rows(run_dir: RunDir, limit: int = LOG_TAIL) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for event in read_events(run_dir)[-limit:]:
        timestamp = str(event.get("ts", ""))
        fields = {k: v for k, v in event.items() if k not in ("ts", "event")}
        rows.append(
            {
                "time": timestamp[11:19],
                "event": str(event.get("event", "")),
                "detail": " ".join(f"{k}={v}" for k, v in fields.items()),
            }
        )
    return rows


def _secret_names(controller: RunController) -> set[str]:
    names: set[str] = set()
    steps = list(controller.capability.steps)
    for recovery in controller.capability.recoveries:
        steps.extend(recovery.steps)
    for step in steps:
        secret = getattr(step.value, "secret", None)
        if isinstance(secret, str):
            names.add(secret)
    return names


def _duration(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 60}m {whole % 60:02d}s"


def _clock() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S")
