"""The five-node LangGraph and its plain-Python stopping decision."""

from typing import Literal

from langgraph.graph import END, START, StateGraph

from agent import config
from agent.citations import invalid_citations
from agent.nodes import generate_research, planner, reflection, search, targeted_research
from agent.state import ResearchState


def route_after_reflection(state: ResearchState) -> Literal["end", "targeted_research"]:
    """Stop on success, insufficient evidence, or the refinement limit."""
    if state.error or not state.sources:
        return "end"
    if (
        state.critique is not None
        and state.critique.passed
        and not invalid_citations(state.report, state.sources)
    ):
        return "end"
    if state.refinement_rounds >= config.MAX_REFINEMENT_ROUNDS:
        return "end"
    return "targeted_research"


def build_graph():
    """Compile exactly five nodes; each generated report gets a fresh critique."""
    graph = StateGraph(ResearchState)
    graph.add_node("planner", planner)
    graph.add_node("search", search)
    graph.add_node("generate_research", generate_research)
    graph.add_node("reflection", reflection)
    graph.add_node("targeted_research", targeted_research)

    graph.add_edge(START, "planner")
    graph.add_conditional_edges(
        "planner", lambda state: "end" if state.error else "search", {"end": END, "search": "search"}
    )
    graph.add_edge("search", "generate_research")
    graph.add_conditional_edges(
        "generate_research", lambda state: "end" if state.error else "reflection",
        {"end": END, "reflection": "reflection"},
    )
    graph.add_conditional_edges(
        "reflection", route_after_reflection, {"end": END, "targeted_research": "targeted_research"}
    )
    graph.add_conditional_edges(
        "targeted_research", lambda state: "end" if state.error else "generate_research",
        {"end": END, "generate_research": "generate_research"},
    )
    return graph.compile()
