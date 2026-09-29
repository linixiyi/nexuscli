"""run.py — Agent event-pump plumbing shared by the main loop and command handlers.

``_run_events`` drains an agent's async event stream into the renderer;
``_run_agent`` is the one-turn wrapper over it. Both were extracted verbatim
from repl.py so command modules (e.g. custom_cmds) can reuse them without
importing the REPL module.
"""

from __future__ import annotations

from contextlib import suppress

from nexuscli.agent import Agent
from nexuscli.render import RichRenderer


async def _run_agent(agent: Agent, renderer: RichRenderer, message: str) -> None:
    await _run_events(agent.run(message), renderer, agent.llm_client.max_context_window)


async def _run_events(events, renderer: RichRenderer, context_window: int | None = None) -> None:
    renderer.set_context_window(context_window)
    renderer.start_run()
    renderer.newline()
    try:
        async for event in events:
            renderer.handle(event)
            if event.get("type") == "error":
                break
    finally:
        # Close the async generator so agent-level finally blocks (post-turn
        # snapshot, HTTP client cleanup) run even when we break early.
        with suppress(Exception):
            await events.aclose()
    renderer.newline()
