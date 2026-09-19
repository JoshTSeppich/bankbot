"""bankbot: record a browser workflow once with an LLM, then replay it without one.

The package is split by seam (see PLAN.md §2): target/ (the hostile demo app),
surface/ (the only module that knows Playwright), discover/ (LLM loop),
compile/ (transcript to artifact), replay/ (artifact execution), control/
(human handoff), policy/ (allowlist and risk) and evidence/ (run outputs).
schemas/ holds the typed objects that cross those seams.
"""
