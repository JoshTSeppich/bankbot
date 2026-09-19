"""Discover: let a model drive the app once, under policy, and record every step as typed data.

Owns: the goal spec the engineer writes, the loop that alternates observe
and act with one forced tool call per turn, the policy check in front of
every action, the stopping rules, and the Transcript the loop leaves
behind. Discovery is the only place a model is called.

Does not own: persistence (evidence/), perception (surface/), what is
allowed (policy/), or the artifact (compile/ turns the Transcript into one).

Governed by ADR-0005 (policy model), ADR-0004 (control transfer) and
ADR-0002 (locator strategy).
"""

from bankbot.discover.loop import Discovery, DiscoveryCouldNotStart
from bankbot.discover.model import ClaudeDecider, Decided, Decider
from bankbot.discover.spec import GoalSpec, load_goal_spec
from bankbot.discover.tools import ProposedAction, ToolAction
from bankbot.discover.transcript import StopReason, Transcript, TranscriptStep

__all__ = [
    "ClaudeDecider",
    "Decided",
    "Decider",
    "Discovery",
    "DiscoveryCouldNotStart",
    "GoalSpec",
    "ProposedAction",
    "StopReason",
    "ToolAction",
    "Transcript",
    "TranscriptStep",
    "load_goal_spec",
]
