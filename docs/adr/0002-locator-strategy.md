# ADR-0002: Locator strategy

Status: Accepted
Date: 2026-09-19

## Context

The target is a hostile legacy web app: table layouts, an iframe, no ids or
test ids, and markup that may shift between visits. Replay must find the same
control a human would find, without an LLM, and must degrade predictably
rather than silently clicking the wrong thing.

The brief asks explicitly for "how each control is identified" and
"robustness reasoning". Discovery observes the page through a Playwright
accessibility snapshot whose element references are ephemeral, so whatever
the artifact persists must be reconstructed into durable, human-meaningful
targets at compile time.

Drift is the long-term risk: a target that resolves today may resolve
differently after a vendor UI change, and the system needs an early signal
before a hard failure.

## Decision

Ranked candidates, not one selector. Every control in an artifact is a
`TargetRef`: an ordered list of ways to find it, tried in order, and the
frame it lives in. A later candidate winning is the drift signal.

| Decision | Choice | Why |
|---|---|---|
| Order | role+name, label, row header, exact text, structural CSS path, bounding box | Most to least durable. Role and accessible name is how a person names a control and survives layout changes. The bounding box is where the control was on the day. |
| Confidence | Fixed per strategy: 0.95, 0.85, 0.85, 0.6, 0.4, 0.1 | The number is a statement about the strategy, not the element. A reviewer can read it as "how surprised should I be if this one was needed". |
| Reasoning | Every candidate carries a sentence; the first carries the model's own reasoning for the step | The artifact says why a control was chosen at all, not only how to find it. |
| Outputs | Role, name and text are left out of an output's candidates | Those facts are the value itself. A candidate built from `$4,242.00` finds today's balance and nobody else's. The row header (`th:text-is("Savings balance") + td`) takes their place. |
| Frames | `frame_path` is the chain of frame names from the top document; unnamed frames get `frame[n]` | The demo app's search form is in an iframe. A locator that ignores the frame never resolves. Positional names are fragile and the artifact shows that. |
| Win condition | Visible and exactly one match within the candidate's share of the timeout | Two matches means the candidate is ambiguous, which is as bad as none. |
| Timeout | Split evenly across candidates, minimum 250 ms each | A target with more fallbacks does not take longer to fail. |
| Drift | `candidate_index > 0` logs `target_resolved` with the index and adds a `drift_warning` to the result | The run goes on. The warning is what tells a maintainer the recording is ageing. |
| Record-named links | A link whose text was not on the start screen is page data: its results-row position ranks first, its name last | The name finds the recorded member and nobody else. Ranking it first raised a meaningless drift warning on every other member. |
| Variant | Title and version string checked before the first step; each key screen's shape measured the first time replay lands on it | A different build of the app is a different signal from one control moving. `variant_mismatch` is a warning, not a stop. |
| Screen shape | Lantern's method: the ordered (role, state bitmap, landmark) tuples a person meets tabbing through the screen, compared by edit distance, threshold in `policy.yaml`. Controls inside a dialog are left out: a modal overlay is state, not shape | A hash says "identical or not", and a bank page is never identical once a name differs. A distance says how different, in controls: a renamed button is 0, an extra focusable control is 1. The number is logged every time. |

Evidence run 7 shows the signals on variant B: the Search button is
renamed, so candidate 1 (its structural path) resolves and `drift_warning` is
raised; the version string differs, so `variant_mismatch` is raised; and
the search screen measures a distance of 0 out of 2, because a renamed
button is the same shape. The shape measure is a port of Lantern
(github.com/JoshTSeppich/Lantern), credited in `surface/fingerprint.py`.

## Alternatives rejected

- One "best" selector per control: a single point of failure on a
  table-based UI with no ids.
- Self-healing with a model at replay time: replay must run without a
  model. Drift is reported; repairing it is a re-record or a future
  learn-from-operator step (REPORT §7).
- Persisting Playwright's ARIA snapshot refs: they are ephemeral and
  mean nothing on the next page load, let alone on a desktop surface.
- Visual matching (template images): fragile across themes and DPI,
  and it cannot explain itself.
- A hash of the ARIA tree as the variant signal (the first build had
  one): it fired on every page with data on it and said nothing about
  how much had changed.

## Consequences

Easier: a control that moves or is relabelled still resolves, and the
run says so. The artifact is readable by someone who has never seen
the page.

Harder: six candidates per control makes the artifact long, and a
bounding box candidate can click the wrong thing if everything else
failed. I kept it at confidence 0.1 and only click and type accept it.

I have not tested this on a frameset with cross-origin iframes;
`frame_path` assumes Playwright can see into every frame.
