"""Configured provider calls, simple retries, and token usage for each reply."""

import logging
import time

from groq import APIConnectionError, Groq, GroqError, RateLimitError
from openai import APIConnectionError as OpenAIConnectionError
from openai import OpenAI, OpenAIError
from openai import RateLimitError as OpenAIRateLimitError
from pydantic import BaseModel, ValidationError

from agent import config
from agent.prompts import json_retry_prompt

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Carry tokens already spent when an LLM operation fails."""

    def __init__(self, message: str, tokens_used: int = 0):
        for api_key in (config.GROQ_API_KEY, config.OPENROUTER_API_KEY):
            if api_key:
                message = message.replace(api_key, "[redacted]")
        super().__init__(message)
        self.tokens_used = tokens_used


def ask_llm(prompt: str) -> str:
    """Send a prompt to the configured model and return its reply."""
    answer, _ = ask_llm_with_usage(prompt)
    return answer


def ask_llm_with_usage(prompt: str) -> tuple[str, int]:
    """Return the reply and its total prompt/completion token count."""
    provider = config.LLM_PROVIDER
    client_options = {}
    request_options = {}
    if provider == "groq":
        provider_name, api_key, model_name = "Groq", config.GROQ_API_KEY, config.MODEL_NAME
        client_type = Groq
    elif provider == "openrouter":
        provider_name, api_key, model_name = "OpenRouter", config.OPENROUTER_API_KEY, config.OPENROUTER_MODEL_NAME
        client_type = OpenAI
        client_options["base_url"] = "https://openrouter.ai/api/v1"
        request_options["extra_body"] = {"reasoning": {"enabled": True}}
    else:
        raise LLMError("Set LLM_PROVIDER to groq or openrouter in .env.")
    if not api_key:
        raise LLMError(f"Add your {provider_name} API key to {provider.upper()}_API_KEY in .env first.")

    retries = max(0, config.MAX_RETRIES)
    try:
        # Disable SDK retries so the retry count is controlled in one place.
        with client_type(api_key=api_key, timeout=config.REQUEST_TIMEOUT, max_retries=0, **client_options) as client:
            for attempt in range(retries + 1):
                try:
                    response = client.chat.completions.create(
                        model=model_name, messages=[{"role": "user", "content": prompt}], **request_options
                    )
                    break
                except (RateLimitError, APIConnectionError, OpenAIRateLimitError, OpenAIConnectionError) as exc:
                    if attempt == retries:
                        raise LLMError(f"{provider_name} request failed after {retries} retries: {exc}") from exc
                    delay = 5.0 * (attempt + 1)
                    if isinstance(exc, (RateLimitError, OpenAIRateLimitError)):
                        try:
                            delay = float(exc.response.headers.get("retry-after", "5"))
                        except ValueError:
                            delay = 5.0
                    delay = max(0.0, min(delay, 60.0))
                    logger.warning("%s %s; retrying in %.1f seconds.", provider_name, type(exc).__name__, delay)
                    time.sleep(delay)
    except LLMError:
        raise
    except (GroqError, OpenAIError) as exc:
        raise LLMError(f"{provider_name} request failed: {exc}") from exc
    except Exception as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc

    tokens = int(response.usage.total_tokens or 0) if response.usage else 0
    if not response.choices:
        raise LLMError(f"{provider_name} returned no reply choices.", tokens)
    answer = response.choices[0].message.content
    if not answer or not answer.strip():
        raise LLMError(f"{provider_name} returned an empty reply.", tokens)
    return answer.strip(), tokens


def ask_json(prompt: str, model: type[BaseModel]) -> tuple[BaseModel, int]:
    """Validate the JSON reply, retrying malformed JSON exactly once."""
    tokens = 0
    current_prompt = prompt
    for attempt in range(2):
        try:
            reply, used = ask_llm_with_usage(current_prompt)
        except LLMError as exc:
            raise LLMError(str(exc), tokens + exc.tokens_used) from exc
        tokens += used
        try:
            return model.model_validate_json(reply), tokens
        except ValidationError as exc:
            if attempt == 1:
                raise LLMError(f"Invalid {model.__name__} JSON after one retry.", tokens) from exc
            logger.warning("Invalid %s JSON; retrying once.", model.__name__)
            current_prompt = json_retry_prompt(prompt)
