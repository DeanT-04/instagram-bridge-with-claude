"""Capture only the Instagram app window to a PNG.

Primary: ``PrintWindow(hwnd, PW_RENDERFULLCONTENT)`` into a memory DC — works when the
window is occluded (not when minimised; the driver restores it first). Hardware-decoded
video frames may render black this way. Fallback: ``PIL.ImageGrab`` of the window rect
(needs the window to be visible on screen). Thread-agnostic (plain GDI, no COM).
"""

from __future__ import annotations

import ctypes
from pathlib import Path

from heliograph.drivers.uia.runtime import make_dpi_aware
from heliograph.errors import DriverUnavailableError

__all__ = ["PW_RENDERFULLCONTENT", "capture_window"]

PW_RENDERFULLCONTENT = 2


def _print_window(hwnd: int, path: Path) -> bool:
    import win32gui
    import win32ui
    from PIL import Image

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return False
    hdc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(hdc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(src, width, height)
        mem.SelectObject(bmp)
        ok = ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), PW_RENDERFULLCONTENT)
        if not ok:
            return False
        info = bmp.GetInfo()
        image = Image.frombuffer(
            "RGB",
            (info["bmWidth"], info["bmHeight"]),
            bmp.GetBitmapBits(True),
            "raw",
            "BGRX",
            0,
            1,
        )
        if image.getbbox() is None:  # all black: capture failed silently
            return False
        image.save(path, "PNG")
        return True
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc)


def _grab(hwnd: int, path: Path) -> bool:
    import win32gui
    from PIL import ImageGrab

    bbox = win32gui.GetWindowRect(hwnd)
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return False
    ImageGrab.grab(bbox=bbox, all_screens=True).save(path, "PNG")
    return True


def capture_window(hwnd: int, path: Path) -> tuple[Path, str]:
    """Save a PNG of window ``hwnd`` to ``path``; returns ``(path, method)``.

    Raises:
        DriverUnavailableError: neither method produced an image (e.g. window minimised).
    """
    make_dpi_aware()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if _print_window(hwnd, path):
            return path, "print_window"
    except Exception:
        pass
    try:
        if _grab(hwnd, path):
            return path, "image_grab"
    except Exception as exc:
        raise DriverUnavailableError(f"could not capture window: {exc}") from exc
    raise DriverUnavailableError("could not capture window (is it minimised?)")
