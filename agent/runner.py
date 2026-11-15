"""Run the graph and return a timed, clearly labelled research result."""

import logging
from collections.abc import Callable
from time import perf_counter

from agent.citations import invalid_citations
from agent.graph import build_graph
from agent.state import ResearchState

logger = logging.getLogger(__name__)


def finish_result(state: ResearchState) -> ResearchState:
    """Set the final status and explain any unmet checks or interrupted run."""
    errors = invalid_citations(state.report, state.sources)
    passed = bool(
        state.report and state.sources and not state.error and not errors
        and state.critique is not None and state.critique.passed
        and not state.critique.missing_aspects and not state.critique.unsupported_claims
    )
    issues = []
    if not state.sources:
        issues.append("Insufficient evidence: no usable sources were read.")
    if state.error:
        issues.append(f"Run stopped: {state.error}")
    if errors:
        labels = ", ".join(f"[{number}]" for number in errors)
        issues.append(f"Citation validity: {labels} do not refer to sources that were read.")
    if state.sources:
        if state.critique is None:
            issues.append("Evidence support and research coverage could not be judged.")
        else:
            issues.extend(f"Research coverage: {aspect}" for aspect in state.critique.missing_aspects)
            issues.extend(f"Evidence support: {claim}" for claim in state.critique.unsupported_claims)
            if not state.critique.passed and not state.critique.missing_aspects and not state.critique.unsupported_claims:
                issues.append("The critique did not pass evidence support and research coverage.")

    report = state.report
    if not report:
        summary = "Insufficient evidence: no usable sources were read." if not state.sources else "A report could not be generated."
        report = "## Summary\n\n" + summary + "\n\n## Limitations\n\n" + "\n".join(f"- {issue}" for issue in issues)
    elif state.error:
        # Preserve the latest draft and place the failure explanation before References.
        body, separator, references = report.partition("## References")
        if "## Limitations" not in body:
            body += "\n\n## Limitations\n"
        note = "**Run ended with limitations.**\n\n" + "\n".join(f"- {issue}" for issue in issues)
        report = body.rstrip() + "\n\n" + note + "\n\n" + separator + references

    return state.model_copy(update={
        "report": report, "citation_errors": errors,
        "status": "success" if passed else "limited", "limitations": [] if passed else issues,
    })


async def run_research(
    question: str, on_update: Callable[[str, ResearchState], None] | None = None
) -> ResearchState:
    """Run one question; token totals start from zero and all waits count as time."""
    started = perf_counter()
    state = ResearchState(question=question)
    try:
        modes = ["updates", "tasks"] if on_update is not None else ["updates"]
        async for mode, event in build_graph().astream(state, stream_mode=modes):
            if mode == "updates":
                for update in event.values():
                    state = state.model_copy(update=update)
            elif mode == "tasks" and "input" in event and on_update is not None:
                # Task-start events show the active node before its result arrives.
                progress = state.model_copy(update={"elapsed_seconds": perf_counter() - started})
                on_update(event["name"], progress)
    except Exception as exc:
        logger.exception("Research run stopped unexpectedly.")
        state = state.model_copy(update={
            "error": str(exc), "total_tokens": state.total_tokens + getattr(exc, "tokens_used", 0),
        })
    state = finish_result(state)
    state = state.model_copy(update={"elapsed_seconds": perf_counter() - started})
    if on_update is not None:
        on_update("done", state)
    return state
