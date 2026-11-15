"""Groq calls, simple retries, and token usage for each reply."""

import logging
import time

from groq import APIConnectionError, Groq, GroqError, RateLimitError
from pydantic import BaseModel, ValidationError

from agent import config
from agent.prompts import json_retry_prompt

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Carry tokens already spent when an LLM operation fails."""

    def __init__(self, message: str, tokens_used: int = 0):
        if config.GROQ_API_KEY:
            message = message.replace(config.GROQ_API_KEY, "[redacted]")
        super().__init__(message)
        self.tokens_used = tokens_used


def ask_llm(prompt: str) -> str:
    """Send a prompt to the configured model and return its reply."""
    answer, _ = ask_llm_with_usage(prompt)
    return answer


def ask_llm_with_usage(prompt: str) -> tuple[str, int]:
    """Return the reply and its total prompt/completion token count."""
    if not config.GROQ_API_KEY:
        raise LLMError("Add your Groq API key to GROQ_API_KEY in .env first.")

    retries = max(0, config.MAX_RETRIES)
    try:
        # Disable SDK retries so the retry count is controlled in one place.
        with Groq(api_key=config.GROQ_API_KEY, timeout=config.REQUEST_TIMEOUT, max_retries=0) as client:
            for attempt in range(retries + 1):
                try:
                    response = client.chat.completions.create(
                        model=config.MODEL_NAME, messages=[{"role": "user", "content": prompt}]
                    )
                    break
                except (RateLimitError, APIConnectionError) as exc:
                    if attempt == retries:
                        raise LLMError(f"Groq request failed after {retries} retries: {exc}") from exc
                    delay = 5.0 * (attempt + 1)
                    if isinstance(exc, RateLimitError):
                        try:
                            delay = float(exc.response.headers.get("retry-after", "5"))
                        except ValueError:
                            delay = 5.0
                    delay = max(0.0, min(delay, 60.0))
                    logger.warning("Groq %s; retrying in %.1f seconds.", type(exc).__name__, delay)
                    time.sleep(delay)
    except LLMError:
        raise
    except GroqError as exc:
        raise LLMError(f"Groq request failed: {exc}") from exc
    except Exception as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc

    tokens = int(response.usage.total_tokens or 0) if response.usage else 0
    if not response.choices:
        raise LLMError("Groq returned no reply choices.", tokens)
    answer = response.choices[0].message.content
    if not answer or not answer.strip():
        raise LLMError("Groq returned an empty reply.", tokens)
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
