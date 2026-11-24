"""Mock public web services; also check the real local stdio connection."""

import asyncio
import logging
from unittest.mock import MagicMock, Mock

import httpx
import pytest
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from agent import config
from mcp_service import server as mcp_server
from mcp_service.client import call_tool, connect_mcp


@pytest.mark.parametrize("outcome", ["results", "empty", "empty_unknown", "no_urls", "timeout", "rate_retry", "failed", "rate_exhausted", "bad_query", "bad_count"])
def test_search_web_results_and_failures(monkeypatch, caplog, outcome):
    caplog.set_level(logging.INFO)
    rows = [{"title": "Doc", "href": "https://example.org", "body": "A snippet."}] * 3
    factory = MagicMock()
    request = factory.return_value.__enter__.return_value.text
    request.return_value = rows
    if outcome == "empty":
        request.return_value = []
    elif outcome == "empty_unknown":
        request.side_effect = DDGSException("No results found.")
    elif outcome == "no_urls":
        request.return_value = [{"title": "Unparsed URL", "body": "Snippet"}]
    elif outcome == "timeout":
        request.side_effect = TimeoutException("Timed out")
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
        assert "stage=search.empty" in caplog.text
    else:
        assert "error" in result
        expected_reason = {
            "empty_unknown": "no_results_unknown", "no_urls": "invalid_results",
            "timeout": "timeout", "failed": "backend_error", "rate_exhausted": "rate_limited",
            "bad_query": "invalid_query", "bad_count": "invalid_count",
        }[outcome]
        assert result["reason"] == expected_reason and result["stage"] in {"search", "validation"}
    if outcome in {"results", "rate_retry"}:
        assert request.call_args.kwargs["backend"] == "auto"
    if outcome == "rate_retry":
        assert request.call_count == 2 and sleep.call_count == 1
    elif outcome == "rate_exhausted":
        assert request.call_count == config.MAX_RETRIES + 1 and sleep.call_count == config.MAX_RETRIES
    elif outcome in {"bad_query", "bad_count"}:
        factory.assert_not_called()


@pytest.mark.parametrize("outcome", ["html", "json", "pdf", "empty", "not_found", "forbidden", "timeout", "timeout_retry", "server_retry", "rate_retry", "rate_exhausted", "bad_url", "missing_type", "missing_type_non_html", "challenge", "js_shell", "fallback_success", "extract_error"])
def test_read_page_html_and_error_results(monkeypatch, caplog, outcome):
    caplog.set_level(logging.INFO)
    html = "<html><body><article><h1>Graphs</h1><p>" + "Useful evidence about stateful graphs. " * 80 + "</p></article></body></html>"
    calls = []

    def respond(request):
        calls.append(request)
        if outcome == "timeout" or outcome == "timeout_retry" and len(calls) == 1:
            raise httpx.ConnectTimeout("Simulated timeout", request=request)
        if outcome == "not_found":
            return httpx.Response(404)
        if outcome == "forbidden":
            return httpx.Response(403, headers={"content-type": "text/plain"}, text="Automated access refused.")
        if outcome == "server_retry" and len(calls) == 1:
            return httpx.Response(503)
        if outcome == "rate_exhausted" or outcome == "rate_retry" and len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        if outcome in {"json", "pdf"}:
            return httpx.Response(200, headers={"content-type": "application/" + outcome}, text="Not HTML")
        if outcome in {"missing_type", "missing_type_non_html"}:
            return httpx.Response(200, content=(html if outcome == "missing_type" else "Not HTML").encode())
        if outcome == "challenge":
            return httpx.Response(200, headers={"content-type": "text/html", "cf-mitigated": "challenge"}, text="<html><title>Just a moment...</title><body>Verify you are human.</body></html>")
        if outcome == "js_shell":
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<html><body><div id='app'></div><script>loadArticle()</script></body></html>")
        text = "<html><body></body></html>" if outcome == "empty" else html
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=text)

    original_client = httpx.Client
    factory = Mock(side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    sleep = Mock()
    monkeypatch.setattr(mcp_server.httpx, "Client", factory)
    monkeypatch.setattr(mcp_server.time, "sleep", sleep)
    extract = Mock(wraps=mcp_server.extract)
    if outcome == "fallback_success":
        extract = Mock(side_effect=[None, "Recovered readable text. " * 100])
    elif outcome == "extract_error":
        extract = Mock(side_effect=ValueError("Parser failed"))
    monkeypatch.setattr(mcp_server, "extract", extract)
    text = mcp_server.read_page("not-a-url" if outcome == "bad_url" else "https://example.org")
    if outcome in {"html", "rate_retry", "timeout_retry", "server_retry", "missing_type", "fallback_success"}:
        assert len(text) == config.MAX_CHARS_PER_PAGE
        assert "<html>" not in text and "stage=extract.success" in caplog.text
        if outcome == "fallback_success":
            assert "Recovered readable text" in text
            assert extract.call_count == 2 and extract.call_args.kwargs["favor_recall"] is True
        else:
            assert "Useful evidence" in text
    else:
        assert text.startswith("Error:")
        stage = "fetch"
        if outcome in {"json", "pdf", "missing_type_non_html"}:
            stage = "content_type"
        elif outcome in {"empty", "js_shell", "extract_error"}:
            stage = "extract"
        elif outcome == "challenge":
            stage = "page_check"
        elif outcome == "bad_url":
            stage = "validation"
        assert f"[{stage}]" in text
        if stage in {"fetch", "content_type", "page_check", "validation"}:
            extract.assert_not_called()
    if outcome == "bad_url":
        factory.assert_not_called()
    if outcome == "rate_retry":
        assert len(calls) == 2 and sleep.call_count == 1
    if outcome == "rate_exhausted":
        assert len(calls) == config.MAX_RETRIES + 1 and sleep.call_count == config.MAX_RETRIES
    if outcome == "timeout":
        assert len(calls) == config.MAX_RETRIES + 1 and sleep.call_count == config.MAX_RETRIES
    if outcome in {"timeout_retry", "server_retry"}:
        assert len(calls) == 2 and sleep.call_count == 1
    if outcome == "forbidden":
        assert len(calls) == 1 and "Automated access refused" in caplog.text
    if outcome == "missing_type":
        assert "stage=content_type.sniffed_html" in caplog.text
    if outcome in {"empty", "js_shell"}:
        assert extract.call_count == 2 and "stage=extract.empty" in caplog.text


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
