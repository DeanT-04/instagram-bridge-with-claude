"""Spans: ``with eye.span(...)``, ``async with eye.span(...)`` and ``@eye.traced``.

A span records name, attributes, start time, duration, status and (on failure) the
exception type, message and traceback. Spans nest: a span opened inside another inherits
its ``trace_id`` and records the outer ``span_id`` as ``parent_id``. Exceptions are
recorded and then re-raised unchanged.
"""

from __future__ import annotations

import functools
import inspect
import time
import traceback
from collections.abc import Callable, Coroutine
from contextvars import Token
from pathlib import Path
from types import TracebackType
from typing import Any, ParamSpec, TypeVar, cast, overload

from heliograph.eye.context import ActiveSpan, _current, current_span
from heliograph.eye.core import get_eye
from heliograph.eye.events import Artifact, ErrorInfo, Event, new_id, utcnow_iso
from heliograph.eye.redact import REDACTED, is_sensitive_key, redact

__all__ = ["attach_artifact", "event", "span", "summarize_args", "traced"]

P = ParamSpec("P")
R = TypeVar("R")

_MAX_REPR = 200
# Message bodies (comment/DM/typed text) are personal content: traces keep only their length.
_CONTENT_ARGS = frozenset({"text", "comment_text", "message"})
_MAX_TB = 8000
ErrorHook = Callable[[ActiveSpan, BaseException], None]


def _short(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_REPR else value[:_MAX_REPR] + "…"
    if isinstance(value, Path):
        return str(value)
    try:
        text = repr(value)
    except Exception:
        text = f"<{type(value).__name__}>"
    return text if len(text) <= _MAX_REPR else text[:_MAX_REPR] + "…"


def summarize_args(
    func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """Return a safe, truncated, redacted summary of a call's arguments.

    ``self``/``cls`` are skipped; values under sensitive parameter names are masked, and
    message bodies (``text``...) are reduced to their length.
    """
    try:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
        items = list(bound.arguments.items())
    except (TypeError, ValueError):
        items = [(f"arg{i}", a) for i, a in enumerate(args)] + list(kwargs.items())
    out: dict[str, Any] = {}
    for key, val in items:
        if key in ("self", "cls"):
            continue
        if is_sensitive_key(key):
            out[key] = REDACTED
        elif key in _CONTENT_ARGS and isinstance(val, str):
            out[key] = f"<{len(val)} chars>"
        else:
            out[key] = _short(val)
    return cast(dict[str, Any], redact(out))


class span:  # lowercase: used like a function, ``with span("x"):``
    """Context manager recording one span; usable with ``with`` and ``async with``.

    Args:
        name: Operation name, dotted by convention (``cdp.navigate``, ``mcp.get_saved``).
        on_error: Optional callback run (synchronously) before the event is written when
            the block raises — e.g. to take a screenshot and :func:`attach_artifact` it.
        **attrs: Attributes recorded with the span (redacted before writing).

    The yielded :class:`ActiveSpan` supports ``.set(**attrs)`` to add attributes later.
    A ``span`` instance is single-use.
    """

    def __init__(self, name: str, *, on_error: ErrorHook | None = None, **attrs: Any) -> None:
        self.name = name
        self.on_error = on_error
        self.attrs = attrs
        self._token: Token[ActiveSpan | None] | None = None
        self._active: ActiveSpan | None = None
        self._t0 = 0.0
        self._wall = 0.0

    def __enter__(self) -> ActiveSpan:
        parent = current_span()
        self._active = ActiveSpan(
            name=self.name,
            trace_id=parent.trace_id if parent else new_id(32),
            span_id=new_id(),
            parent_id=parent.span_id if parent else None,
            attrs=dict(self.attrs),
        )
        self._token = _current.set(self._active)
        self._wall = time.time()
        self._t0 = time.perf_counter()
        return self._active

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        duration_ms = (time.perf_counter() - self._t0) * 1000
        active = self._active
        assert active is not None and self._token is not None, "span exited before entered"
        error: ErrorInfo | None = None
        if exc is not None:
            if self.on_error is not None:
                try:
                    self.on_error(active, exc)
                except Exception as hook_exc:
                    active.attrs["on_error_failed"] = repr(hook_exc)
            error = ErrorInfo(
                type=type(exc).__name__,
                message=str(exc)[:2000],
                traceback="".join(traceback.format_exception(exc_type, exc, tb))[-_MAX_TB:],
            )
        try:
            _current.reset(self._token)
        except ValueError:  # exited in a different context (e.g. generator finalisation)
            _current.set(None)
        get_eye().emit(
            Event(
                kind="span",
                name=active.name,
                ts=utcnow_iso(self._wall),
                ts_epoch=self._wall,
                trace_id=active.trace_id,
                span_id=active.span_id,
                parent_id=active.parent_id,
                status="error" if exc is not None else "ok",
                duration_ms=round(duration_ms, 3),
                attrs=active.attrs,
                error=error,
                artifacts=active.artifacts,
            )
        )

    async def __aenter__(self) -> ActiveSpan:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.__exit__(exc_type, exc, tb)


@overload
def traced(func: Callable[P, R], /) -> Callable[P, R]: ...
@overload
def traced(
    *, name: str | None = None, capture_args: bool = True
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def traced(
    func: Callable[P, R] | None = None,
    /,
    *,
    name: str | None = None,
    capture_args: bool = True,
) -> Callable[P, R] | Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorate a sync or async function so each call is recorded as a span.

    Usable bare (``@traced``) or configured (``@traced(name="cdp.fetch")``). The span
    name defaults to ``<module leaf>.<qualname>``; argument summaries go in
    ``attrs["args"]`` unless ``capture_args=False``.
    """

    def decorate(fn: Callable[P, R]) -> Callable[P, R]:
        span_name = name or f"{fn.__module__.rsplit('.', 1)[-1]}.{fn.__qualname__}"

        def _attrs(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
            return {"args": summarize_args(fn, args, kwargs)} if capture_args else {}

        if inspect.iscoroutinefunction(fn):
            coro_fn = cast(Callable[P, Coroutine[Any, Any, Any]], fn)

            @functools.wraps(fn)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                async with span(span_name, **_attrs(args, kwargs)):
                    return await coro_fn(*args, **kwargs)

            return cast(Callable[P, R], async_wrapper)

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with span(span_name, **_attrs(args, kwargs)):
                return fn(*args, **kwargs)

        return wrapper

    return decorate(func) if func is not None else decorate


def attach_artifact(path: str | Path, kind: str) -> None:
    """Link a file (``kind`` e.g. "screenshot", "uia_snapshot", "dom") to the current span.

    Outside any span a standalone ``artifact`` event is written instead.
    """
    art = Artifact(path=str(path), kind=kind)
    active = current_span()
    if active is not None:
        active.artifacts.append(art)
        return
    get_eye().emit(
        Event(kind="artifact", name=f"artifact.{kind}", trace_id=new_id(32), artifacts=[art])
    )


def event(name: str, *, error: bool = False, **attrs: Any) -> None:
    """Record a point-in-time event (no duration) within the current trace, if any."""
    active = current_span()
    get_eye().emit(
        Event(
            kind="event",
            name=name,
            trace_id=active.trace_id if active else new_id(32),
            parent_id=active.span_id if active else None,
            status="error" if error else "ok",
            attrs=attrs,
        )
    )
