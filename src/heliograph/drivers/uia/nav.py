"""Left-navigation targets of Instagram web, as exposed in the Store app's UIA tree.

Live-discovered link names (window ~1575 px wide, collapsed icon rail)::

    hyperlink "Home"                 -> https://www.instagram.com/
    hyperlink "Reels"                -> https://www.instagram.com/reels/
    hyperlink "Messages Direct messaging – N new notification link" -> /direct/inbox/
    hyperlink "Search"               -> /explore/ (opens the search drawer, URL unchanged)
    hyperlink "Explore"              -> /explore/ (only in the wide layout)
    hyperlink "Notifications"        -> # (opens the notifications drawer)
    hyperlink "New post" / "Create"  -> # (opens the create dialog)
    hyperlink "<user>'s profile picture" or "Profile" -> /<user>/

``saved`` has no nav link: it is ``/<user>/saved/`` (the profile's "Saved" tab).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from heliograph.drivers.uia.read import username_from_url
from heliograph.drivers.uia.tree import Node, Snapshot

__all__ = ["SECTIONS", "NavTarget", "own_username", "resolve_section"]

SECTIONS = (
    "home",
    "search",
    "explore",
    "reels",
    "messages",
    "notifications",
    "create",
    "profile",
    "saved",
)
_BASE = "https://www.instagram.com/"
_FALLBACK_URLS = {
    "home": _BASE,
    "explore": _BASE + "explore/",
    "reels": _BASE + "reels/",
    "messages": _BASE + "direct/inbox/",
}
_RULES: dict[str, tuple[re.Pattern[str], ...]] = {
    "home": (re.compile(r"^Home$"),),
    "search": (re.compile(r"^Search$"),),
    "explore": (re.compile(r"^Explore$"),),
    "reels": (re.compile(r"^Reels$"),),
    "messages": (re.compile(r"^(Messages|Direct messaging)"),),
    "notifications": (re.compile(r"^Notifications"),),
    "create": (re.compile(r"^(New post|Create)$"),),
    "profile": (re.compile(r"^Profile$"), re.compile(r"'s profile picture$")),
}


@dataclass(frozen=True, slots=True)
class NavTarget:
    """How to reach a section: click ``node`` if set, else open ``url``."""

    section: str
    node: Node | None = None
    url: str | None = None


def _nav_links(snap: Snapshot) -> list[Node]:
    """Hyperlinks of the left rail: before the ``main`` landmark and in the same column as
    the "Home" link. Empty when the rail is hidden (e.g. the Notifications panel replaces
    it), so panel links such as other users' profile pictures are never mistaken for nav.
    """
    main = snap.find(role="main")
    limit = snap.index_of(main[0].ref) if main else len(snap.nodes)
    candidates = [n for n in snap.nodes[:limit] if n.role == "hyperlink" and n.name]
    home = next((n for n in candidates if n.name == "Home"), None)
    if home is None:
        return []
    width = max(home.rect[2] - home.rect[0], 1)
    return [n for n in candidates if abs(n.center[0] - home.center[0]) <= width]


def own_username(snap: Snapshot) -> str | None:
    """The logged-in username, from the nav's profile link (None if not visible)."""
    for node in _nav_links(snap):
        if node.name == "Profile" or node.name.endswith("'s profile picture"):
            user = username_from_url(node.value)
            if user:
                return user
    return None


def resolve_section(snap: Snapshot, section: str) -> NavTarget:
    """Find how to open ``section`` (one of :data:`SECTIONS`) from the current snapshot.

    Raises:
        ValueError: unknown section.
    """
    if section not in SECTIONS:
        raise ValueError(f"unknown section {section!r}; expected one of {', '.join(SECTIONS)}")
    if section == "saved":
        user = own_username(snap)
        return NavTarget(section, url=f"{_BASE}{user}/saved/" if user else None)
    links = _nav_links(snap)
    for pattern in _RULES[section]:
        for node in links:
            if not pattern.search(node.name):
                continue
            if section == "profile" and not username_from_url(node.value):
                continue
            return NavTarget(section, node=node)
    if section == "profile":
        user = own_username(snap)
        return NavTarget(section, url=f"{_BASE}{user}/" if user else None)
    return NavTarget(section, url=_FALLBACK_URLS.get(section))
