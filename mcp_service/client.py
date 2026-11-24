"""Start the local MCP server and call its tools over stdio."""

import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent import config

logger = logging.getLogger(__name__)

@asynccontextmanager
async def connect_mcp() -> AsyncIterator[ClientSession]:
    """Keep one server connection open for a group of tool calls."""
    project_root = Path(__file__).resolve().parents[1]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_service.server"],
        cwd=project_root,
        env=os.environ.copy(),
    )

    async with stdio_client(parameters) as (read_stream, write_stream):
        # Allow time for the tool's web request and rate-limit retries.
        timeout = (config.REQUEST_TIMEOUT + 60) * (max(0, config.MAX_RETRIES) + 1)
        async with ClientSession(read_stream, write_stream, read_timeout_seconds=timeout) as session:
            await session.initialize()
            yield session


async def call_tool(
    session: ClientSession, name: str, arguments: dict[str, str | int]
) -> list[dict[str, str]] | dict[str, str] | str:
    """Call a tool and unwrap the SDK's structured result."""
    logger.info("stage=mcp.request tool=%s", name)
    try:
        result = await session.call_tool(name, arguments)
    except Exception as exc:
        logger.warning("stage=mcp.transport_failed tool=%s error_type=%s message=%s", name, type(exc).__name__, exc)
        return {"error": f"MCP tool {name} failed: {exc}"}
    if result.is_error:
        message = "\n".join(block.text for block in result.content if block.type == "text")
        logger.warning("stage=mcp.tool_failed tool=%s message=%s", name, message)
        return {"error": message or f"Tool {name} failed."}

    # The SDK wraps both of our tools' return types in {"result": ...}.
    data = result.structured_content
    if not data or "result" not in data:
        logger.warning("stage=mcp.decode_failed tool=%s", name)
        return {"error": f"Tool {name} returned no structured result."}
    return data["result"]
