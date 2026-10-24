"""Registro dei tool disponibili all'agente (nativi + MCP)."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..providers.base import ToolSpec

log = logging.getLogger(__name__)


@dataclass
class ToolContext:
    """Contesto passato ai tool: chi chiama, con quale policy, dove salvare i file."""
    user: Any
    policy: dict
    db: Any
    conversation_id: str | None = None
    project_id: str | None = None


@dataclass
class ToolResult:
    content: str                      # testo restituito al modello
    is_error: bool = False
    artifacts: list[dict] | None = None  # es. [{"type":"image","file_id":..., "url":...}] mostrati in chat
    usage: dict | None = None         # consumo del tool: {"provider","model","input_tokens","output_tokens","detail"} (per i costi)


ToolFn = Callable[[dict, ToolContext], Awaitable[ToolResult]]


@dataclass
class Tool:
    spec: ToolSpec
    fn: ToolFn
    usage_kind: str = "tool"  # per le quote: image | video | web_search | tool


_tools: dict[str, Tool] = {}


def register(name: str, description: str, input_schema: dict, usage_kind: str = "tool"):
    def deco(fn: ToolFn):
        _tools[name] = Tool(ToolSpec(name, description, input_schema), fn, usage_kind)
        return fn
    return deco


def register_tool(tool: Tool) -> None:
    _tools[tool.spec.name] = tool


def unregister_prefix(prefix: str) -> None:
    for k in [k for k in _tools if k.startswith(prefix)]:
        _tools.pop(k, None)


def all_tools() -> dict[str, Tool]:
    return _tools


def get_tool(name: str) -> Tool | None:
    return _tools.get(name)
