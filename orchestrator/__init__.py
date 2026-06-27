"""
Deterministic orchestrator for the dev <-> reviewer <-> tester loop.

The state machine is plain Python (zero model tokens); each worker role runs as its
own top-level Claude Agent SDK `query()`. Entry point: `python -m orchestrator <branch>`.
"""
from .machine import run

__all__ = ["run"]
