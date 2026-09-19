# CLAUDE.md — engineering directives for this repo

This repo is a take-home submission that will be read side-by-side with others and defended
line-by-line in an interview. Two properties are non-negotiable and override speed:

1. **Every part must be explainable in one breath by the author.** If a module, function, or
   decision cannot be explained in two sentences of plain English, it is too clever. Simplify it.
2. **Git history must be immaculate.** A reviewer reading `git log --oneline` should be able to
   reconstruct how the system was built and why, without opening a single diff.

Read `PLAN.md` first. Read `docs/adr/` before changing any load-bearing design.

## Explainability rules

- One concept per module. Module docstring states (a) what it owns, (b) what it deliberately
  does not own, (c) which ADR governs it.
- Public functions get a docstring that answers *why this exists*, not what the code does.
  No docstring restates the signature.
- Comments explain non-obvious *decisions* ("ranked candidates, not a single selector, because…"),
  never mechanics. If a comment says what the next line does, delete the comment.
- No abstraction without two concrete uses in this repo. No plugin systems, registries,
  metaclasses, or decorators that hide control flow.
- Prefer boring code: explicit `if` chains over dispatch tables, dataclasses/Pydantic over dicts,
  named constants over literals. Optimize for the reader who has 90 seconds per file.
- Every load-bearing design decision (schema shape, locator strategy, error taxonomy, control
  transfer model, policy model) gets a short ADR in `docs/adr/NNNN-title.md`:
  Context / Decision / Alternatives rejected / Consequences. ADRs are the source for REPORT.md.
- Types everywhere. `mypy --strict` clean on the package. Pydantic models for every boundary
  object (artifact, result, policy, intervention request, log record).
- Tests are documentation. Each test name is a sentence describing a behavior
  (`test_replay_reports_member_not_found_as_outcome_not_failure`). Test the load-bearing parts;
  do not chase coverage.
- Errors are typed and named after the condition, not the mechanism:
  `MemberNotFound` not `ElementTimeoutError`.

## Git hygiene rules

- Branch model: `main` only, linear history, no merge commits. Rebase, never merge.
- One logical change per commit. A commit must build and pass tests on its own.
  Never commit "wip", "fix", "stuff", or half-finished slices.
- Conventional Commits, imperative mood, ≤ 72-char subject, body explains *why*:
  `feat(replay): classify known outcomes before hard failures`
  Types: feat, fix, refactor, test, docs, chore, evidence. Scope = module name.
- Commit order tells the build story: schema → target app → replay → discovery → compiler →
  policy → evidence → control/handoff → docs. Do not interleave unrelated work.
- Never rewrite history that has been pushed. Never force-push `main`.
- No generated files, caches, `.venv`, `__pycache__`, traces, or screenshots outside `/evidence/`
  in the tree. `.gitignore` is set up before the first commit.
- Secrets: none in the tree, ever, including in evidence and tests. `.env.example` documents
  every variable. A pre-commit hook (`gitleaks` or `detect-secrets`) blocks the commit if one leaks.
- `/evidence/` files are committed with `evidence:` type commits, after redaction review,
  as the last commits before submission — they are outputs of the finished system.
- Before every commit: `uv run ruff format && uv run ruff check && uv run mypy && uv run pytest`.
  A pre-commit config enforces this; do not bypass with `--no-verify`.
- Commit author is Joshua's GitHub identity. No co-author trailers, no tool attribution lines,
  no session links. The brief assumes AI-assisted development; the repo speaks for itself.

## Repo layout (fixed)

```
README.md        setup, demo path, run-without-LLM path
REPORT.md        seven mandated headings, exact wording from the brief
PLAN.md          this build plan
CLAUDE.md        this file
docs/adr/        architecture decision records
bankbot/         the package (target/ surface/ discover/ compile/ replay/ control/ policy/ evidence/)
tests/
evidence/        artifact + discovery log + replay logs + screenshots (redacted)
policy.yaml      allowlist and risk classes
.env.example
```

## Definition of done for any slice

Code + tests green + ADR (if design) + docstrings answer "why" + one clean commit +
you can explain it aloud in under a minute without looking at the code.

## Voice

Everything human-readable (README, REPORT, ADRs, commit bodies, docstrings) is written by Joshua,
first person, as if typed directly to a senior engineer he respects. Match this register:

- Short declarative sentences. Decision first, reason second. "Ranked candidates, not one
  selector. A single selector is a single point of failure on a table-based UI."
- First person singular for decisions. "I" not "we". Never passive voice for a choice.
- Blunt about tradeoffs. "This is slower. I took the hit because…" Say what was cut in the same
  tone as what was built.
- Tables for decisions: Decision | Choice | Why. Prose for everything else.
- No hedging words unless the uncertainty is real, and then name it exactly: "I have not tested
  this on a frameset with cross-origin iframes."
- Zero marketing register. No "robust", "seamless", "powerful", "comprehensive", "leverage",
  "ensure", "delve", "it's worth noting". No triplets for rhythm. No sentence that can be cut
  without losing a fact.
- Plain words over technical-sounding ones when they mean the same thing. "Stuck" not
  "non-convergent state".
- Headers no deeper than ###. No emoji, no exclamation marks, one CI badge.
- README order: what this is (3 sentences), run it (commands), the demo path, what's mocked and
  why, where to read more. Nothing else.
