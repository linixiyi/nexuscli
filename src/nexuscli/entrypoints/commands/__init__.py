"""commands — REPL slash-command and lifecycle handlers, split out of repl.py.

One module per command domain; every function here is a verbatim extraction
from the former monolithic repl.py. The main loop, the ``_handle_slash``
dispatch skeleton and the prompt/toolbar rendering assembly stay in
``nexuscli.entrypoints.repl``, which imports these names back — names that
tests (or callers) historically imported from ``repl`` keep working because
repl re-exports them.
"""
