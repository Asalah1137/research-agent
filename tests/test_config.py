"""Check defaults and environment overrides without loading the local .env."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent import config

SETTINGS = [
    "GROQ_API_KEY", "MODEL_NAME", "MAX_SEARCH_RESULTS", "MAX_PAGES_READ",
    "MAX_CHARS_PER_PAGE", "MAX_REFINEMENT_ROUNDS", "REQUEST_TIMEOUT", "MAX_RETRIES",
]


def load_settings(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    spec = importlib.util.spec_from_file_location("isolated_config", Path(config.__file__))
    settings = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(settings)
    return settings


def test_config_defaults(monkeypatch):
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    settings = load_settings(monkeypatch)
    assert settings.GROQ_API_KEY == ""
    assert settings.MODEL_NAME == "openai/gpt-oss-120b"
    assert (settings.MAX_SEARCH_RESULTS, settings.MAX_PAGES_READ) == (5, 5)
    assert settings.MAX_CHARS_PER_PAGE == 1000
    assert settings.MAX_REFINEMENT_ROUNDS == 2
    assert (settings.REQUEST_TIMEOUT, settings.MAX_RETRIES) == (30, 2)


def test_config_loads_env_from_project_root(monkeypatch):
    load_env = Mock(return_value=False)
    monkeypatch.setattr("dotenv.load_dotenv", load_env)
    spec = importlib.util.spec_from_file_location("config_path_check", Path(config.__file__))
    settings = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(settings)
    load_env.assert_called_once_with(Path(config.__file__).resolve().parents[1] / ".env")


@pytest.mark.parametrize("rounds, expected", [("-1", 0), ("0", 0), ("1", 1), ("99", 2)])
def test_config_environment_and_round_cap(monkeypatch, rounds, expected):
    monkeypatch.setenv("MODEL_NAME", "custom-model")
    monkeypatch.setenv("MAX_CHARS_PER_PAGE", "2500")
    monkeypatch.setenv("REQUEST_TIMEOUT", "12.5")
    monkeypatch.setenv("MAX_RETRIES", "1")
    monkeypatch.setenv("MAX_REFINEMENT_ROUNDS", rounds)
    settings = load_settings(monkeypatch)
    assert settings.MODEL_NAME == "custom-model"
    assert settings.MAX_CHARS_PER_PAGE == 2500
    assert settings.REQUEST_TIMEOUT == 12.5 and settings.MAX_RETRIES == 1
    assert settings.MAX_REFINEMENT_ROUNDS == expected
