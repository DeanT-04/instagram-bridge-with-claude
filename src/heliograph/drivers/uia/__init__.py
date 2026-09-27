"""Windows UI Automation driver for the Microsoft Store Instagram app (an Edge PWA).

Public API::

    from heliograph.drivers.uia import UiaDriver

    driver = UiaDriver()
    await driver.ensure_ready()
    snap = await driver.take_snapshot()        # Snapshot with refs e1, e2, ...
    print(snap.render_text(visible_only=True))
    await driver.navigate_section("reels")
    posts = await driver.visible_posts()
    await driver.screenshot(Path("app.png"))

Modules: ``runtime`` (COM worker thread), ``app`` (find/launch/focus windows by AUMID),
``source`` (UIA -> raw tree, cached), ``tree`` (compact snapshot + text rendering),
``actions`` (click/type/keys/scroll), ``nav`` (left-nav sections), ``read`` (posts,
badges, section), ``screenshot`` (PrintWindow), ``core``/``writes``/``driver`` (the driver).
Importing this package is safe on any OS; only calling into it needs Windows.
"""

from heliograph.drivers.uia.driver import UiaDriver
from heliograph.drivers.uia.tree import Node, RawNode, Snapshot, build_snapshot

__all__ = ["Node", "RawNode", "Snapshot", "UiaDriver", "build_snapshot"]
