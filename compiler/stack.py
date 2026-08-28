"""Shared Python-stack reservation for the compiler's recursive walks.

Several phases bound how deeply a program may nest and promise a diagnostic —
never a `RecursionError` — when that bound is exceeded. Those bounds are counted
in language levels, but they are enforced in Python frames, so the promise only
holds if the interpreter can actually reach the bound from wherever the caller
happens to be. An embedding tool, a language server, or a future self-hosted
driver does not start at a shallow stack.

`sys.setrecursionlimit` is interpreter-global, so reservations are serialized.
The lock is an `RLock` so a nested reservation on one thread cannot deadlock,
and so save/restore pairs nest correctly.
"""

from __future__ import annotations

import contextlib
import sys
import threading

RECURSION_LIMIT_LOCK = threading.RLock()

# Slack for the driver, the CLI, and the tail of each walk beyond its own frames.
STACK_MARGIN = 96


def stack_depth() -> int:
    depth = 0
    frame: object = sys._getframe()
    while frame is not None:
        depth += 1
        frame = frame.f_back
    return depth


@contextlib.contextmanager
def reserved(frames: int):
    """Guarantee `frames` Python frames are available, then restore the limit."""
    with RECURSION_LIMIT_LOCK:
        required = frames + STACK_MARGIN
        previous_limit = sys.getrecursionlimit()
        depth = stack_depth()
        if previous_limit - depth < required:
            sys.setrecursionlimit(depth + required)
        try:
            yield
        finally:
            sys.setrecursionlimit(previous_limit)
