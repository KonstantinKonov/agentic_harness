"""Postgres checkpointer for BranchState persistence.

In the hybrid state model (tz.md) the machine state is persisted by LangGraph's Postgres
checkpointer (this module); the rendered views live on disk (store.py). langgraph is
imported lazily so the rest of the package stays import-light and SDK/infra-free to test.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.checkpoint.postgres import PostgresSaver

DEFAULT_URI = "postgresql://harness:harness@localhost:5432/app_checkpointer"


def checkpointer_uri() -> str:
    return os.environ.get("CHECKPOINTER_DB_URI", DEFAULT_URI)


@contextmanager
def open_checkpointer(uri: str | None = None) -> Iterator[PostgresSaver]:
    """Yield a set-up Postgres checkpointer, creating its tables on first use."""
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(uri or checkpointer_uri()) as saver:
        saver.setup()
        yield saver
