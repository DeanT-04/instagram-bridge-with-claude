"""Low-level UI actions on live UIA elements (synchronous; run on the UIA worker thread).

* :func:`click` — ``InvokePattern`` first (works without focus or a visible cursor), then
  falls back to scrolling the element into view and clicking its bounding-rect centre.
  The centre-click fallback is refused for container-sized elements (both sides above
  :data:`MAX_MOUSE_TARGET`): the centre of a feed/article is some arbitrary child, e.g.
  a Like button, unless ``allow_large=True``.
* :func:`type_text` / :func:`press_keys` — focus then ``SendKeys`` (real keystrokes, so
  React inputs see proper input events; ``ValuePattern.SetValue`` would not).
* :func:`scroll_element` — ``ScrollPattern`` (no focus needed); :func:`scroll_keys` —
  PageUp/PageDown keystrokes as a last resort.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from heliograph.drivers.uia.runtime import load_uiautomation
from heliograph.errors import ElementNotFoundError, UnsafeActionError

__all__ = [
    "MAX_MOUSE_TARGET",
    "ClickMethod",
    "check_mouse_target",
    "click",
    "escape_keys",
    "focus",
    "focused_element",
    "press_keys",
    "scroll_element",
    "scroll_keys",
    "type_text",
]

ClickMethod = Literal["invoke", "mouse"]
MAX_MOUSE_TARGET = (400, 200)
"""``(width, height)`` in px: larger elements are containers, never centre-clicked."""


def _control(element: Any) -> Any:
    """Wrap a raw ``IUIAutomationElement`` (or pass a uiautomation Control through)."""
    auto = load_uiautomation()
    if isinstance(element, auto.Control):
        return element
    control = auto.Control.CreateControlFromElement(element)
    if control is None:
        raise ElementNotFoundError("element is no longer available")
    return control


def escape_keys(text: str) -> str:
    """Escape ``{`` and ``}`` so ``uiautomation.SendKeys`` types them literally."""
    return "".join("{{}" if ch == "{" else "{}}" if ch == "}" else ch for ch in text)


def check_mouse_target(width: int, height: int, name: str, *, allow_large: bool) -> None:
    """Refuse a centre click on a container-sized element (see module docstring)."""
    max_w, max_h = MAX_MOUSE_TARGET
    if not allow_large and width > max_w and height > max_h:
        raise UnsafeActionError(
            f"refusing to mouse-click the centre of {name!r} ({width}x{height}px): it is a "
            "container, the click would land on an arbitrary child",
            hint="Click a specific child element (take a snapshot), or pass allow_large=True.",
        )


def _mouse_click(control: Any, *, allow_large: bool = False) -> None:
    auto = load_uiautomation()
    try:
        control.GetScrollItemPattern().ScrollIntoView()
        time.sleep(0.2)
    except Exception:
        pass
    rect = control.BoundingRectangle
    if rect.width() <= 0 or rect.height() <= 0:
        raise ElementNotFoundError(f"element {control.Name!r} has no on-screen area to click")
    check_mouse_target(rect.width(), rect.height(), control.Name, allow_large=allow_large)
    auto.Click(rect.xcenter(), rect.ycenter(), waitTime=0.1)


def click(
    element: Any, *, prefer: ClickMethod = "invoke", allow_large: bool = False
) -> ClickMethod:
    """Activate ``element``; returns the method that worked (``"invoke"`` or ``"mouse"``)."""
    control = _control(element)
    if prefer == "invoke":
        try:
            pattern = control.GetInvokePattern()
            if pattern is not None:
                pattern.Invoke()
                return "invoke"
        except Exception:
            pass  # not invokable -> physical click
    _mouse_click(control, allow_large=allow_large)
    return "mouse"


def focus(element: Any) -> None:
    """Give keyboard focus to ``element`` (best effort, falls back to a mouse click)."""
    control = _control(element)
    try:
        control.SetFocus()
    except Exception:
        _mouse_click(control)


def focused_element() -> tuple[str, str] | None:
    """``(role, name)`` of the element with keyboard focus, or None if unknown."""
    auto = load_uiautomation()
    try:
        control = auto.GetFocusedControl()
        if control is None:
            return None
        role = str(control.ControlTypeName).removesuffix("Control").lower()
        return role, str(control.Name or "")
    except Exception:
        return None


def type_text(text: str, element: Any | None = None, *, clear: bool = False) -> None:
    """Type ``text`` into ``element`` (focused first) or into whatever has focus."""
    auto = load_uiautomation()
    if element is not None:
        focus(element)
        time.sleep(0.1)
    if clear:
        auto.SendKeys("{Ctrl}a{Delete}", waitTime=0.05)
    if text:  # SendKeys("") raises inside uiautomation
        auto.SendKeys(escape_keys(text), interval=0.01, waitTime=0.1)


def press_keys(keys: str, element: Any | None = None) -> None:
    """Send ``uiautomation.SendKeys`` syntax, e.g. ``"{Enter}"``, ``"{Ctrl}a"``, ``"{Esc}"``."""
    auto = load_uiautomation()
    if element is not None:
        focus(element)
    auto.SendKeys(keys, waitTime=0.1)


def scroll_element(element: Any, direction: Literal["up", "down"] = "down", pages: int = 1) -> bool:
    """Scroll ``element`` via its ``ScrollPattern``; True if its scroll position changed.

    Needs no focus and moves no mouse. The document scrolls on feed/profile pages; the
    Reels viewer scrolls an inner container group instead (the document does not move).
    """
    auto = load_uiautomation()
    control = _control(element)
    amounts = auto.ScrollAmount
    step = amounts.LargeIncrement if direction == "down" else amounts.LargeDecrement
    try:
        pattern = control.GetPattern(auto.PatternId.ScrollPattern)
        if pattern is None or not pattern.VerticallyScrollable:
            return False
        before = pattern.VerticalScrollPercent
        if (before <= 0 and direction == "up") or (before >= 100 and direction == "down"):
            return True  # already at the edge: nothing to scroll, but this is the scroller
        for _ in range(pages):
            pattern.Scroll(amounts.NoAmount, step)
        for _ in range(6):  # smooth scrolling updates the position asynchronously
            if pattern.VerticalScrollPercent != before:
                return True
            time.sleep(0.1)
        return False
    except Exception:
        return False


def scroll_keys(doc: Any, direction: Literal["up", "down"] = "down", pages: int = 1) -> None:
    """Send PageDown/PageUp to the focused document (window must be in the foreground)."""
    auto = load_uiautomation()
    try:
        _control(doc).SetFocus()
    except Exception:
        pass
    key = "{PageDown}" if direction == "down" else "{PageUp}"
    auto.SendKeys(key * pages, waitTime=0.1)
