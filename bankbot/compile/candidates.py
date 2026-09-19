"""Turning what the surface saw about an element into ranked ways to find it again.

Owns: the ranking. Role and accessible name first, because that is how a
person names a control and it survives layout changes; label and exact
text next; a table cell anchored on its row header; the structural path;
and the bounding box last, at a confidence that says "do not trust this".
Every candidate carries the model's reasoning for the step so a reviewer
can see why the control was chosen at all.

Does not own: resolving candidates (surface/) or assembling steps
(compile/compiler.py).

Governed by ADR-0002 (locator strategy).
"""

from bankbot.schemas import Candidate, LocatorStrategy, TargetRef
from bankbot.surface import ElementFacts

ROLE_NAME_CONFIDENCE = 0.95
LABEL_CONFIDENCE = 0.85
ROW_HEADER_CONFIDENCE = 0.85
TEXT_CONFIDENCE = 0.6
STRUCTURAL_CONFIDENCE = 0.4
BBOX_CONFIDENCE = 0.1


def target_from_facts(
    facts: ElementFacts, reasoning: str, *, for_output: bool = False
) -> TargetRef:
    """Rank every fact about the element into a candidate, most durable first.

    for_output marks an element whose text is an output. Its accessible name
    and text are its value, so candidates built from them would only ever
    find today's value; they are left out and the row header takes their
    place.
    """
    candidates: list[Candidate] = []
    if facts.role and facts.name and not for_output:
        candidates.append(
            Candidate(
                strategy=LocatorStrategy.ROLE_NAME,
                value=f"{facts.role}:{facts.name}",
                confidence=ROLE_NAME_CONFIDENCE,
                reasoning=reasoning,
            )
        )
    if facts.label:
        candidates.append(
            Candidate(
                strategy=LocatorStrategy.LABEL,
                value=facts.label,
                confidence=LABEL_CONFIDENCE,
                reasoning="The control's label, which a person reads to find it.",
            )
        )
    if facts.row_header:
        candidates.append(
            Candidate(
                strategy=LocatorStrategy.CSS_STRUCTURAL,
                value=f'th:text-is("{facts.row_header}") + td',
                confidence=ROW_HEADER_CONFIDENCE,
                reasoning="The cell right after its row header; survives rows moving.",
            )
        )
    if facts.text and facts.text != facts.name and not for_output:
        candidates.append(
            Candidate(
                strategy=LocatorStrategy.TEXT,
                value=facts.text,
                confidence=TEXT_CONFIDENCE,
                reasoning="Exact visible text; breaks when the wording changes.",
            )
        )
    candidates.append(
        Candidate(
            strategy=LocatorStrategy.CSS_STRUCTURAL,
            value=facts.css_path,
            confidence=STRUCTURAL_CONFIDENCE,
            reasoning="Structural path from the document root; breaks when the layout changes.",
        )
    )
    x, y, width, height = facts.bbox
    candidates.append(
        Candidate(
            strategy=LocatorStrategy.BBOX,
            value=f"{x:.0f},{y:.0f},{width:.0f},{height:.0f}",
            confidence=BBOX_CONFIDENCE,
            reasoning="Screen position at recording time; a last resort.",
        )
    )
    return TargetRef(candidates=candidates, frame_path=list(facts.frame_path))
