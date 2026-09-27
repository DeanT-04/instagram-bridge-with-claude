"""Read the live web document of an app window into :class:`~.tree.RawNode` trees.

The web content of the PWA window is the ``DocumentControl`` (UIA ``RootWebArea``) under
the ``Chrome_RenderWidgetHostHWND`` pane. Its ``Name`` is the page title and its
``ValuePattern.Value`` is the **current URL** (live-verified), which is how
:func:`document_url` works even though the standalone window has no omnibox.

For speed the whole subtree is fetched in one cross-process round trip with a UIA
``CacheRequest`` (``TreeScope_Subtree``): ~500 nodes in ~0.6 s versus ~2 s for a
``WalkControl`` walk. Must run on the UIA worker thread.
"""

from __future__ import annotations

import time
from typing import Any

from heliograph.drivers.uia.runtime import load_uiautomation
from heliograph.drivers.uia.tree import RawNode, Snapshot, build_snapshot
from heliograph.errors import ElementNotFoundError

__all__ = ["document_url", "find_document", "read_snapshot", "role_of"]

_LANDMARK_TYPES = {
    "article",
    "main",
    "navigation",
    "dialog",
    "search",
    "form",
    "region",
    "banner",
    "time",
    "content information",
}


def _core() -> Any:
    from uiautomation import uiautomation as impl

    return impl._AutomationClient.instance()


def find_document(hwnd: int) -> Any:
    """Return the ``uiautomation`` DocumentControl of the web page in window ``hwnd``."""
    auto = load_uiautomation()
    win = auto.ControlFromHandle(hwnd)
    if win is None:
        raise ElementNotFoundError(f"window {hwnd:#x} is gone")
    pane = win.PaneControl(searchDepth=14, ClassName="Chrome_RenderWidgetHostHWND")
    if not pane.Exists(maxSearchSeconds=3, searchIntervalSeconds=0.2):
        raise ElementNotFoundError("Instagram web content pane not found in app window")
    doc = pane.DocumentControl(searchDepth=1)
    if not doc.Exists(maxSearchSeconds=3, searchIntervalSeconds=0.2):
        raise ElementNotFoundError("Instagram web document not found in app window")
    return doc


def document_url(doc: Any) -> str | None:
    """URL of the page shown by ``doc`` (from its ValuePattern), or None."""
    try:
        value = doc.GetValuePattern().Value
    except Exception:
        return None
    return str(value) or None


def role_of(control_type: int, localized: str, names: dict[int, str]) -> str:
    """Map a UIA control type id (+ localized type) to a short lower-case role."""
    base = names.get(control_type, "Control")
    role = base.removesuffix("Control").lower() or "unknown"
    loc = (localized or "").lower()
    if role in ("group", "text") and loc in _LANDMARK_TYPES:
        return loc.replace(" ", "_")
    return role


def _rect(value: Any) -> tuple[int, int, int, int]:
    try:
        return (int(value.left), int(value.top), int(value.right), int(value.bottom))
    except AttributeError:
        return (0, 0, 0, 0)


def read_snapshot(doc: Any) -> Snapshot:
    """Snapshot the whole subtree of ``doc`` using a single cached UIA request."""
    auto = load_uiautomation()
    client = _core()
    uia, core = client.IUIAutomation, client.UIAutomationCore
    props = {
        "name": core.UIA_NamePropertyId,
        "type": core.UIA_ControlTypePropertyId,
        "loc": core.UIA_LocalizedControlTypePropertyId,
        "rect": core.UIA_BoundingRectanglePropertyId,
        "off": core.UIA_IsOffscreenPropertyId,
        "value": core.UIA_ValueValuePropertyId,
    }
    request = uia.CreateCacheRequest()
    for pid in props.values():
        request.AddProperty(pid)
    request.TreeScope = core.TreeScope_Subtree
    request.TreeFilter = uia.RawViewCondition
    names: dict[int, str] = auto.ControlTypeNames
    t0 = time.perf_counter()
    cached = doc.Element.BuildUpdatedCache(request)

    def convert(elem: Any) -> RawNode:
        role = role_of(
            int(elem.CachedControlType), str(elem.CachedLocalizedControlType or ""), names
        )
        value = ""
        if role in ("hyperlink", "edit", "document", "combobox"):
            try:
                value = str(elem.GetCachedPropertyValue(props["value"]) or "")
            except Exception:
                value = ""
        node = RawNode(
            role=role,
            name=str(elem.CachedName or ""),
            value=value,
            rect=_rect(elem.CachedBoundingRectangle),
            offscreen=bool(elem.CachedIsOffscreen),
            handle=elem,
        )
        kids = elem.GetCachedChildren()
        if kids:
            node.children = [convert(kids.GetElement(i)) for i in range(kids.Length)]
        return node

    root = convert(cached)
    took = (time.perf_counter() - t0) * 1000
    return build_snapshot(root, url=root.value or None, title=root.name, took_ms=took)
