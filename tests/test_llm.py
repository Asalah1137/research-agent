"""LLM recovery and token accounting use mocked Groq replies."""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import httpx
import pytest
from groq import APIConnectionError, RateLimitError

from agent import config, llm
from agent.state import ResearchPlan


@pytest.mark.parametrize("outcome, tokens", [("valid", 18), ("invalid", 18), ("request_error", 12)])
def test_json_retry_and_spent_tokens(monkeypatch, plan, outcome, tokens):
    second = (plan.model_dump_json(), 11)
    if outcome == "invalid":
        second = ("still invalid", 11)
    elif outcome == "request_error":
        second = llm.LLMError("Provider unavailable", 5)
    request = Mock(side_effect=[("not JSON", 7), second])
    monkeypatch.setattr(llm, "ask_llm_with_usage", request)
    if outcome == "valid":
        reply, used = llm.ask_json("Return a research plan as JSON.", ResearchPlan)
        assert reply == plan and used == tokens
    else:
        with pytest.raises(llm.LLMError) as failure:
            llm.ask_json("Return a research plan as JSON.", ResearchPlan)
        assert failure.value.tokens_used == tokens
    assert request.call_count == 2
    assert "previous reply was not valid JSON" in request.call_args.args[0]


@pytest.mark.parametrize("kind, succeeds", [
    ("rate", True), ("rate", False), ("connection", True), ("connection", False),
])
def test_transient_groq_retries_are_bounded(monkeypatch, kind, succeeds):
    request_data = httpx.Request("POST", "https://api.groq.com")
    error = APIConnectionError(request=request_data)
    if kind == "rate":
        error = RateLimitError("Rate limited", body=None,
                               response=httpx.Response(429, request=request_data, headers={"retry-after": "0.1"}))
    response = SimpleNamespace(usage=SimpleNamespace(total_tokens=55),
                               choices=[SimpleNamespace(message=SimpleNamespace(content="A reply."))])
    factory = MagicMock()
    request = factory.return_value.__enter__.return_value.chat.completions.create
    request.side_effect = [error, response] if succeeds else [error] * (config.MAX_RETRIES + 1)
    sleep = Mock()
    monkeypatch.setattr(llm, "Groq", factory)
    monkeypatch.setattr(llm.time, "sleep", sleep)
    if succeeds:
        assert llm.ask_llm_with_usage("Hello") == ("A reply.", 55)
        assert request.call_count == 2
        sleep.assert_called_once_with(0.1 if kind == "rate" else 5.0)
    else:
        with pytest.raises(llm.LLMError) as failure:
            llm.ask_llm_with_usage("Hello")
        assert failure.value.tokens_used == 0
        assert request.call_count == config.MAX_RETRIES + 1
        assert sleep.call_count == config.MAX_RETRIES
    assert factory.call_args.kwargs["max_retries"] == 0
    assert request.call_args.kwargs["model"] == config.MODEL_NAME


@pytest.mark.parametrize("content, choices", [("A reply.", True), (" ", True), (None, True), (None, False)])
def test_reply_usage_and_empty_response_errors(monkeypatch, content, choices):
    response = SimpleNamespace(
        usage=SimpleNamespace(total_tokens=17),
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))] if choices else [],
    )
    factory = MagicMock()
    factory.return_value.__enter__.return_value.chat.completions.create.return_value = response
    monkeypatch.setattr(llm, "Groq", factory)
    if content == "A reply.":
        assert llm.ask_llm_with_usage("Hello") == ("A reply.", 17)
        assert llm.ask_llm("Hello") == "A reply."
    else:
        with pytest.raises(llm.LLMError) as failure:
            llm.ask_llm_with_usage("Hello")
        assert failure.value.tokens_used == 17


def test_missing_key_and_error_redaction(monkeypatch):
    assert "test-key" not in str(llm.LLMError("Invalid key: test-key", 7))
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    factory = Mock()
    monkeypatch.setattr(llm, "Groq", factory)
    with pytest.raises(llm.LLMError, match="GROQ_API_KEY"):
        llm.ask_llm("Hello")
    factory.assert_not_called()
