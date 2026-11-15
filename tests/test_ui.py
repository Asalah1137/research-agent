"""Use Streamlit's app tests with a mocked researcher; no browser or API calls."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from agent import config, runner
from agent.state import Critique, ResearchState

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_ui_initial_screen_and_empty_question(monkeypatch):
    research = AsyncMock()
    monkeypatch.setattr(runner, "run_research", research)
    app = AppTest.from_file(APP_PATH).run(timeout=15)
    assert not app.exception and app.info[0].value == "Current node: Idle"
    assert len(app.text_area) == 1 and app.button[0].label == "Research"
    assert app.metric[0].value == "0"
    app.button[0].click().run(timeout=15)
    assert not app.exception and research.call_count == 0
    assert any("Please enter a question" in item.value for item in app.warning)


@pytest.mark.parametrize("outcome", ["success", "limited", "no_sources", "writing_next_report"])
def test_ui_results_progress_and_session_persistence(monkeypatch, source, report, outcome):
    result = ResearchState(
        question="Explain LangGraph", sources=[source], report=report,
        critique=Critique(missing_aspects=[], unsupported_claims=[], passed=True),
        refinement_rounds=1, total_tokens=42, elapsed_seconds=3.5, status="success",
    )
    if outcome in {"limited", "no_sources"}:
        result = result.model_copy(update={"status": "limited", "limitations": ["Missing evidence."]})
    if outcome == "no_sources":
        result = result.model_copy(update={"sources": [], "report": "## Summary\nInsufficient evidence.", "refinement_rounds": 0})

    async def research(question, on_update=None):
        assert question == "Explain LangGraph"
        if outcome == "writing_next_report":
            on_update("generate_research", result.model_copy(update={"refinement_rounds": 0}))
            st.stop()
        on_update("planner", ResearchState(question=question))
        on_update("done", result)
        return result

    mocked = AsyncMock(side_effect=research)
    monkeypatch.setattr(runner, "run_research", mocked)
    app = AppTest.from_file(APP_PATH).run(timeout=15)
    app.text_area[0].set_value("Explain LangGraph")
    app.button[0].click().run(timeout=15)
    assert not app.exception and mocked.call_count == 1
    if outcome == "writing_next_report":
        assert app.info[0].value == "Current node: Generate Research"
        assert app.metric[0].value == "2" and app.metric[1].value == f"0/{config.MAX_REFINEMENT_ROUNDS}"
        return
    assert app.info[0].value == "Current node: Done"
    assert app.metric[0].value == ("0" if outcome == "no_sources" else "2")
    assert app.metric[2].value == "42" and app.metric[3].value == "3.5 s"
    messages = app.success if outcome == "success" else app.warning
    assert any(f"Status: {result.status}" in item.value for item in messages)
    assert any(result.report in item.value for item in app.markdown)
    if outcome == "success":
        assert any(source.title in item.value for item in app.sidebar.markdown)
    else:
        assert any("Missing evidence" in item.value for item in app.markdown)
    assert app.session_state["result"] == result
    app.run(timeout=15)
    assert not app.exception and mocked.call_count == 1
    assert any(result.report in item.value for item in app.markdown)
