"""Citation validity depends on numbered pages that were actually readable."""

import pytest

from agent.citations import invalid_citations
from agent.state import Source


@pytest.mark.parametrize("text, expected", [
    ("Claim [1].\n## References\n[1] Known source.", []),
    ("Claim [1] and unknown [9], repeated [9].", [9]),
    ("Unknown [4], [0], and [3].", [0, 3, 4]),
])
def test_citation_numbers(source, text, expected):
    assert invalid_citations(text, [source]) == expected


@pytest.mark.parametrize("text", [" ", "Error: page failed"])
def test_unreadable_sources_do_not_validate_citations(source, text):
    unread = Source(number=2, title="Unread page", url="https://example.org/unread", text=text)
    assert invalid_citations("Claim [1] and [2].", [source, unread]) == [2]
    assert invalid_citations("Claim [1].", []) == [1]
