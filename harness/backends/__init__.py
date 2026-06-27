"""Role backends. Only the SDK-free backends are re-exported here; the ``claude_sdk``
backend must be imported explicitly so importing this package never pulls in the SDK.
"""
from __future__ import annotations

from harness.backends.base import RoleBackend, RoleContext, RoleResult
from harness.backends.stub import StubBackend

__all__ = ["RoleBackend", "RoleContext", "RoleResult", "StubBackend"]
