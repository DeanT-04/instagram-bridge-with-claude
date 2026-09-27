"""CDP driver: a dedicated Chromium profile driven over the Chrome DevTools Protocol.

* :class:`BrowserLauncher` — find/reuse/launch the profile's browser (127.0.0.1 only).
* :class:`CdpDriver` — Playwright ``connect_over_cdp`` session implementing InstagramDriver.
* :class:`WebApiClient` — same-origin Instagram web API calls from inside the page.
"""

from heliograph.drivers.cdp.capture import CapturedResponse, NetworkCapture
from heliograph.drivers.cdp.devtools import CdpEndpoint
from heliograph.drivers.cdp.launcher import BrowserLauncher, find_browser_executable
from heliograph.drivers.cdp.ratelimit import RateLimiter, limiter_for
from heliograph.drivers.cdp.session import CdpDriver
from heliograph.drivers.cdp.webapi import InstagramApiError, WebApiClient

__all__ = [
    "BrowserLauncher",
    "CapturedResponse",
    "CdpDriver",
    "CdpEndpoint",
    "InstagramApiError",
    "NetworkCapture",
    "RateLimiter",
    "WebApiClient",
    "find_browser_executable",
    "limiter_for",
]
