"""Provider recovery and token accounting, including OpenRouter SDK wire checks."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import httpx
import pytest
from groq import APIConnectionError, RateLimitError
from openai import APIConnectionError as OpenAIConnectionError
from openai import RateLimitError as OpenAIRateLimitError

from agent import config, llm
from agent.state import ResearchPlan


@pytest.fixture(params=["groq", "openrouter"])
def provider(monkeypatch, request):
    monkeypatch.setattr(config, "LLM_PROVIDER", request.param)
    groq = request.param == "groq"
    return SimpleNamespace(
        sdk="Groq" if groq else "OpenAI",
        model=config.MODEL_NAME if groq else config.OPENROUTER_MODEL_NAME,
        connection_error=APIConnectionError if groq else OpenAIConnectionError,
        rate_limit_error=RateLimitError if groq else OpenAIRateLimitError,
        key_setting="GROQ_API_KEY" if groq else "OPENROUTER_API_KEY",
    )


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
def test_transient_provider_retries_are_bounded(monkeypatch, provider, kind, succeeds):
    request_data = httpx.Request("POST", "https://example.org/chat/completions")
    error = provider.connection_error(request=request_data)
    if kind == "rate":
        error = provider.rate_limit_error("Rate limited", body=None,
                                          response=httpx.Response(429, request=request_data, headers={"retry-after": "0.1"}))
    response = SimpleNamespace(usage=SimpleNamespace(total_tokens=55),
                               choices=[SimpleNamespace(message=SimpleNamespace(content="A reply."))])
    factory = MagicMock()
    request = factory.return_value.__enter__.return_value.chat.completions.create
    request.side_effect = [error, response] if succeeds else [error] * (config.MAX_RETRIES + 1)
    sleep = Mock()
    monkeypatch.setattr(llm, provider.sdk, factory)
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
    assert request.call_args.kwargs["model"] == provider.model
    if provider.sdk == "Groq":
        assert "base_url" not in factory.call_args.kwargs
        assert "extra_body" not in request.call_args.kwargs
    else:
        assert factory.call_args.kwargs["base_url"] == "https://openrouter.ai/api/v1"
        assert request.call_args.kwargs["extra_body"] == {"reasoning": {"enabled": True}}


@pytest.mark.parametrize("content, choices", [("A reply.", True), (" ", True), (None, True), (None, False)])
def test_reply_usage_and_empty_response_errors(monkeypatch, provider, content, choices):
    response = SimpleNamespace(
        usage=SimpleNamespace(total_tokens=17),
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))] if choices else [],
    )
    factory = MagicMock()
    factory.return_value.__enter__.return_value.chat.completions.create.return_value = response
    monkeypatch.setattr(llm, provider.sdk, factory)
    if content == "A reply.":
        assert llm.ask_llm_with_usage("Hello") == ("A reply.", 17)
        assert llm.ask_llm("Hello") == "A reply."
    else:
        with pytest.raises(llm.LLMError) as failure:
            llm.ask_llm_with_usage("Hello")
        assert failure.value.tokens_used == 17


def test_missing_key_and_error_redaction(monkeypatch, provider):
    message = str(llm.LLMError("Invalid keys: test-key and test-openrouter-key", 7))
    assert "test-key" not in message and "test-openrouter-key" not in message
    monkeypatch.setattr(config, provider.key_setting, "")
    factory = Mock()
    monkeypatch.setattr(llm, provider.sdk, factory)
    with pytest.raises(llm.LLMError, match=provider.key_setting):
        llm.ask_llm("Hello")
    factory.assert_not_called()


@pytest.mark.parametrize("kind", ["text", "json", "json_retry", "missing_usage"])
def test_openrouter_sdk_calls_and_pydantic_validation(monkeypatch, plan, kind):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openrouter")
    requests = []
    replies = [plan.model_dump_json()] if kind.startswith("json") else ["  A reply.  "]
    if kind == "json_retry":
        replies.insert(0, "{}")  # Valid JSON that fails the actual ResearchPlan schema.

    def respond(request):
        requests.append(request)
        content = replies[min(len(requests) - 1, len(replies) - 1)]
        payload = {
            "id": "gen-test", "object": "chat.completion", "created": 1,
            "model": config.OPENROUTER_MODEL_NAME,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": content,
                "reasoning": "Separate reasoning is not the final answer.",
            }}],
        }
        if kind != "missing_usage":
            payload["usage"] = {
                "prompt_tokens": 15, "completion_tokens": 40, "total_tokens": 55,
                "completion_tokens_details": {"reasoning_tokens": 30},
            }
        return httpx.Response(200, json=payload)

    real_openai = llm.OpenAI
    factory = Mock(side_effect=lambda **kwargs: real_openai(
        http_client=httpx.Client(transport=httpx.MockTransport(respond)), **kwargs
    ))
    groq = Mock()
    monkeypatch.setattr(llm, "OpenAI", factory)
    monkeypatch.setattr(llm, "Groq", groq)
    if kind.startswith("json"):
        reply, used = llm.ask_json("Return a research plan as JSON.", ResearchPlan)
        assert isinstance(reply, ResearchPlan) and reply == plan
        assert used == (110 if kind == "json_retry" else 55)
        assert len(requests) == (2 if kind == "json_retry" else 1)
        if kind == "json_retry":
            assert "previous reply was not valid JSON" in json.loads(requests[-1].content)["messages"][0]["content"]
    else:
        assert llm.ask_llm("Hello") == "A reply."
        assert llm.ask_llm_with_usage("Hello") == ("A reply.", 0 if kind == "missing_usage" else 55)

    for request in requests:
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == config.OPENROUTER_MODEL_NAME
        assert body["reasoning"] == {"enabled": True}
        assert "response_format" not in body and "tools" not in body
    assert factory.call_args.kwargs["max_retries"] == 0
    assert factory.call_args.kwargs["timeout"] == config.REQUEST_TIMEOUT
    groq.assert_not_called()


def test_openrouter_sdk_errors_preserve_llm_error(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openrouter")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(401, json={"error": {"message": "Invalid key: test-openrouter-key", "code": 401}})

    real_openai = llm.OpenAI
    monkeypatch.setattr(llm, "OpenAI", lambda **kwargs: real_openai(
        http_client=httpx.Client(transport=httpx.MockTransport(respond)), **kwargs
    ))
    with pytest.raises(llm.LLMError, match="OpenRouter request failed") as failure:
        llm.ask_llm("Hello")
    assert failure.value.tokens_used == 0
    assert "test-openrouter-key" not in str(failure.value)
    assert len(requests) == 1


def test_unknown_provider_is_llm_error(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "unknown")
    with pytest.raises(llm.LLMError, match="LLM_PROVIDER"):
        llm.ask_llm("Hello")
