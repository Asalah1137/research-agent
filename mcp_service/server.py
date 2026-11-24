"""Two web research tools served through the official MCP SDK."""

import logging
import re
import time
from urllib.parse import urlparse

import httpx
from ddgs import DDGS
from ddgs.exceptions import RatelimitException, TimeoutException
from mcp.server import MCPServer
from trafilatura import extract

from agent import config

server = MCPServer("research-web", log_level=config.LOG_LEVEL)
logger = logging.getLogger(__name__)


def _search_reason(exc: Exception) -> str:
    message = str(exc).strip().lower()
    if isinstance(exc, RatelimitException) or any(word in message for word in ["429", "rate limit", "ratelimit"]):
        return "rate_limited"
    if isinstance(exc, TimeoutException) or "timed out" in message or "timeout" in message:
        return "timeout"
    if message.rstrip(".") == "no results found":
        return "no_results_unknown"
    return "backend_error"


def _retry_delay(headers, attempt: int) -> float:
    try:
        seconds = float(headers.get("retry-after", 5 * (attempt + 1)))
    except ValueError:
        seconds = 5.0
    return max(0.0, min(seconds, 60.0))


def _page_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return " ".join(match.group(1).split())[:160] if match else ""


@server.tool()
def search_web(query: str, max_results: int) -> list[dict[str, str]] | dict[str, str]:
    """Search the web and return titles, URLs, and snippets, or an error field."""
    try:
        query = query.strip()
        if not query:
            logger.warning("stage=validation.failed reason=invalid_query")
            return {"error": "Search query cannot be empty.", "stage": "validation", "reason": "invalid_query"}
        if max_results < 1:
            logger.warning("stage=validation.failed reason=invalid_count query=%r", query)
            return {"error": "max_results must be at least 1.", "stage": "validation", "reason": "invalid_count"}

        limit = min(max_results, config.MAX_SEARCH_RESULTS)
        retries = max(0, config.MAX_RETRIES)
        for attempt in range(retries + 1):
            logger.info("stage=search.request query=%r backend=%s attempt=%d", query, config.SEARCH_BACKEND, attempt + 1)
            try:
                with DDGS(timeout=config.REQUEST_TIMEOUT) as search:
                    results = search.text(query, max_results=limit, backend=config.SEARCH_BACKEND)
                break
            except Exception as exc:
                if _search_reason(exc) != "rate_limited" or attempt == retries:
                    raise
                delay = 5 * (attempt + 1)
                logger.warning("stage=search.retry reason=rate_limited query=%r delay=%s", query, delay)
                time.sleep(delay)

        # DDGS calls its URL and snippet fields "href" and "body".
        parsed = [
            {
                "title": str(item.get("title") or ""),
                "url": str(item["href"]),
                "snippet": str(item.get("body") or ""),
            }
            for item in results
            if item.get("href")
        ][:limit]
        if results and not parsed:
            logger.warning("stage=search.parse reason=invalid_results query=%r raw_count=%d", query, len(results))
            return {"error": "Search results contained no usable URL fields.", "stage": "search", "reason": "invalid_results"}
        logger.info("stage=search.%s query=%r results=%d", "success" if parsed else "empty", query, len(parsed))
        return parsed
    except Exception as exc:
        reason = _search_reason(exc)
        logger.warning("stage=search.failed reason=%s query=%r error_type=%s message=%s", reason, query, type(exc).__name__, exc)
        if reason == "no_results_unknown":
            message = "DDGS returned no usable results; it did not identify whether there were no matches or a backend/parser failed."
        else:
            message = f"Search failed: {exc}"
        return {"error": message, "stage": "search", "reason": reason}


