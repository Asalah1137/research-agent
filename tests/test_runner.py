"""Failures retain the latest draft, paid tokens, and a useful final result."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from agent import llm, nodes, runner
from agent.state import Critique, TargetedQueries


@pytest.mark.parametrize("stage, expected_tokens", [
    ("planner_json", 18), ("no_sources", 13), ("critique_json", 46),
    ("rewrite_request", 105), ("targeted_json", 78),
])
def test_failed_runs_preserve_work_and_usage(monkeypatch, source, plan, report, stage, expected_tokens):
    failed = Critique(missing_aspects=["Need measurements."], unsupported_claims=[], passed=False).model_dump_json()
    queries = TargetedQueries(queries=["LangGraph measurements"]).model_dump_json()
    replies = [(plan.model_dump_json(), 10), (report, 20)]
    sources = ([source], 0)
    if stage == "planner_json":
        replies = [("bad JSON", 7), ("still bad", 11)]
    elif stage == "no_sources":
        replies = [(plan.model_dump_json(), 10)]
        sources = ([], 3)
    elif stage == "critique_json":
        replies += [("bad critique", 7), ("still bad", 9)]
    elif stage == "rewrite_request":
        replies += [(failed, 30), (queries, 40), llm.LLMError("Provider unavailable", 5)]
    else:
        replies += [(failed, 30), ("bad queries", 7), ("still bad", 11)]
    request = Mock(side_effect=replies)
    collector = AsyncMock(return_value=sources)
    monkeypatch.setattr(llm, "ask_llm_with_usage", request)
    monkeypatch.setattr(nodes, "collect_sources", collector)
    monkeypatch.setattr(runner, "perf_counter", Mock(side_effect=[100.0, 103.5]))
    result = asyncio.run(runner.run_research("Explain graphs"))

    assert result.status == "limited" and result.limitations
    assert result.total_tokens == expected_tokens and result.elapsed_seconds == 3.5
    assert result.refinement_rounds == 0
    assert request.call_count == len(replies)
    if stage in {"planner_json", "no_sources"}:
        assert "Insufficient evidence" in result.report
        assert collector.call_count == (0 if stage == "planner_json" else 1)
    else:
        assert "Latest draft" in result.report and result.error
        assert result.report.index("Run ended with limitations") < result.report.index("## References")
    if stage == "critique_json":
        assert result.critique is None


def test_token_totals_start_from_zero_for_each_run(monkeypatch, source, plan, report):
    passed = Critique(missing_aspects=[], unsupported_claims=[], passed=True).model_dump_json()

    def reply(prompt):
        if prompt.startswith("Plan a small"):
            return plan.model_dump_json(), 5
        if prompt.startswith("Write an evidence-based"):
            return report, 5
        return passed, 5

    monkeypatch.setattr(llm, "ask_llm_with_usage", Mock(side_effect=reply))
    monkeypatch.setattr(nodes, "collect_sources", AsyncMock(return_value=([source], 0)))
    first = asyncio.run(runner.run_research("First question"))
    second = asyncio.run(runner.run_research("Second question"))
    assert first.status == second.status == "success"
    assert first.total_tokens == second.total_tokens == 15
    assert first.limitations == second.limitations == []
    assert first.elapsed_seconds > 0 and second.elapsed_seconds > 0
