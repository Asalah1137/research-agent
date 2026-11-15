"""The five research node functions, each returning a state update."""

from agent import config, llm
from agent.citations import invalid_citations
from agent.prompts import (
    generate_research_prompt,
    planner_prompt,
    reflection_prompt,
    targeted_research_prompt,
)
from agent.helpers import collect_sources
from agent.state import Critique, ResearchPlan, ResearchState, TargetedQueries


def planner(state: ResearchState) -> dict[str, object]:
    """Turn the question into a scope, subquestions, and search queries."""
    prompt = planner_prompt(state)
    try:
        plan, tokens = llm.ask_json(prompt, ResearchPlan)
    except llm.LLMError as exc:
        return {"error": f"Planner failed: {exc}", "total_tokens": state.total_tokens + exc.tokens_used}
    return {"plan": plan, "total_tokens": state.total_tokens + tokens}


async def search(state: ResearchState) -> dict[str, object]:
    """Search the plan's queries and keep roughly five readable sources."""
    if state.plan is None:
        raise ValueError("Run Planner before Search.")
    sources, tokens = await collect_sources(
        state.plan.queries, state.question, state.sources, config.MAX_PAGES_READ
    )
    return {"sources": sources, "total_tokens": state.total_tokens + tokens}


def generate_research(state: ResearchState) -> dict[str, object]:
    """Write a cited report, or revise the previous report using its critique."""
    if not state.sources:
        return {
            "report": "## Summary\n\nInsufficient evidence: no usable sources were read.\n\n"
            "## Limitations\n\nA sourced report could not be written.",
            "critique": None,
            "citation_errors": [],
            "status": "limited",
            "limitations": ["Insufficient evidence: no usable sources were read."],
        }
    if state.plan is None:
        raise ValueError("Run Planner before Generate Research.")

    prompt = generate_research_prompt(state)
    try:
        report, tokens = llm.ask_llm_with_usage(prompt)
    except llm.LLMError as exc:
        return {"error": f"Generate Research failed: {exc}", "total_tokens": state.total_tokens + exc.tokens_used}
    return {
        "report": report,
        # A previous critique must not be mistaken for a verdict on this new report.
        "critique": None,
        "citation_errors": [],
        "refinement_rounds": state.refinement_rounds + int(bool(state.report)),
        "total_tokens": state.total_tokens + tokens,
        "status": "limited",
        "limitations": [],
    }


def reflection(state: ResearchState) -> dict[str, object]:
    """Judge evidence support and coverage in one LLM call."""
    if not state.report or state.plan is None:
        raise ValueError("Generate a planned report before Reflection.")
    if not state.sources:
        return {
            "critique": Critique(
                missing_aspects=["No usable sources were read."],
                unsupported_claims=[],
                passed=False,
            ),
            "citation_errors": [],
            "status": "limited",
            "limitations": ["Insufficient evidence: no usable sources were read."],
        }

    prompt = reflection_prompt(state)
    try:
        critique, tokens = llm.ask_json(prompt, Critique)
    except llm.LLMError as exc:
        return {"error": f"Reflection failed: {exc}", "total_tokens": state.total_tokens + exc.tokens_used}
    if critique.missing_aspects or critique.unsupported_claims:
        critique.passed = False
    errors = invalid_citations(state.report, state.sources)
    issues = []
    if errors:
        labels = ", ".join(f"[{number}]" for number in errors)
        issues.append(f"Citation validity: {labels} do not refer to sources that were read.")
    issues.extend(f"Research coverage: {aspect}" for aspect in critique.missing_aspects)
    issues.extend(f"Evidence support: {claim}" for claim in critique.unsupported_claims)
    if not issues and not critique.passed:
        issues.append("The critique did not pass evidence support and research coverage.")
    update: dict[str, object] = {
        "critique": critique,
        "citation_errors": errors,
        "total_tokens": state.total_tokens + tokens,
        "status": "success" if critique.passed and not errors else "limited",
        "limitations": issues,
    }

    if state.refinement_rounds >= config.MAX_REFINEMENT_ROUNDS and (errors or not critique.passed):
        # Keep the code-generated stopping note inside Limitations, before References.
        body, separator, references = state.report.partition("## References")
        if "## Limitations" not in body:
            body += "\n\n## Limitations\n"
        note = (
            f"**Refinement limit reached ({config.MAX_REFINEMENT_ROUNDS} rounds).** "
            "The remaining quality checks were not satisfied:\n\n"
            + "\n".join(f"- {issue}" for issue in issues)
        )
        report = body.rstrip() + "\n\n" + note + "\n\n"
        if separator:
            report += separator + references
        update["report"] = report
    return update


async def targeted_research(state: ResearchState) -> dict[str, object]:
    """Turn critique gaps into 1-2 searches and add up to two new sources."""
    if state.critique is None:
        raise ValueError("Run Reflection before Targeted Research.")
    if state.critique.passed and not state.citation_errors:
        return {"sources": list(state.sources)}

    prompt = targeted_research_prompt(state)
    try:
        queries, tokens = llm.ask_json(prompt, TargetedQueries)
    except llm.LLMError as exc:
        return {"error": f"Targeted Research failed: {exc}", "total_tokens": state.total_tokens + exc.tokens_used}
    sources, search_tokens = await collect_sources(
        queries.queries, state.question, state.sources, min(2, config.MAX_PAGES_READ)
    )
    return {"sources": sources, "total_tokens": state.total_tokens + tokens + search_tokens}
