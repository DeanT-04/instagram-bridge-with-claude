"""The UI Automation worker thread.

UI Automation is COM: each thread that touches it must initialise COM, and element
pointers are best used from the thread that obtained them. The driver therefore owns one
dedicated worker thread (a single-worker executor whose initializer runs
``uiautomation.InitializeUIAutomationInCurrentThread``) and funnels *every* UIA call
through :meth:`UiaWorker.run`. Snapshot refs stay valid between calls because they are
always dereferenced on that same thread.
"""

from __future__ import annotations

import asyncio
import ctypes
import functools
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

__all__ = ["UiaWorker", "load_uiautomation", "make_dpi_aware"]

T = TypeVar("T")

_dpi_done = False


def make_dpi_aware() -> None:
    """Make this process per-monitor DPI aware so win32 and UIA agree on pixel coordinates.

    Without it ``GetWindowRect`` returns scaled (logical) coordinates while UIA returns
    physical ones, and clicks/screenshots land in the wrong place. Idempotent; never raises.
    """
    global _dpi_done
    if _dpi_done or sys.platform != "win32":
        return
    _dpi_done = True
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def load_uiautomation() -> Any:
    """Import and return the ``uiautomation`` module (Windows only).

    Raises:
        ImportError: the package is missing or this is not Windows.
    """
    if sys.platform != "win32":
        raise ImportError("uiautomation is Windows-only")
    make_dpi_aware()
    import uiautomation

    return uiautomation


def _init_thread() -> None:
    auto = load_uiautomation()
    auto.InitializeUIAutomationInCurrentThread()


class UiaWorker:
    """A single COM-initialised thread that runs all UI Automation work."""

    def __init__(self) -> None:
        self._pool: ThreadPoolExecutor | None = None

    def _executor(self) -> ThreadPoolExecutor:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="heliograph-uia", initializer=_init_thread
            )
        return self._pool

    async def run(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
        """Run ``fn(*args, **kwargs)`` on the UIA thread and await its result."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor(), functools.partial(fn, *args, **kwargs))

    def call(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
        """Synchronous variant of :meth:`run` (blocks the caller until done)."""
        return self._executor().submit(fn, *args, **kwargs).result()

    def shutdown(self) -> None:
        """Stop the worker thread (a later :meth:`run` starts a new one)."""
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
            self._pool = None
