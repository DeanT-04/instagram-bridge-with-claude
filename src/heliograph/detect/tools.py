"""Detection of external tools (ffmpeg/ffprobe) and optional Python packages."""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import shutil
import subprocess
import sys

from heliograph.detect.models import PackageInfo, ToolInfo
from heliograph.eye import span

__all__ = ["detect_package", "detect_tool"]


def _which(name: str) -> str | None:
    """``shutil.which`` without the implicit current-directory lookup on Windows.

    On Windows ``shutil.which`` searches ``.`` before ``PATH``, so running
    ``heliograph doctor`` inside a folder containing e.g. a planted ``ffmpeg.exe`` would
    execute it. Only absolute ``PATH`` entries are searched here.
    """
    if sys.platform != "win32" or os.path.dirname(name):
        return shutil.which(name)
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if entry and os.path.isabs(entry):
            found = shutil.which(os.path.join(entry, name))
            if found:
                return found
    return None


def detect_tool(name: str, *, version_arg: str = "-version", timeout: float = 10) -> ToolInfo:
    """Find ``name`` on PATH and read the first line of its version output. Never raises."""
    with span("detect.tool", tool=name) as s:
        path = _which(name)
        if not path:
            s.set(found=False)
            return ToolInfo(name=name, found=False, reason=f"{name} not found on PATH")
        try:
            proc = subprocess.run(
                [path, version_arg], capture_output=True, text=True, timeout=timeout,
                check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            first = (proc.stdout or proc.stderr).strip().splitlines()
            version = first[0].split(" Copyright")[0][:120] if first else None
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolInfo(name=name, found=True, path=path, reason=f"version check failed: {exc}")
        s.set(found=True, version=version)
        return ToolInfo(name=name, found=True, path=path, version=version)


def detect_package(module: str, dist: str | None = None) -> PackageInfo:
    """Check that ``module`` imports and report its distribution version. Never raises."""
    with span("detect.package", package=module) as s:
        try:
            importlib.import_module(module)
        except Exception as exc:
            s.set(importable=False)
            return PackageInfo(name=module, importable=False, reason=f"{type(exc).__name__}: {exc}")
        try:
            version: str | None = importlib.metadata.version(dist or module)
        except importlib.metadata.PackageNotFoundError:
            version = None
        s.set(importable=True, version=version)
        return PackageInfo(name=module, importable=True, version=version)
