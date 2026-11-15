"""The CLI forwards one question and prints the existing research result."""

import sys
from unittest.mock import AsyncMock

import pytest

import run_cli
from agent.state import Critique, ResearchState


def test_cli_question_and_output(monkeypatch, capsys, source, report):
    result = ResearchState(
        question="test question", sources=[source], report=report, status="success",
        critique=Critique(missing_aspects=[], unsupported_claims=[], passed=True),
        total_tokens=123, elapsed_seconds=2.25,
    )
    research = AsyncMock(return_value=result)
    monkeypatch.setattr(run_cli, "run_research", research)
    monkeypatch.setattr(sys, "argv", ["run_cli.py", "test question"])
    run_cli.main()
    research.assert_awaited_once_with("test question")
    output = capsys.readouterr().out
    assert report in output
    assert "Status: success" in output and "Elapsed time: 2.25 seconds" in output
    assert "Total tokens: 123" in output


def test_cli_requires_a_question(monkeypatch):
    research = AsyncMock()
    monkeypatch.setattr(run_cli, "run_research", research)
    monkeypatch.setattr(sys, "argv", ["run_cli.py"])
    with pytest.raises(SystemExit) as exit_status:
        run_cli.main()
    assert exit_status.value.code == 2
    research.assert_not_called()
