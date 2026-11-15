"""Empty searches and failed pages should not lose sources or token usage."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent import llm, helpers
from mcp_service.client import call_tool


@pytest.mark.parametrize("outcome", ["new_page", "empty_again", "rephrase_error", "connect_error"])
def test_empty_search_recovery_and_source_numbers(monkeypatch, source, outcome):
    existing = source.model_copy(update={"number": 4})
    searches, reads = [], []

    @asynccontextmanager
    async def connection():
        if outcome == "connect_error":
            raise RuntimeError("MCP unavailable")
        yield object()

    async def tool(session, name, arguments):
        if name == "search_web":
            searches.append(arguments["query"])
            if len(searches) == 1 or outcome == "empty_again":
                return []
            return [
                {"title": "LangGraph blocked", "url": "https://example.org/bad", "snippet": ""},
                {"title": "Useful doc", "url": "https://example.org/new", "snippet": ""},
                {"title": "Duplicate", "url": "https://example.org/new", "snippet": ""},
                {"title": "Already read", "url": existing.url, "snippet": ""},
            ]
        reads.append(arguments["url"])
        return "Error: blocked page" if arguments["url"].endswith("/bad") else "Cleaned evidence."

    rephrase = Mock(return_value=("broader query", 13))
    if outcome == "rephrase_error":
        rephrase.side_effect = llm.LLMError("Could not rephrase", 7)
    monkeypatch.setattr(helpers, "connect_mcp", connection)
    monkeypatch.setattr(helpers, "call_tool", tool)
    monkeypatch.setattr(llm, "ask_llm_with_usage", rephrase)
    sources, tokens = asyncio.run(helpers.collect_sources(["empty query"], "LangGraph", [existing], 1))
    assert sources[0] == existing
    if outcome == "new_page":
        assert searches == ["empty query", "broader query"]
        assert reads == ["https://example.org/bad", "https://example.org/new"]
        assert len(sources) == 2 and sources[1].number == 5 and tokens == 13
    else:
        assert sources == [existing] and not reads
        assert tokens == {"empty_again": 13, "rephrase_error": 7, "connect_error": 0}[outcome]
    assert rephrase.call_count == (0 if outcome == "connect_error" else 1)
    assert len(searches) <= 2


def test_mcp_client_returns_transport_error():
    session = SimpleNamespace(call_tool=AsyncMock(side_effect=RuntimeError("Disconnected")))
    result = asyncio.run(call_tool(session, "search_web", {"query": "LangGraph", "max_results": 2}))
    assert "Disconnected" in result["error"]
