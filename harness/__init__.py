"""Agentic harness: a deterministic LangGraph FSM orchestrator over pluggable role backends.

The control-flow layer (graph, state, routers) is plain Python and spends zero model
tokens. Role backends (``stub`` / ``claude_sdk`` / ``own``) are the only place that talks
to a model; each is imported lazily so this package can be imported and tested without an
SDK or network. See ``tz.md`` for the full specification.
"""
from __future__ import annotations

__all__: list[str] = []
__version__ = "0.1.0"
