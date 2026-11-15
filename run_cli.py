"""Run one research question from the terminal."""

import argparse
import asyncio
import sys

from agent.runner import run_research


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a cited report for one research question.")
    parser.add_argument("question", help="Research question, enclosed in quotes.")
    args = parser.parse_args()
    result = asyncio.run(run_research(args.question))
    print(result.report)
    print(f"\nStatus: {result.status}")
    print(f"Elapsed time: {result.elapsed_seconds:.2f} seconds")
    print(f"Total tokens: {result.total_tokens}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
