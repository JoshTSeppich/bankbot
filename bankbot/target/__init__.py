"""The hostile demo bank app that every other module is exercised against.

Owns: a small server-rendered "Legacy Core Teller" with the traits that break
naive automation: table layout, the search form inside an iframe, no ids or
test ids, a fake cookie login, member search to detail to savings balance, a
risky "Close account" button, and fault flags (session expiry, an unknown
modal, slow loads) that the admin endpoint can arm at run time. Variant B is
the same app under /b with a renamed button, an extra column and a different
version string, so drift and variant detection have something to detect.

Does not own: anything the automation knows about. No route, template or
attribute here exists because a locator wants it. The automation only ever
sees this app the way a human teller would. That rule is a claim, so it is
pinned: tests/target/test_hygiene.py checks that no element carries an id or
a test id, that every page lays itself out with tables, and that no page or
source file holds a nine- or sixteen-digit run.

Governed by ADR-0006 (surface abstraction): the app exists to give the
surface something hostile enough to prove the abstraction earns its keep.
"""

from bankbot.target.app import create_app
from bankbot.target.faults import Faults
from bankbot.target.server import ServerHandle, start_server

__all__ = ["Faults", "ServerHandle", "create_app", "start_server"]
