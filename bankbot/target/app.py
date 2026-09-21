"""The FastAPI app: fake login, member pages, and the admin endpoint for faults.

Owns: routes, the cookie session, and wiring faults into the page routes.
One router factory builds both variants from the same templates. Does not
own: the fixture data (members.py), the fault bookkeeping (faults.py), or
serving in a thread (server.py). Governed by ADR-0006.
"""

import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Form, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from bankbot.target.faults import Faults, FaultState
from bankbot.target.members import MEMBERS, Member, money
from bankbot.target.variants import VARIANT_A, VARIANT_B, Variant

TEMPLATES_DIR = Path(__file__).parent / "templates"
SESSION_COOKIE = "teller_session"
USERNAME_ENV = "BANKBOT_USERNAME"
PASSWORD_ENV = "BANKBOT_PASSWORD"
DEFAULT_USERNAME = "teller"
DEFAULT_PASSWORD = "teller-demo-password"
SEE_OTHER = 303
NOT_FOUND = 404
UNAUTHORIZED = 401


@dataclass(frozen=True)
class Credentials:
    """The one teller account the fake login accepts."""

    username: str
    password: str

    @classmethod
    def from_environment(cls) -> "Credentials":
        """Read the demo credentials with defaults, so a fresh clone logs in without a .env."""
        return cls(
            username=os.environ.get(USERNAME_ENV, DEFAULT_USERNAME),
            password=os.environ.get(PASSWORD_ENV, DEFAULT_PASSWORD),
        )


class SessionStore:
    """Server-side sessions behind an opaque cookie token.

    I keep sessions on the server rather than trusting the cookie so the
    session-expiry fault can end a session the way a real backend does: the
    browser still sends its cookie and the next page is the login form.
    """

    def __init__(self) -> None:
        self._username_by_token: dict[str, str] = {}

    def open(self, username: str) -> str:
        """Start a session and return the token the cookie will carry."""
        token = secrets.token_urlsafe(12)
        self._username_by_token[token] = username
        return token

    def username_for(self, token: str | None) -> str | None:
        """Who a cookie token belongs to, or None when it is missing, stale or forged."""
        if token is None:
            return None
        return self._username_by_token.get(token)

    def clear(self) -> None:
        """End every session; this is what the session-expiry fault does."""
        self._username_by_token.clear()


@dataclass(frozen=True)
class Shared:
    """State both variant routers share: one login, one fault counter, one template set."""

    templates: Jinja2Templates
    sessions: SessionStore
    faults: FaultState
    credentials: Credentials


@dataclass(frozen=True)
class Page:
    """What a counted page knows before rendering.

    username None means the route redirects to login. show_dialog means the
    injected "System notice" overlay rides along with this response.
    """

    username: str | None
    show_dialog: bool