@server.tool()
def read_page(url: str) -> str:
    """Read an HTML page and return cleaned, truncated text or an Error: message."""
    stage = "validation"
    try:
        address = urlparse(url)
        if address.scheme not in {"http", "https"} or not address.netloc:
            logger.warning("stage=validation.failed url=%r reason=invalid_url", url)
            return "Error: [validation] URL must be a valid HTTP or HTTPS address."

        stage = "fetch"
        headers = {"User-Agent": "ResearcherAgent/0.1", "Accept": "text/html,application/xhtml+xml"}
        with httpx.Client(timeout=config.REQUEST_TIMEOUT, follow_redirects=True, headers=headers) as client:
            retries = max(0, config.MAX_RETRIES)
            for attempt in range(retries + 1):
                logger.info("stage=fetch.request url=%s attempt=%d", url, attempt + 1)
                try:
                    response = client.get(url)
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    logger.warning("stage=fetch.network_error url=%s error_type=%s message=%s", url, type(exc).__name__, exc)
                    if attempt == retries:
                        raise
                    delay = _retry_delay({}, attempt)
                    logger.warning("stage=fetch.retry reason=network url=%s delay=%.1f", url, delay)
                    time.sleep(delay)
                    continue
                logger.info("stage=fetch.response url=%s status=%d content_type=%s bytes=%d", response.url, response.status_code, response.headers.get("content-type", "unknown"), len(response.content))
                if response.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                    delay = _retry_delay(response.headers, attempt)
                    logger.warning("stage=fetch.retry status=%d url=%s delay=%.1f", response.status_code, response.url, delay)
                    time.sleep(delay)
                    continue
                if response.is_error:
                    hint = _page_title(response.text)
                    if response.headers.get("content-type", "").startswith("text/plain"):
                        hint = " ".join(response.text.split())[:200]
                    logger.warning("stage=fetch.rejected url=%s status=%d server=%s hint=%r", response.url, response.status_code, response.headers.get("server", "unknown"), hint)
                response.raise_for_status()
                break

        stage = "content_type"
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type not in {"text/html", "application/xhtml+xml"}:
            looks_html = re.match(r"\s*(?:<!doctype\s+html|<html\b)", response.text, re.IGNORECASE)
            if content_type or not looks_html:
                logger.warning("stage=content_type.rejected url=%s content_type=%s", response.url, content_type or "unknown")
                return f"Error: [content_type] Only HTML pages are supported (received {content_type or 'unknown'})."
            logger.info("stage=content_type.sniffed_html url=%s", response.url)

        stage = "page_check"
        title = _page_title(response.text)
        blocked_titles = {"just a moment", "access denied", "attention required! | cloudflare", "robot check"}
        if response.headers.get("cf-mitigated") == "challenge" or title.lower().rstrip(".!") in blocked_titles:
            logger.warning("stage=page_check.blocked url=%s status=%d title=%r", response.url, response.status_code, title)
            return "Error: [page_check] The response is an access/bot challenge, not readable page content."

        stage = "extract"
        logger.info("stage=extract.start url=%s title=%r", response.url, title)
        text = extract(response.content, url=str(response.url), include_comments=False, include_tables=True)
        strategy = "default"
        if not text or not text.strip():
            strategy = "favor_recall"
            logger.info("stage=extract.retry strategy=favor_recall url=%s", response.url)
            text = extract(response.content, url=str(response.url), include_comments=False, include_tables=True, favor_recall=True)
        if not text or not text.strip():
            logger.warning("stage=extract.empty url=%s title=%r bytes=%d", response.url, title, len(response.content))
            return "Error: [extract] HTML was retrieved, but no readable main text was found; it may be sparse or JavaScript-rendered."
        text = text.strip()
        returned = text[:config.MAX_CHARS_PER_PAGE]
        logger.info("stage=extract.success url=%s strategy=%s extracted_chars=%d returned_chars=%d truncated=%s", response.url, strategy, len(text), len(returned), len(text) > len(returned))
        return returned
    except Exception as exc:
        logger.warning("stage=%s.failed url=%s error_type=%s message=%s", stage, url, type(exc).__name__, exc)
        return f"Error: [{stage}] Could not read page: {exc}"


if __name__ == "__main__":
    # stdout belongs to MCP messages; logging goes to stderr.
    server.run(transport="stdio")
