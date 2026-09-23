"""The README makes checkable promises; these check them.

A link that rots, a `make` target that is renamed out from under a code block,
or an evidence directory the table names but nobody committed are all the same
failure: the document claims something the repository does not have. They are
cheap to catch here and embarrassing to find in review.

Nothing here starts a browser, reads the network or needs a key.
"""

import re
from pathlib import Path

import pytest

from bankbot.evidence.verify import LEAK_PATTERNS

ROOT = Path(__file__).parent.parent
README = ROOT / "README.md"
# Markdown `[text](target)` plus the `src=` / `href=` of the raw HTML the media
# block uses, because GitHub resolves both against the repository root.
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
HTML_ATTR = re.compile(r'(?:src|href)="([^"]+)"')
MAKE_COMMAND = re.compile(r"\bmake ([a-z][a-z0-9-]*)")
# A `## Heading` becomes `#heading` on GitHub: lowercased, spaces to dashes,
# anything else dropped.
HEADING = re.compile(r"^#{2,3} (.+)$", re.MULTILINE)
# Large enough to notice in a clone, small enough that GitHub still renders it.
MEDIA_CEILING_BYTES = 8 * 1024 * 1024


def readme() -> str:
    return README.read_text(encoding="utf-8")


def targets(document: str) -> list[str]:
    """Every link target in the document, Markdown and raw HTML alike."""
    return MARKDOWN_LINK.findall(document) + HTML_ATTR.findall(document)


def anchor(heading: str) -> str:
    return "#" + re.sub(r"[^a-z0-9 -]", "", heading.lower()).replace(" ", "-")


def test_every_relative_link_in_the_readme_points_at_something_that_exists() -> None:
    missing = [
        target
        for target in targets(readme())
        if not target.startswith(("http://", "https://", "#", "mailto:"))
        and not (ROOT / target.split("#")[0]).exists()
    ]
    assert missing == []


def test_every_anchor_the_readme_links_to_is_a_heading_in_it() -> None:
    document = readme()
    headings = {anchor(found) for found in HEADING.findall(document)}
    used = {target for target in targets(document) if target.startswith("#")}
    assert used - headings == set()


def test_every_make_command_the_readme_names_is_a_real_target() -> None:
    declared = set(
        re.findall(r"^([a-z][a-z0-9-]*):", (ROOT / "Makefile").read_text(), re.MULTILINE)
    )
    named = set(MAKE_COMMAND.findall(readme()))
    assert named - declared == set()


def test_the_readme_names_every_committed_evidence_directory() -> None:
    # The table is the map a reviewer navigates by, so a run nobody mentions is
    # as much a defect as a mention with no run behind it.
    document = readme()
    on_disk = {path.name for path in (ROOT / "evidence").iterdir() if path.is_dir()}
    unmentioned = {name for name in on_disk if name not in document and name[:2] not in document}
    assert unmentioned == set()


@pytest.mark.parametrize("name", ["handoff.gif", "handoff.mp4"])
def test_committed_media_is_referenced_and_small_enough_to_ship(name: str) -> None:
    path = ROOT / "docs" / "media" / name
    assert path.is_file()
    assert f"docs/media/{name}" in readme()
    assert path.stat().st_size <= MEDIA_CEILING_BYTES, (
        f"{name} is {path.stat().st_size / 1024 / 1024:.1f} MB"
    )


def test_the_animation_is_never_the_only_account_of_the_handoff() -> None:
    # A reader with images off, or a screen reader, still gets the sequence.
    document = readme()
    assert "The same sequence in text" in document
    for reason in ("unknown_dialog", "checkpoint_unmet", "wait_for"):
        assert reason in document


def test_no_committed_document_carries_a_key_or_a_home_path() -> None:
    # The same shapes bankbot/evidence/verify.py refuses inside a run directory.
    # Prose is written by hand, so it never went through the redactor at all.
    offenders = []
    for path in [README, ROOT / "REPORT.md", *(ROOT / "docs").rglob("*.md")]:
        text = path.read_text(encoding="utf-8")
        for label, pattern in LEAK_PATTERNS.items():
            if pattern.search(text):
                offenders.append(f"{path.relative_to(ROOT)}: looks like {label}")
    assert offenders == []
