"""A small Streamlit interface for the researcher agent."""

import asyncio

import streamlit as st

from agent import config
from agent.runner import run_research
from agent.state import ResearchState

NODE_LABELS = {
    "idle": "Idle",
    "planner": "Planner",
    "search": "Search",
    "generate_research": "Generate Research",
    "reflection": "Reflection / Critique",
    "targeted_research": "Targeted Research",
    "done": "Done",
}

st.set_page_config(page_title="Researcher Agent", layout="wide")
st.title("Researcher Agent")
st.caption(f"Web research, a cited report, and up to {config.MAX_REFINEMENT_ROUNDS} refinement rounds.")

with st.form("research_form"):
    question = st.text_area("Question", placeholder="What would you like to research?", height=100)
    submitted = st.form_submit_button("Research")

node_box = st.empty()
metrics_box = st.empty()
status_box = st.empty()
with st.sidebar:
    st.header("Sources")
    sources_box = st.empty()
st.subheader("Final report")
report_box = st.empty()
limitations_box = st.empty()


def show_progress(node: str, state: ResearchState) -> None:
    """Refresh the active node, counters, and numbered source list."""
    node_box.info(f"Current node: {NODE_LABELS[node]}")
    reports = state.refinement_rounds + 1 if state.report and state.sources else 0
    if node == "generate_research" and state.sources:
        reports += 1
    with metrics_box.container():
        report, rounds, tokens, elapsed = st.columns(4)
        report.metric("Report number", reports)
        rounds.metric("Refinements used", f"{state.refinement_rounds}/{config.MAX_REFINEMENT_ROUNDS}")
        tokens.metric("Total tokens", f"{state.total_tokens:,}")
        elapsed.metric("Elapsed time", f"{state.elapsed_seconds:.1f} s")
    with sources_box.container():
        if not state.sources:
            st.caption("No pages have been read yet.")
        for source in state.sources:
            st.markdown(f"**[{source.number}] {source.title}**")
            st.caption(source.url)


saved = st.session_state.get("result")
# A display-only state gives the initial screen zeroed counters.
show_progress("done" if saved is not None else "idle", saved or ResearchState(question="Waiting"))

if submitted:
    if not question.strip():
        st.warning("Please enter a question.")
    else:
        st.session_state["result"] = None
        report_box.empty()
        limitations_box.empty()
        status_box.info("Status: running")
        result = asyncio.run(run_research(question.strip(), on_update=show_progress))
        st.session_state["result"] = result

result = st.session_state.get("result")
if result is not None:
    show_progress("done", result)
    if result.status == "success":
        status_box.success("Status: success — all three quality checks passed.")
    else:
        status_box.warning("Status: limited — see the remaining limitations below.")
    report_box.markdown(result.report)
    with limitations_box.container():
        with st.expander("Limitations", expanded=bool(result.limitations)):
            if result.limitations:
                for limitation in result.limitations:
                    st.write(f"- {limitation}")
            else:
                st.write("No unresolved quality-check issues.")
