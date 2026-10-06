# COPIED by tools/sync-from-lumr.sh from lumr-studio/server/lumr_studio/mcp_results.py. Do not edit: change it in Lumr Studio and run the script.
"""How every MCP tool answers: shared by server.py and overlay_mcp.py.

One place for the three helpers both wiring modules need, so the overlay tools
answer and fail exactly the way the edit tools do. It lives apart from
server.py because overlay_mcp imports it and server.py imports overlay_mcp.

Results: for a returned dict, mcp 2.2.0 builds the text block with
``pydantic_core.to_json(indent=2)``, which pads every result by about 40%.
``result`` builds a ``CallToolResult`` instead: compact JSON (or a given text)
as the text block, and the full dict as ``structured_content``.
"""

import json
import logging
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from desk_crit.errors import StudioError
from desk_crit.jobs import describe_failure

logger = logging.getLogger("desk_crit")


def tool_annotations(title: str, read_only: bool) -> ToolAnnotations:
    """Tool annotations: a title, whether it only reads, and never destructive."""
    return ToolAnnotations(title=title, read_only_hint=read_only, destructive_hint=False)


def run(fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run a tool function, turning every failure into a readable tool error.

    A ``StudioError`` keeps its message, which already says how to fix it. Any
    other exception is logged with its traceback to stderr and answered with
    ``describe_failure``.
    """
    try:
        return fn(*args, **kwargs)
    except StudioError as exc:
        raise ToolError(str(exc)) from None
    except Exception as exc:
        logger.exception("tool %s crashed", fn.__name__)
        raise ToolError(describe_failure(exc)) from None


def json_text(data: dict[str, Any]) -> TextContent:
    """``data`` as one compact JSON text block."""
    return TextContent(type="text", text=json.dumps(data, separators=(",", ":"), ensure_ascii=False))


def result(data: dict[str, Any], text: str | None = None) -> CallToolResult:
    """``data`` as structured content, with compact JSON (or ``text``) as the text block."""
    block = json_text(data) if text is None else TextContent(type="text", text=text)
    return CallToolResult(content=[block], structured_content=data)
