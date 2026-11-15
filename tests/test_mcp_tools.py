"""Mock public web services; also check the real local stdio connection."""

import asyncio
from unittest.mock import MagicMock, Mock

import httpx
import pytest
from ddgs.exceptions import DDGSException, RatelimitException

from agent import config
from mcp_service import server as mcp_server
from mcp_service.client import call_tool, connect_mcp


@pytest.mark.parametrize("outcome", ["results", "empty", "rate_retry", "failed", "rate_exhausted", "bad_query", "bad_count"])
def test_search_web_results_and_failures(monkeypatch, outcome):
    rows = [{"title": "Doc", "href": "https://example.org", "body": "A snippet."}] * 3
    factory = MagicMock()
    request = factory.return_value.__enter__.return_value.text
    request.return_value = rows
    if outcome == "empty":
        request.side_effect = DDGSException("No results found.")
    elif outcome == "rate_retry":
        request.side_effect = [RatelimitException("Rate limited"), rows]
    elif outcome == "failed":
        request.side_effect = RuntimeError("Search unavailable")
    elif outcome == "rate_exhausted":
        request.side_effect = RatelimitException("Rate limited")
    sleep = Mock()
    monkeypatch.setattr(mcp_server, "DDGS", factory)
    monkeypatch.setattr(mcp_server.time, "sleep", sleep)
    result = mcp_server.search_web(" " if outcome == "bad_query" else "LangGraph", 0 if outcome == "bad_count" else 2)
    if outcome in {"results", "rate_retry"}:
        assert result == [{"title": "Doc", "url": "https://example.org", "snippet": "A snippet."}] * 2
        assert request.call_args.kwargs["max_results"] == 2
    elif outcome == "empty":
        assert result == []
    else:
        assert "error" in result
    if outcome == "rate_retry":
        assert request.call_count == 2 and sleep.call_count == 1
    elif outcome == "rate_exhausted":
        assert request.call_count == config.MAX_RETRIES + 1 and sleep.call_count == config.MAX_RETRIES
    elif outcome in {"bad_query", "bad_count"}:
        factory.assert_not_called()


@pytest.mark.parametrize("outcome", ["html", "json", "pdf", "empty", "not_found", "timeout", "rate_retry", "rate_exhausted", "bad_url"])
def test_read_page_html_and_error_results(monkeypatch, outcome):
    html = "<html><body><article><h1>Graphs</h1><p>" + "Useful evidence about stateful graphs. " * 80 + "</p></article></body></html>"
    calls = []

    def respond(request):
        calls.append(request)
        if outcome == "timeout":
            raise httpx.ConnectTimeout("Simulated timeout", request=request)
        if outcome == "not_found":
            return httpx.Response(404)
        if outcome == "rate_exhausted" or outcome == "rate_retry" and len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        if outcome in {"json", "pdf"}:
            return httpx.Response(200, headers={"content-type": "application/" + outcome}, text="Not HTML")
        text = "<html><body></body></html>" if outcome == "empty" else html
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=text)

    original_client = httpx.Client
    factory = Mock(side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    sleep = Mock()
    monkeypatch.setattr(mcp_server.httpx, "Client", factory)
    monkeypatch.setattr(mcp_server.time, "sleep", sleep)
    text = mcp_server.read_page("not-a-url" if outcome == "bad_url" else "https://example.org")
    if outcome in {"html", "rate_retry"}:
        assert len(text) == config.MAX_CHARS_PER_PAGE
        assert "Useful evidence" in text and "<html>" not in text
    else:
        assert text.startswith("Error:")
    if outcome == "bad_url":
        factory.assert_not_called()
    if outcome == "rate_retry":
        assert len(calls) == 2 and sleep.call_count == 1
    if outcome == "rate_exhausted":
        assert len(calls) == config.MAX_RETRIES + 1 and sleep.call_count == config.MAX_RETRIES


def test_local_stdio_tools_survive_invalid_requests():
    async def check():
        async with connect_mcp() as session:
            names = sorted(tool.name for tool in (await session.list_tools()).tools)
            assert names == ["read_page", "search_web"]
            assert (await call_tool(session, "read_page", {"url": "not-a-url"})).startswith("Error:")
            result = await call_tool(session, "search_web", {"query": "", "max_results": 2})
            assert "error" in result
            assert sorted(tool.name for tool in (await session.list_tools()).tools) == names
    asyncio.run(check())
