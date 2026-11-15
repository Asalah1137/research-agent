"""Plain-Python checks for numbered report citations."""

import re

from agent.state import Source


def invalid_citations(report: str, sources: list[Source]) -> list[int]:
    """Return citation numbers that do not belong to readable collected sources."""
    cited = {int(number) for number in re.findall(r"\[(\d+)\]", report)}
    read = {
        source.number
        for source in sources
        if source.text.strip() and not source.text.startswith("Error:")
    }
    return sorted(cited - read)
