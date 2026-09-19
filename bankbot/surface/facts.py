"""ElementFacts: describe the element an action touched, in one round trip to the browser.

Owns: the one piece of JavaScript in the repo and its translation into
ElementFacts. Every field is something the compiler can turn into a locator
candidate: role and accessible name first, label, text, a structural CSS
path, and a bounding box last.

Does not own: choosing candidates or ranking them (compile/).

Governed by ADR-0002 (locator strategy).
"""

from playwright.sync_api import Locator

from bankbot.surface.types import ElementFacts

# Computes, for one element: its role (explicit role attribute, else a small implicit map that
# follows what Playwright's own snapshot reports for this app's tags), a best-effort accessible
# name (aria-label, else its <label>, else its text, else its value), the label text, the visible
# text capped at 80 characters, the first th of its table row when it is or sits inside a td (null
# otherwise), a CSS path from body down using tag and :nth-of-type only where a
# tag repeats among its siblings, and a bounding box in CSS pixels of the top document (each
# enclosing iframe's offset is added, so a same-origin frame gives the same coordinates a bbox
# candidate expects; a cross-origin frame stops the walk and the box stays frame-relative).
ELEMENT_FACTS_JS = """
(element) => {
  const IMPLICIT = { a: "link", button: "button", select: "combobox", td: "cell",
                     h1: "heading", h2: "heading", h3: "heading",
                     h4: "heading", h5: "heading", h6: "heading" };
  const tag = element.tagName.toLowerCase();
  const inputType = (element.getAttribute("type") || "text").toLowerCase();

  const textInput = inputType === "text" || inputType === "password";
  const buttonInput = inputType === "submit" || inputType === "button";
  let role = element.getAttribute("role");
  if (!role && tag === "input" && textInput) role = "textbox";
  if (!role && tag === "input" && buttonInput) role = "button";
  if (!role && tag === "th") role = element.getAttribute("scope") === "row" ? "rowheader" : "cell";
  if (!role) role = IMPLICIT[tag] || null;

  const labels = element.labels ? Array.from(element.labels) : [];
  const label = labels.length ? (labels[0].innerText.trim() || null) : null;
  const text = (element.innerText || "").trim();
  // A password's value must never become its name: the name ends up in the artifact.
  const value = (tag === "input" && inputType === "password") ? "" : (element.value || "");
  const name = element.getAttribute("aria-label") || label || text || value || null;

  const cell = element.closest("td");
  const th = cell && cell.closest("tr") ? cell.closest("tr").querySelector("th") : null;
  const rowHeader = th ? (th.innerText.trim() || null) : null;

  const segments = [];
  const body = element.ownerDocument.body;
  for (let node = element; node && node !== body && node.parentElement; node = node.parentElement) {
    const siblings = Array.from(node.parentElement.children);
    const sameTag = siblings.filter((s) => s.tagName === node.tagName);
    const tagName = node.tagName.toLowerCase();
    const nth = sameTag.length > 1 ? `:nth-of-type(${sameTag.indexOf(node) + 1})` : "";
    segments.unshift(tagName + nth);
  }
  segments.unshift("body");

  const rect = element.getBoundingClientRect();
  let x = rect.x;
  let y = rect.y;
  for (let win = element.ownerDocument.defaultView; win && win.frameElement; win = win.parent) {
    const frameRect = win.frameElement.getBoundingClientRect();
    x += frameRect.x + win.frameElement.clientLeft;
    y += frameRect.y + win.frameElement.clientTop;
  }

  return { role, name, label, text: text ? text.slice(0, 80) : null, row_header: rowHeader,
           css_path: segments.join(" > "), bbox: [x, y, rect.width, rect.height] };
}
"""


def element_facts(locator: Locator, frame_path: list[str]) -> ElementFacts:
    """Describe the element before acting on it, so a click that navigates still has facts."""
    raw = locator.evaluate(ELEMENT_FACTS_JS)
    return ElementFacts.model_validate({**raw, "frame_path": frame_path})
