"""Compact, ref-addressable snapshots of the Instagram web document's accessibility tree.

Pure (no UIA imports, unit-testable): ``source`` builds :class:`RawNode` trees;
:func:`build_snapshot` compacts them into a pre-order list of :class:`Node` with refs
(``e1``, ``e2``...) valid for the :class:`Snapshot`'s lifetime; ``render_text`` outlines it.

Compaction rules: unnamed structural nodes (groups, panes, images...) are dropped and their
children hoisted; a child that merely repeats its parent's label (``Image "Like"`` inside
``Button "Like"``, or a nested ``Button "Save"``) is dropped likewise. Interactive
controls, named nodes and landmarks (article, main, navigation, dialog...) are kept.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

__all__ = ["INTERACTIVE_ROLES", "LANDMARK_ROLES", "Node", "RawNode", "Snapshot", "build_snapshot"]

Rect = tuple[int, int, int, int]
"""``(left, top, right, bottom)`` in physical screen pixels."""

_I = "button hyperlink edit checkbox combobox menuitem tabitem slider radiobutton splitbutton"
INTERACTIVE_ROLES = frozenset([*_I.split(), "spinner"])
LANDMARK_ROLES = frozenset(["article", "main", "navigation", "dialog", "search", "form", "list"])
_LABEL_ROLES = frozenset({"image", "text", "group"})  # roles of mere labels


@dataclass(slots=True)
class RawNode:
    """One element as read from UI Automation (or built by a test)."""

    role: str
    name: str = ""
    value: str = ""
    rect: Rect = (0, 0, 0, 0)
    offscreen: bool = False
    children: list[RawNode] = field(default_factory=list)
    handle: Any = None
    """Opaque live element (``IUIAutomationElement``) used by actions; None in tests."""


@dataclass(slots=True)
class Node:
    """A kept node of a snapshot."""

    ref: str
    role: str
    name: str
    value: str
    rect: Rect
    depth: int
    parent: str | None
    visible: bool
    interactive: bool

    @property
    def center(self) -> tuple[int, int]:
        """Centre point of the bounding rectangle."""
        left, top, right, bottom = self.rect
        return (left + right) // 2, (top + bottom) // 2

    def to_dict(self) -> dict[str, Any]:
        """JSON-able representation (empty fields omitted)."""
        out: dict[str, Any] = {"ref": self.ref, "role": self.role}
        if self.name:
            out["name"] = self.name
        if self.value:
            out["value"] = self.value
        out["rect"] = list(self.rect)
        out["depth"] = self.depth
        if self.parent:
            out["parent"] = self.parent
        if not self.visible:
            out["visible"] = False
        return out


def _intersects(rect: Rect, viewport: Rect | None) -> bool:
    left, top, right, bottom = rect
    if right <= left or bottom <= top:
        return False
    if viewport is None:
        return True
    vl, vt, vr, vb = viewport
    return left < vr and right > vl and top < vb and bottom > vt


@dataclass
class Snapshot:
    """A compacted accessibility snapshot of the app's web document."""

    url: str | None
    title: str
    nodes: list[Node]
    viewport: Rect | None = None
    raw_count: int = 0
    took_ms: float = 0.0
    handles: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._by_ref = {n.ref: n for n in self.nodes}
        self._index = {n.ref: i for i, n in enumerate(self.nodes)}

    def __len__(self) -> int:
        return len(self.nodes)

    def get(self, ref: str) -> Node | None:
        """Node for ``ref`` (e.g. ``"e12"``), or None."""
        return self._by_ref.get(ref)

    def handle(self, ref: str) -> Any:
        """Live element behind ``ref`` (None for fake trees or unknown refs)."""
        return self.handles.get(ref)

    def index_of(self, ref: str) -> int:
        """Pre-order position of ``ref``."""
        return self._index[ref]

    def subtree(self, ref: str) -> list[Node]:
        """``ref`` and all its descendants, in document order."""
        i = self._index[ref]
        root = self.nodes[i]
        out = [root]
        for node in self.nodes[i + 1 :]:
            if node.depth <= root.depth:
                break
            out.append(node)
        return out

    def find(
        self,
        name: str | None = None,
        role: str | None = None,
        *,
        exact: bool = True,
        visible: bool | None = None,
        within: str | None = None,
    ) -> list[Node]:
        """Nodes matching ``name`` (exact or case-insensitive substring) and/or ``role``."""
        pool: Iterator[Node] | list[Node] = self.subtree(within) if within else self.nodes
        needle = name.casefold() if name is not None and not exact else name
        out = []
        for node in pool:
            if role is not None and node.role != role:
                continue
            if visible is not None and node.visible != visible:
                continue
            if name is not None:
                if exact and node.name != name:
                    continue
                if not exact and (needle or "") not in node.name.casefold():
                    continue
            out.append(node)
        return out

    def select(
        self,
        *,
        interactive_only: bool = False,
        visible_only: bool = False,
        max_nodes: int | None = None,
    ) -> tuple[list[Node], bool]:
        """Filtered nodes and whether the list was truncated by ``max_nodes``."""
        nodes = [
            n
            for n in self.nodes
            if (not interactive_only or n.interactive) and (not visible_only or n.visible)
        ]
        if max_nodes is not None and len(nodes) > max_nodes:
            return nodes[:max_nodes], True
        return nodes, False

    def to_dict(
        self,
        *,
        interactive_only: bool = False,
        visible_only: bool = False,
        max_nodes: int | None = None,
    ) -> dict[str, Any]:
        """JSON-able snapshot ``{driver, url, title, nodes, ...}``."""
        nodes, truncated = self.select(
            interactive_only=interactive_only, visible_only=visible_only, max_nodes=max_nodes
        )
        return {
            "driver": "uia",
            "url": self.url,
            "title": self.title,
            "viewport": list(self.viewport) if self.viewport else None,
            "node_count": len(self.nodes),
            "raw_count": self.raw_count,
            "took_ms": round(self.took_ms, 1),
            "truncated": truncated,
            "nodes": [n.to_dict() for n in nodes],
        }

    def render_text(
        self,
        *,
        interactive_only: bool = False,
        visible_only: bool = True,
        max_nodes: int | None = 400,
        max_name: int = 120,
    ) -> str:
        """Indented outline for an LLM, e.g. ``  - button "Like" [e12]``.

        Offscreen nodes (when included) are suffixed ``(offscreen)``; hyperlinks show their
        URL as ``-> url``; indentation follows the compacted tree depth.
        """
        nodes, truncated = self.select(
            interactive_only=interactive_only, visible_only=visible_only, max_nodes=max_nodes
        )
        lines = [f"# {self.title} — {self.url or '?'}"]
        for n in nodes:
            depth = 0 if interactive_only else n.depth
            name = n.name if len(n.name) <= max_name else n.name[: max_name - 1] + "…"
            line = f"{'  ' * depth}- {n.role}"
            if name:
                line += f' "{name}"'
            line += f" [{n.ref}]"
            if n.value:
                line += f" -> {n.value}" if n.role == "hyperlink" else f' = "{n.value[:80]}"'
            if not n.visible:
                line += " (offscreen)"
            lines.append(line)
        if truncated:
            lines.append(f"… truncated at {max_nodes} nodes (snapshot has {len(self.nodes)})")
        return "\n".join(lines)


