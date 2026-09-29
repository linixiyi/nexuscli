"""Session-level trace id primitive for cross-surface correlation.

One trace id ties a session's audit records (``trace_id`` field in the audit
JSON) to its LLM requests (``x-request-id`` header). Scope: process-local —
cross-process propagation is explicitly out of the slice.

Design tradeoff (dual layer: module fallback + ContextVar override):

- ``asyncio.gather`` child tasks copy the caller's context (see the concurrent
  read group in ``nexuscli.tools.executor.execute_all``), so with a pure
  ContextVar the *first* consumer that lazily generates an id inside a child
  task would leave sibling tasks each generating their own — the ids would
  diverge exactly where consistency matters.
- Therefore ``get_trace_id`` falls back to a module-level id shared by the
  whole process: one REPL process is "one session", and same-process
  subagents share it, matching the "consistent within a session" contract.
  ``set_trace_id`` pins the id (tests today, future REPL wiring) and stamps
  both layers.
- The functions never await, so the check-then-set in ``get_trace_id`` is
  atomic within a single event loop thread.
"""

from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4

_TRACE_ID: ContextVar[str] = ContextVar("nexuscli_trace_id", default="")
_SESSION_TRACE_ID = ""  # process-wide fallback（模块级，供兄弟 asyncio 任务达成一致）


def new_trace_id() -> str:
    return uuid4().hex[:16]


def set_trace_id(value: str) -> str:
    global _SESSION_TRACE_ID
    _SESSION_TRACE_ID = value
    _TRACE_ID.set(value)
    return value


def get_trace_id() -> str:
    tid = _TRACE_ID.get()
    if tid:
        return tid
    global _SESSION_TRACE_ID
    if not _SESSION_TRACE_ID:
        _SESSION_TRACE_ID = new_trace_id()  # 首个消费者惰性生成，进程内此后稳定
    return _SESSION_TRACE_ID
