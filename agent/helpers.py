"""Shared web collection steps for Search and Targeted Research."""

import logging
import re

from agent import config, llm
from agent.prompts import rephrase_query_prompt
from agent.state import Source
from mcp_service.client import call_tool, connect_mcp

logger = logging.getLogger(__name__)


async def collect_sources(
    queries: list[str], question: str, existing: list[Source], max_new_pages: int
) -> tuple[list[Source], int]:
    """Read relevant new pages; keep existing sources and their numbers."""
    sources = list(existing)
    known_urls = {source.url for source in sources}
    candidates: dict[str, dict[str, str]] = {}
    next_number = max((source.number for source in sources), default=0) + 1
    tokens = 0

    try:
        async with connect_mcp() as session:
            for query in queries:
                results = await call_tool(
                    session, "search_web", {"query": query, "max_results": config.MAX_SEARCH_RESULTS}
                )
                empty = not results or (
                    isinstance(results, dict) and "no results" in results.get("error", "").lower()
                )
                if empty:
                    logger.warning("Empty search %r; rephrasing and retrying once.", query)
                    try:
                        query, used = llm.ask_llm_with_usage(rephrase_query_prompt(query, question))
                        tokens += used
                    except llm.LLMError as exc:
                        tokens += exc.tokens_used
                        logger.warning("Could not rephrase search: %s", exc)
                        continue
                    results = await call_tool(
                        session, "search_web", {"query": query, "max_results": config.MAX_SEARCH_RESULTS}
                    )
                if isinstance(results, dict):
                    logger.warning("Skipping search %r: %s", query, results["error"])
                    continue
                if not results:
                    logger.warning("Search %r still has no results; continuing.", query)
                for result in results:
                    if result["url"] not in known_urls:
                        candidates.setdefault(result["url"], result)

            # A small keyword-overlap score keeps page selection simple.
            keywords = {word for word in re.findall(r"\w+", question.lower()) if len(word) > 3}
            ranked = sorted(
                candidates.values(),
                key=lambda page: len(
                    keywords & set(re.findall(r"\w+", f"{page['title']} {page['snippet']}".lower()))
                ),
                reverse=True,
            )

            added = 0
            for page in ranked:
                if added >= max_new_pages:
                    break
                text = await call_tool(session, "read_page", {"url": page["url"]})
                if isinstance(text, dict):
                    logger.warning("Skipping page %s: %s", page["url"], text["error"])
                    continue
                if not text.strip() or text.startswith("Error:"):
                    logger.warning("Skipping page %s: %s", page["url"], text or "Empty page")
                    continue

                sources.append(
                    Source(number=next_number, title=page["title"], url=page["url"], text=text)
                )
                next_number += 1
                added += 1
    except Exception as exc:
        logger.warning("Web collection failed: %s", exc)

    return sources, tokens
