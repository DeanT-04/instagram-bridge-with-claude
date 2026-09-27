"""``heliograph login``: open the dedicated browser profile and wait for a manual sign-in."""

from __future__ import annotations

import asyncio
from typing import Any

from rich.console import Console

__all__ = ["run_login"]


async def _login(driver: Any, console: Console, timeout: float) -> bool:
    try:
        with console.status("Opening the Heliograph Instagram window…"):
            await driver.connect()
            if await driver.is_logged_in():
                return True
        console.print("Sign in to Instagram [bold]yourself[/] in the window that just opened. "
                      "Heliograph never sees or stores your password.")
        with console.status(f"Waiting for you to finish signing in (up to {timeout:.0f}s)…"):
            return bool(await driver.login_interactive(timeout=timeout))
    finally:
        await driver.close()


def run_login(console: Console, *, account: str = "default", timeout: float = 300.0,
              driver: Any = None) -> bool:
    """Return True once the profile is logged in (the browser window stays open)."""
    if driver is None:
        from heliograph.drivers.cdp import CdpDriver

        driver = CdpDriver(account)
    ok = asyncio.run(_login(driver, console, timeout))
    if ok:
        console.print(f"[green]✓ Logged in[/] (profile: {account}). You can keep or close the "
                      "window; Heliograph reopens it when Claude needs it.")
    else:
        console.print("[red]Timed out waiting for sign-in.[/] Run `uv run heliograph login` again.")
    return ok
