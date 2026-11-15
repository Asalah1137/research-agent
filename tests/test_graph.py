"""Exercise the real five-node graph with deterministic LLM and source replies."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from langgraph.graph import END, START

from agent import llm, nodes
from agent.graph import build_graph, route_after_reflection
from agent.runner import run_research
from agent.state import Critique, ResearchState, TargetedQueries


@pytest.mark.parametrize("passed, citation, rounds, sources, error, expected", [
    (True, 1, 0, True, "", "end"),
    (False, 1, 0, True, "", "targeted_research"),
    (None, 1, 0, True, "", "targeted_research"),
    (True, 99, 0, True, "", "targeted_research"),
    (False, 1, 2, True, "", "end"),
    (True, 1, 2, True, "", "end"),
    (False, 1, 0, False, "", "end"),
    (False, 1, 0, True, "Provider failed", "end"),
])
def test_conditional_edge(source, passed, citation, rounds, sources, error, expected):
    critique = None if passed is None else Critique(
        missing_aspects=[] if passed else ["Need measurements."], unsupported_claims=[], passed=passed,
    )
    state = ResearchState(
        question="Explain graphs", report=f"Claim [{citation}].",
        sources=[source] if sources else [], critique=critique,
        refinement_rounds=rounds, error=error,
    )
    assert route_after_reflection(state) == expected


@pytest.mark.parametrize("verdicts, citations, status", [
    ([True], [1], "success"),
    ([False, True], [1, 1], "success"),
    ([False, False, True], [1, 1, 1], "success"),
    ([False, False, False], [1, 1, 1], "limited"),
    ([True, True], [99, 1], "success"),
    ([True, True, True], [99, 99, 99], "limited"),
])
def test_graph_reports_and_fresh_critiques(monkeypatch, source, plan, verdicts, citations, status):
    replies = [plan.model_dump_json()]
    queries = TargetedQueries(queries=["LangGraph measurements"]).model_dump_json()
    for index, (passed, citation) in enumerate(zip(verdicts, citations), start=1):
        if index > 1:
            replies.append(queries)
        replies.append(
            f"## Summary\nReport {index} [{citation}].\n## Findings\nEvidence [{citation}]."
            "\n## Limitations\nA draft.\n## References\n[1] Known source."
        )
        replies.append(Critique(
            missing_aspects=[] if passed else ["Missing measurements."],
            unsupported_claims=[] if passed else ["Unsupported speedup."], passed=passed,
        ).model_dump_json())
    request = Mock(side_effect=[(reply, 10) for reply in replies])
    collector = AsyncMock(return_value=([source], 0))
    monkeypatch.setattr(llm, "ask_llm_with_usage", request)
    monkeypatch.setattr(nodes, "collect_sources", collector)
    events = []
    result = asyncio.run(run_research("Explain graphs", on_update=lambda node, state: events.append((node, state))))

    reports = len(verdicts)
    assert result.refinement_rounds == reports - 1 <= 2
    assert f"Report {reports}" in result.report and result.status == status
    assert result.total_tokens == 10 * len(replies) and request.call_count == len(replies)
    assert collector.call_count == reports
    names = [node for node, _ in events]
    assert names.count("generate_research") == names.count("reflection") == reports
    assert names.count("targeted_research") == reports - 1 and names[-1] == "done"
    assert events[0][1].total_tokens == 0 and events[1][1].total_tokens == 10
    assert events[2][1].sources == [source] and not events[2][1].report
    assert events[-1][1] == result
    if status == "limited":
        assert result.limitations and "Refinement limit reached (2 rounds)" in result.report
        assert result.report.index("Refinement limit reached") < result.report.index("## References")
    else:
        assert not result.limitations and "Refinement limit reached" not in result.report
    assert set(build_graph().get_graph().nodes) - {START, END} == {
        "planner", "search", "generate_research", "reflection", "targeted_research",
    }


def test_critique_issues_override_an_inconsistent_pass(monkeypatch, source, plan, report):
    reply = Critique(missing_aspects=["Need an example."], unsupported_claims=[], passed=True)
    monkeypatch.setattr(llm, "ask_llm_with_usage", Mock(return_value=(reply.model_dump_json(), 7)))
    update = nodes.reflection(ResearchState(question="Explain graphs", plan=plan, sources=[source], report=report))
    assert not update["critique"].passed
    assert update["status"] == "limited" and update["total_tokens"] == 7
