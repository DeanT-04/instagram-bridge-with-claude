"""List saved-post collections by loading ``/<username>/saved/`` in the Instagram page.

The web client has no working REST route for this (``/api/v1/collections/list/`` returns
404), so we open the saved page and combine two sources:

* JSON responses the page itself fetches while rendering (any object carrying
  ``collection_id`` + ``collection_name`` — gives ``media_count`` when present), and
* the rendered anchors ``/<username>/saved/<slug>/<id>/`` (always present).
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from heliograph.eye import span
from heliograph.instagram.endpoints import parse_collection_links
from heliograph.instagram.models import Collection

__all__ = ["CollectionLister", "collections_from_json"]

_ANCHORS_JS = """async ([username, timeoutMs]) => {
  const re = new RegExp('^/' + username + '/saved/[^/]+/\\\\d+/?$', 'i');
  const grab = () => [...document.querySelectorAll('a[href]')]
      .map(a => [a.getAttribute('href') || '', (a.innerText || a.getAttribute('aria-label') || '')])
      .filter(([h]) => re.test(h.split('?')[0]));
  const t0 = Date.now();
  let links = grab();
  while (!links.length && Date.now() - t0 < timeoutMs) {
    await new Promise(r => setTimeout(r, 300));
    links = grab();
  }
  return links;
}"""


class _Driver(Protocol):
    page: Any

    async def navigate(self, url: str, *, wait_until: str = ...) -> None: ...

    def capture(self, patterns: tuple[str, ...] = ...) -> Any: ...


def collections_from_json(node: Any, out: dict[str, Collection] | None = None,
                          depth: int = 0) -> dict[str, Collection]:
    """Collect collection objects (``collection_id`` + ``collection_name``) from any JSON."""
    out = {} if out is None else out
    if depth > 12:
        return out
    if isinstance(node, dict):
        if node.get("collection_id") and node.get("collection_name"):
            col = Collection.from_api(node)
            if col.id and col.id not in out:
                out[col.id] = col
        for v in node.values():
            collections_from_json(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            collections_from_json(v, out, depth + 1)
    return out


class CollectionLister:
    """List collections through the CDP driver's page."""

    def __init__(self, driver: _Driver, *, timeout: float = 15.0) -> None:
        self.driver = driver
        self.timeout = timeout

    async def list(self, username: str) -> list[Collection]:
        """Return the user's collections in on-page order (media counts when known)."""
        async with span("ig.collections.page", username=username) as s:
            async with self.driver.capture() as cap:
                await self.driver.navigate(f"https://www.instagram.com/{username}/saved/")
                links = await self.driver.page.evaluate(
                    _ANCHORS_JS, [username, int(self.timeout * 1000)])
                await asyncio.sleep(1.0)  # let in-flight JSON responses land
            from_json: dict[str, Collection] = {}
            for resp in cap.responses:
                collections_from_json(resp.data, from_json)
            parsed = parse_collection_links([(h, t) for h, t in links], username)
            result: list[Collection] = []
            for item in parsed:
                known = from_json.pop(item["id"], None)
                result.append(Collection(
                    id=item["id"],
                    name=known.name if known and known.name else item["name"],
                    media_count=known.media_count if known else None,
                ))
            result.extend(from_json.values())  # e.g. collections not rendered yet
            s.set(anchors=len(parsed), from_json=len(result) - len(parsed),
                  captured=len(cap.responses))
            return result
