"""Control: moving the browser between the automation and a person, and back.

Owns: the ControlState machine, the RunController the engine and the
operator pages share, the registry of live runs, the operator pages
(queue, run detail, finished record), and the heartbeat lease that ends a
run whose operator walked away. The engine blocks inside
RunController.request until a person answers, which is what lets it
continue from the same step. While a person holds control, their clicks,
edits and page changes are recorded with values masked.

Does not own: the browser (surface/), deciding when a person is needed
(replay/, discover/), or auth. One operator, no login: mocked and named
as such in the README.

Governed by ADR-0004 (control transfer).
"""

from bankbot.control.operator import create_operator_app
from bankbot.control.state import ControlState, IllegalTransition, RunController, RunRegistry

__all__ = [
    "ControlState",
    "IllegalTransition",
    "RunController",
    "RunRegistry",
    "create_operator_app",
]
