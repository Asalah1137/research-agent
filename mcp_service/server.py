"""Two web research tools served through the official MCP SDK."""

import logging
import time
from urllib.parse import urlparse

import httpx
from ddgs import DDGS
from ddgs.exceptions import RatelimitException
from mcp.server import MCPServer
from trafilatura import extract

from agent import config

server = MCPServer("research-web", log_level="WARNING")
logger = logging.getLogger(__name__)


@server.tool()
def search_web(query: str, max_results: int) -> list[dict[str, str]] | dict[str, str]:
    """Search the web and return titles, URLs, and snippets, or an error field."""
    try:
        query = query.strip()
        if not query:
            return {"error": "Search query cannot be empty."}
        if max_results < 1:
            return {"error": "max_results must be at least 1."}

        limit = min(max_results, config.MAX_SEARCH_RESULTS)
        retries = max(0, config.MAX_RETRIES)
        for attempt in range(retries + 1):
            try:
                with DDGS(timeout=config.REQUEST_TIMEOUT) as search:
                    results = search.text(query, max_results=limit)
                break
            except Exception as exc:
                message = str(exc).lower()
                rate_limited = isinstance(exc, RatelimitException) or any(
                    marker in message for marker in ["429", "rate limit", "ratelimit"]
                )
                if not rate_limited or attempt == retries:
                    raise
                delay = 5 * (attempt + 1)
                logger.warning("Search rate limited; retrying in %s seconds.", delay)
                time.sleep(delay)

        # DDGS calls its URL and snippet fields "href" and "body".
        return [
            {
                "title": str(item.get("title") or ""),
                "url": str(item["href"]),
                "snippet": str(item.get("body") or ""),
            }
            for item in results
            if item.get("href")
        ][:limit]
    except Exception as exc:
        if str(exc).strip().lower().rstrip(".") == "no results found":
            return []
        logger.warning("Search failed for %r: %s", query, exc)
        return {"error": f"Search failed: {exc}"}


@server.tool()
def read_page(url: str) -> str:
    """Read an HTML page and return cleaned, truncated text or an Error: message."""
    try:
        address = urlparse(url)
        if address.scheme not in {"http", "https"} or not address.netloc:
            return "Error: URL must be a valid HTTP or HTTPS address."

        with httpx.Client(timeout=config.REQUEST_TIMEOUT, follow_redirects=True) as client:
            retries = max(0, config.MAX_RETRIES)
            for attempt in range(retries + 1):
                response = client.get(url)
                if response.status_code != 429 or attempt == retries:
                    break
                try:
                    delay = float(response.headers.get("retry-after", "5"))
                except ValueError:
                    delay = 5.0
                delay = max(0.0, min(delay, 60.0))
                logger.warning("Page rate limited; retrying in %.1f seconds.", delay)
                time.sleep(delay)
            response.raise_for_status()

        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type not in {"text/html", "application/xhtml+xml"}:
            return f"Error: Only HTML pages are supported (received {content_type or 'unknown'})."

        text = extract(response.text, include_comments=False, include_tables=True)
        if not text or not text.strip():
            return "Error: No readable text found on this page."
        return text.strip()[:config.MAX_CHARS_PER_PAGE]
    except Exception as exc:
        logger.warning("Could not read %s: %s", url, exc)
        return f"Error: Could not read page: {exc}"


if __name__ == "__main__":
    # stdout belongs to MCP messages; logging goes to stderr.
    server.run(transport="stdio")
