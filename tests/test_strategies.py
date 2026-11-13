import pytest

from qualitybridge.config import ConfigurationError
from qualitybridge.models import ProjectConfig, SuiteConfig
from qualitybridge.strategies import FullSuite, NamedSuite, select_suite


@pytest.fixture
def config():
    return ProjectConfig(
        project="Projeto de teste",
        runner="pytest",
        suites={
            "full": SuiteConfig(paths=["tests"]),
            "smoke": SuiteConfig(
                paths=["tests/smoke"],
                filter="smoke",
            ),
        },
    )


def test_full_suite_selects_full_suite(config):
    result = FullSuite().select(config)

    assert result is config.suites["full"]


def test_named_suite_selects_requested_suite(config):
    result = NamedSuite("smoke").select(config)

    assert result is config.suites["smoke"]


def test_named_suite_rejects_unknown_name(config):
    with pytest.raises(ConfigurationError, match="Suíte 'inexistente' não definida"):
        NamedSuite("inexistente").select(config)


def test_select_suite_uses_full_strategy(config):
    result = select_suite(config, "full")

    assert result is config.suites["full"]


def test_select_suite_uses_named_strategy(config):
    result = select_suite(config, "smoke")

    assert result is config.suites["smoke"]
