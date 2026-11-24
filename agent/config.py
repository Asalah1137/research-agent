"""Project settings loaded from .env or environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Find the root .env, regardless of the terminal's current folder.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").strip().lower()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
# Keep Groq's model separate so switching back only needs LLM_PROVIDER=groq.
MODEL_NAME = os.getenv("MODEL_NAME", "openai/gpt-oss-120b")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL_NAME = os.getenv(
    "OPENROUTER_MODEL_NAME", "nvidia/nemotron-3-ultra-550b-a55b:free"
)
MAX_SEARCH_RESULTS = int(os.getenv("MAX_SEARCH_RESULTS", "5"))
MAX_PAGES_READ = int(os.getenv("MAX_PAGES_READ", "5"))
MAX_CHARS_PER_PAGE = int(os.getenv("MAX_CHARS_PER_PAGE", "1000"))
# v1 allows zero, one, or two rewrites, never more than three reports.
MAX_REFINEMENT_ROUNDS = min(2, max(0, int(os.getenv("MAX_REFINEMENT_ROUNDS", "2"))))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "2"))
SEARCH_BACKEND = os.getenv("SEARCH_BACKEND", "auto").strip()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
