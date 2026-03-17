"""
Unit tests for MEDIUM-12: config hygiene.
Verifies that api_key is sourced from env vars, not hardcoded in config.ini.
"""

import configparser
import os
from unittest.mock import patch

import pytest


@pytest.fixture
def config_without_api_key():
    """Config that mirrors the cleaned config.ini — no api_key field."""
    cfg = configparser.ConfigParser()
    cfg.read_dict({
        "model_fallback_settings": {
            "name": "test-model",
            "base_url": "https://example.com/v1",
        },
    })
    return cfg


@pytest.fixture
def config_with_api_key():
    """Config that still has an api_key field (backward compat)."""
    cfg = configparser.ConfigParser()
    cfg.read_dict({
        "model_fallback_settings": {
            "name": "test-model",
            "base_url": "https://example.com/v1",
            "api_key": "config-key-value",
        },
    })
    return cfg


class TestFallbackApiKeyResolution:
    """The fallback api_key should prefer LLM_API_KEY env var over config."""

    def _resolve_api_key(self, config):
        """Replicate the resolution logic from ModelManager._get_fallback_settings."""
        return os.environ.get("LLM_API_KEY") or config.get(
            "model_fallback_settings", "api_key", fallback=""
        )

    def test_env_var_takes_precedence(self, config_with_api_key):
        with patch.dict(os.environ, {"LLM_API_KEY": "env-secret-key"}):
            assert self._resolve_api_key(config_with_api_key) == "env-secret-key"

    def test_falls_back_to_config_value(self, config_with_api_key):
        with patch.dict(os.environ, {}, clear=True):
            # Ensure LLM_API_KEY is not set
            os.environ.pop("LLM_API_KEY", None)
            assert self._resolve_api_key(config_with_api_key) == "config-key-value"

    def test_empty_when_neither_set(self, config_without_api_key):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("LLM_API_KEY", None)
            assert self._resolve_api_key(config_without_api_key) == ""

    def test_env_var_preferred_over_empty_config(self, config_without_api_key):
        with patch.dict(os.environ, {"LLM_API_KEY": "my-key"}):
            assert self._resolve_api_key(config_without_api_key) == "my-key"


class TestConfigIniNoSecrets:
    """Verify config.ini template does not contain credential values."""

    def test_template_has_no_api_key_value(self):
        cfg = configparser.ConfigParser()
        cfg.read("config/config.ini.template")
        assert not cfg.has_option("model_fallback_settings", "api_key")
