from __future__ import annotations
from typing import Callable, Optional

# Simple world initializer seam.
# Phase 1 keeps behavior identical: we just centralize the one-time world init call.

_initializer: Optional[Callable[[], None]] = None
_done: bool = False

def register_initializer(fn: Callable[[], None]) -> None:
    global _initializer
    _initializer = fn


def reset() -> None:
    """For tests/dev only: allow re-running initialization."""
    global _done
    _done = False


def ensure_initialized(fallback: Optional[Callable[[], None]] = None) -> None:
    """Ensure the world is initialized once.
    - If a registered initializer exists, use it.
    - Else, if a fallback is provided (callable), use that once.
    - Otherwise, no-op.
    """
    global _done
    if _done:
        return
    fn = _initializer or fallback
    if fn is None:
        return
    fn()
    _done = True
