"""Shared test data and isolation from local settings and external services."""

import httpx
import pytest

from agent import config
from agent.state import ResearchPlan, Source


@pytest.fixture(autouse=True)
def offline_settings(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(config, "MAX_REFINEMENT_ROUNDS", 2)
    monkeypatch.setattr(config, "MAX_RETRIES", 2)
    monkeypatch.setattr(config, "MAX_SEARCH_RESULTS", 5)
    monkeypatch.setattr(config, "MAX_PAGES_READ", 5)
    monkeypatch.setattr(config, "MAX_CHARS_PER_PAGE", 1000)
    monkeypatch.setattr(config, "SEARCH_BACKEND", "auto")
    monkeypatch.setattr(config, "LOG_LEVEL", "INFO")
    attempted_requests = []

    def block_request(transport, request):
        attempted_requests.append(str(request.url))
        raise AssertionError("Use a mock instead of an external HTTP request.")

    async def block_async_request(transport, request):
        return block_request(transport, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", block_request)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", block_async_request)
    yield
    assert not attempted_requests, "A test attempted an external HTTP request."


@pytest.fixture
def source():
    return Source(
        number=1, title="Known source", url="https://example.org/known",
        text="LangGraph supports stateful workflows.",
    )


@pytest.fixture
def plan():
    return ResearchPlan(
        scope="Explain stateful workflows", subquestions=["When are loops useful?"],
        queries=["LangGraph", "LangGraph state", "LangGraph loops"],
    )


@pytest.fixture
def report():
    return (
        "## Summary\n\nLatest draft [1].\n\n## Findings\n\nStateful workflows [1]."
        "\n\n## Limitations\n\nA draft.\n\n## References\n\n"
        "[1] Known source - https://example.org/known"
    )