def create_app(faults: Faults | None = None) -> FastAPI:
    """Build the app with variant A at / and variant B at /b.

    Faults default to whatever the environment says at startup so a make
    target can arm one; tests pass them explicitly.
    """
    shared = Shared(
        templates=Jinja2Templates(directory=TEMPLATES_DIR),
        sessions=SessionStore(),
        faults=FaultState(faults if faults is not None else Faults.from_environment()),
        credentials=Credentials.from_environment(),
    )
    app = FastAPI(title="Legacy Core Teller", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(build_admin_router(shared.faults))
    app.include_router(build_variant_router(VARIANT_A, shared))
    app.include_router(build_variant_router(VARIANT_B, shared))
    return app


def build_admin_router(faults: FaultState) -> APIRouter:
    """The out-of-band switch a test or make target uses to arm faults between runs."""
    router = APIRouter(prefix="/admin")

    @router.get("/faults")
    def read_faults() -> Faults:
        return faults.faults

    @router.post("/faults")
    def set_faults(body: Faults) -> Faults:
        faults.arm(body)
        return body

    @router.delete("/faults")
    def reset_faults() -> Faults:
        faults.arm(Faults())
        return faults.faults

    return router


def build_variant_router(variant: Variant, shared: Shared) -> APIRouter:
    """Build every page route for one variant.

    Called once per variant so B is the same code path with different
    context, not a second copy of the templates that could drift on its own.
    """
    templates = shared.templates
    sessions = shared.sessions
    faults = shared.faults

    def slow_load() -> None:
        # Every page on this variant waits when slow_load_ms is armed, so the
        # replay's bounded waits are exercised on real latency, not a mock.
        if faults.faults.slow_load_ms > 0:
            time.sleep(faults.faults.slow_load_ms / 1000)

    def signed_in_username(request: Request) -> str | None:
        return sessions.username_for(request.cookies.get(SESSION_COOKIE))

    def counted_page(request: Request) -> Page:
        # Count first, then apply the fault, then read the session: the Nth
        # counted request is the one whose session vanishes.
        effects = faults.count_request()
        if effects.expire_session:
            sessions.clear()
        return Page(username=signed_in_username(request), show_dialog=effects.show_dialog)

    def render(
        request: Request,
        name: str,
        page: Page,
        status_code: int = 200,
        **context: object,
    ) -> Response:
        return templates.TemplateResponse(
            request,
            name,
            {
                "variant": variant,
                "username": page.username,
                "show_dialog": page.show_dialog,
                "money": money,
                **context,
            },
            status_code=status_code,
        )

    def redirect(path: str) -> Response:
        return RedirectResponse(url=f"{variant.prefix}{path}", status_code=SEE_OTHER)

    def render_results(request: Request, page: Page, member_id: str) -> Response:
        member = MEMBERS.get(member_id)
        return render(request, "results.html", page, member_id=member_id, member=member)

    def render_missing(request: Request, page: Page, member_id: str) -> Response:
        return render(
            request,
            "results.html",
            page,
            status_code=NOT_FOUND,
            member_id=member_id,
            member=None,
        )

    router = APIRouter(prefix=variant.prefix, dependencies=[Depends(slow_load)])
    SignedIn = Annotated[str | None, Depends(signed_in_username)]  # noqa: N806
    Counted = Annotated[Page, Depends(counted_page)]  # noqa: N806

    @router.get("/")
    def home(username: SignedIn) -> Response:
        if username is None:
            return redirect("/login")
        return redirect("/members/search")

    @router.get("/login")
    def login_form(request: Request) -> Response:
        return render(request, "login.html", Page(username=None, show_dialog=False), error=None)

    @router.post("/login")
    def login(
        request: Request,
        username: Annotated[str, Form()],
        password: Annotated[str, Form()],
    ) -> Response:
        credentials = shared.credentials
        if username != credentials.username or password != credentials.password:
            return render(
                request,
                "login.html",
                Page(username=None, show_dialog=False),
                status_code=UNAUTHORIZED,
                error="Invalid username or password",
            )
        response = redirect("/members/search")
        response.set_cookie(SESSION_COOKIE, sessions.open(username), httponly=True)
        return response

    @router.get("/members/search")
    def search(request: Request, page: Counted) -> Response:
        if page.username is None:
            return redirect("/login")
        return render(request, "search.html", page)

    @router.get("/members/search-form")
    def search_form(request: Request, username: SignedIn) -> Response:
        # The browser fetches this for the iframe on its own; it is not counted
        # so fault step numbers follow what the automation did, not how the
        # page was fetched.
        if username is None:
            return redirect("/login")
        return render(request, "search_form.html", Page(username=username, show_dialog=False))

    @router.post("/members/results")
    def results_from_form(
        request: Request,
        page: Counted,
        member_id: Annotated[str, Form()],
    ) -> Response:
        if page.username is None:
            return redirect("/login")
        return render_results(request, page, member_id.strip())

    @router.get("/members/results")
    def results_from_query(request: Request, page: Counted, member_id: str = "") -> Response:
        if page.username is None:
            return redirect("/login")
        return render_results(request, page, member_id.strip())

    # ParaBank reaches a record by clicking its id in a list and labels values with a
    # plain cell; the shipped screens do neither, which is why neither shape was ever
    # tested. Nothing links here, so the recorded capability still sees the old pages.
    @router.get("/members/directory")
    def directory(request: Request, page: Counted) -> Response:
        if page.username is None:
            return redirect("/login")
        return render(
            request,
            "directory.html",
            page,
            title="Member directory",
            members=list(MEMBERS.values()),
        )

    @router.get("/members/profile")
    def profile(request: Request, page: Counted, member: str = "") -> Response:
        if page.username is None:
            return redirect("/login")
        found = MEMBERS.get(member.strip())
        if found is None:
            return render_missing(request, page, member.strip())
        return render(request, "profile.html", page, title="Member profile", member=found)

    @router.get("/members/{member_id}")
    def member_detail(request: Request, page: Counted, member_id: str) -> Response:
        if page.username is None:
            return redirect("/login")
        member: Member | None = MEMBERS.get(member_id)
        if member is None:
            return render_missing(request, page, member_id)
        return render(request, "member.html", page, member=member)

    @router.post("/members/{member_id}/close")
    def close_account(request: Request, page: Counted, member_id: str) -> Response:
        # Renders the confirmation and changes nothing: the point is that the
        # policy stops the automation before it gets here, not what happens after.
        if page.username is None:
            return redirect("/login")
        member = MEMBERS.get(member_id)
        if member is None:
            return render_missing(request, page, member_id)
        return render(request, "closed.html", page, member=member)

    return router
