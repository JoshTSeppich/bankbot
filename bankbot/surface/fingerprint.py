"""A screen's shape as the sequence of controls a person would tab through, and a distance.

Owns: the one script that walks a document's focusable elements in tab
order and reports each as (role, state bitmap, landmark), and the edit
distance between two such sequences. The method is Lantern's
(github.com/JoshTSeppich/Lantern): a page's shape is the ordered tuples
reached by tabbing through it, and two pages have the same shape when
those sequences are close under Levenshtein distance. Lantern probes with
real focus over CDP; this port approximates tab order as document order
of the focusable elements, positive tabindex first, which is what the
browser does for pages without scripted focus.

Why this and not a hash of the ARIA tree: a hash answers "identical or
not", and on a bank app the answer is always "not" the moment a member
name or a balance differs. A distance says how different, in units a
person can read: a renamed button is 0, an extra column is 1, a different
page is most of the sequence.

Does not own: deciding what distance is too much (policy.yaml) or when to
compare (replay/).

Governed by ADR-0002 (locator strategy: the variant signal).
"""

from collections.abc import Sequence

from playwright.sync_api import Page

from bankbot.schemas.artifact import ScreenElement
from bankbot.surface.frames import walk_frames

# Lantern's eight state bits, in its bit order, read from ARIA attributes and element
# properties. Landmark is the nearest ancestor with a landmark tag or role, else "none".
TAB_ORDER_JS = """
() => {
  const IMPLICIT = { a: "link", button: "button", select: "combobox", textarea: "textbox" };
  const INPUT = { text: "textbox", password: "textbox", search: "searchbox", email: "textbox",
                  submit: "button", button: "button", reset: "button", checkbox: "checkbox",
                  radio: "radio", number: "spinbutton" };
  const LANDMARK_TAG = { main: "main", nav: "nav", header: "header", footer: "footer",
                         aside: "aside" };
  const LANDMARK_ROLE = { main: "main", navigation: "nav", banner: "header", contentinfo: "footer",
                          complementary: "aside" };
  const BITS = ["expanded", "haspopup", "selected", "checked", "disabled", "required",
                "invalid", "readonly"];

  const truthy = (v) => v !== null && v !== undefined && v !== false && v !== "" && v !== "false"
                        && v !== "undefined" && v !== "0";
  const role = (el) => {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "text").toLowerCase();
    if (tag === "input") return INPUT[type] || "textbox";
    return IMPLICIT[tag] || "generic";
  };
  const state = (el) => {
    const props = {
      expanded: el.getAttribute("aria-expanded"),
      haspopup: el.getAttribute("aria-haspopup"),
      selected: el.getAttribute("aria-selected") ?? el.selected,
      checked: el.getAttribute("aria-checked") ?? el.checked,
      disabled: el.getAttribute("aria-disabled") ?? el.disabled,
      required: el.getAttribute("aria-required") ?? el.required,
      invalid: el.getAttribute("aria-invalid"),
      readonly: el.getAttribute("aria-readonly") ?? el.readOnly,
    };
    let bits = 0;
    BITS.forEach((name, i) => { if (truthy(props[name])) bits |= 1 << i; });
    return bits;
  };
  const landmark = (el) => {
    for (let node = el.parentElement; node; node = node.parentElement) {
      const byRole = LANDMARK_ROLE[node.getAttribute("role") || ""];
      if (byRole) return byRole;
      const byTag = LANDMARK_TAG[node.tagName.toLowerCase()];
      if (byTag) return byTag;
    }
    return "none";
  };
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && getComputedStyle(el).visibility !== "hidden";
  };

  const focusable = Array.from(
    document.querySelectorAll("a[href], button, input, select, textarea, [tabindex]")
  ).filter((el) => !el.disabled && el.tabIndex >= 0
                   && (el.getAttribute("type") || "").toLowerCase() !== "hidden" && visible(el));
  const positive = focusable.filter((el) => el.tabIndex > 0)
                            .sort((a, b) => a.tabIndex - b.tabIndex);
  const natural = focusable.filter((el) => el.tabIndex === 0);
  return [...positive, ...natural].map(
    (el) => ({ role: role(el), state: state(el), landmark: landmark(el) })
  );
}
"""


def screen_fingerprint(page: Page) -> list[ScreenElement]:
    """The tab sequence of the top document, then of each frame in walk order.

    Frames are appended rather than interleaved because a person tabs into
    an iframe where it sits, and this app's forms live inside one; a
    fingerprint that skipped frames would miss every control that matters.
    """
    elements: list[ScreenElement] = []
    for _, frame in walk_frames(page):
        for raw in frame.evaluate(TAB_ORDER_JS):
            elements.append(ScreenElement.model_validate(raw))
    return elements


def distance(recorded: Sequence[ScreenElement], actual: Sequence[ScreenElement]) -> int:
    """Levenshtein distance between two tab sequences: insertions, deletions, substitutions."""
    a = [element.key() for element in recorded]
    b = [element.key() for element in actual]
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, item in enumerate(a, start=1):
        current = [i] + [0] * len(b)
        for j, other in enumerate(b, start=1):
            cost = 0 if item == other else 1
            current[j] = min(current[j - 1] + 1, previous[j] + 1, previous[j - 1] + cost)
        previous = current
    return previous[-1]