def _keep(raw: RawNode, parent: Node | None) -> bool:
    if (
        parent is not None
        and raw.name
        and raw.name == parent.name
        and (raw.role in _LABEL_ROLES or raw.role == parent.role)
    ):
        return False  # label that repeats its parent
    if raw.role in INTERACTIVE_ROLES or raw.role in LANDMARK_ROLES:
        return True
    return bool(raw.name.strip() or raw.value)


def build_snapshot(
    root: RawNode,
    *,
    url: str | None = None,
    title: str | None = None,
    viewport: Rect | None = None,
    took_ms: float = 0.0,
) -> Snapshot:
    """Compact ``root`` (the document node itself is not emitted) into a :class:`Snapshot`."""
    nodes: list[Node] = []
    handles: dict[str, Any] = {}
    raw_count = 0
    view = (
        viewport if viewport is not None else (root.rect if _intersects(root.rect, None) else None)
    )
    # iterative pre-order walk: (raw, kept_parent, depth)
    stack: list[tuple[RawNode, Node | None, int]] = [
        (child, None, 0) for child in reversed(root.children)
    ]
    while stack:
        raw, parent, depth = stack.pop()
        raw_count += 1
        next_parent, next_depth = parent, depth
        if _keep(raw, parent):
            ref = f"e{len(nodes) + 1}"
            node = Node(
                ref=ref,
                role=raw.role,
                name=" ".join(raw.name.split()),
                value=raw.value,
                rect=raw.rect,
                depth=depth,
                parent=parent.ref if parent else None,
                visible=not raw.offscreen and _intersects(raw.rect, view),
                interactive=raw.role in INTERACTIVE_ROLES,
            )
            nodes.append(node)
            if raw.handle is not None:
                handles[ref] = raw.handle
            next_parent, next_depth = node, depth + 1
        for child in reversed(raw.children):
            stack.append((child, next_parent, next_depth))
    return Snapshot(
        url=url if url is not None else (root.value or None),
        title=title if title is not None else root.name,
        nodes=nodes,
        viewport=view,
        raw_count=raw_count + 1,
        took_ms=took_ms,
        handles=handles,
    )
